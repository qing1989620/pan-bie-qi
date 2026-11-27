"""Run local R3 v3 weights on the released corpus and report comparable metrics."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import time
from pathlib import Path

import numpy as np
import torch
from accelerate import init_empty_weights
from accelerate.utils import set_module_tensor_to_device
from transformers import (
    AttentionInterface,
    AutoConfig,
    AutoModel,
    AutoModelForCausalLM,
    AutoTokenizer,
)
from transformers.integrations.sdpa_attention import sdpa_attention_forward
from transformers.masking_utils import ALL_MASK_ATTENTION_FUNCTIONS

REVISIONS = {
    "embedding": ("9df7e112330d858f4fd0a9d1aa5e1def15d5d25a",
                  "832c0224b941c586af58bdb9b14a6b597ed15464055d1f7338cca172b51065c4"),
    "rerank": ("6d0c3e486b8653578fd3f0e3c8e7871a0ce01953",
               "bd2594bfbffaaff0e5cab6283cc32025cac5d5397262fd73ecd109b69cddeb92"),
}
DATA_REVISION = "57d9285969cc293832c71ecbe42a75ac416bef93"
DATA_DIGESTS = {
    "skills_test.jsonl": "b80f88aa0d3826fb6456eb45f84be12e4ca6df23d60a1148ac92854a0ce2a6a5",
    "test.jsonl": "f1f241303379d313ca7904460c4395b3e80143f5b3ee4a1e0f4056135e30a947",
    "reproduce.py": "00c094970bacc79f265b606cf800fbec4c44d6aad1b149a9b68b15da3495f44d",
}
ATTENTION_LIMIT = 4096
EMBED_QUERY = "Instruct: Given a user request, retrieve the agent skill that solves it.\nQuery: "
RERANK_TASK = "Given a user request, retrieve the agent skill that solves it."


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verified_weights(directory: Path, stage: str):
    revision, expected = REVISIONS[stage]
    if checksum(directory / "model.safetensors") != expected:
        raise ValueError(f"{stage} SHA-256 differs from the official release")
    return {"revision": revision, "sha256": expected}


def chunked_sdpa(module, query, key, value, attention_mask, **kwargs):
    """Evaluate the same causal attention with a bounded number of query rows."""
    if query.size(-2) <= 512:
        return sdpa_attention_forward(module, query, key, value, attention_mask, **kwargs)
    positions = torch.arange(key.size(-2), device=query.device)
    if attention_mask is None:
        attention_mask = positions[None, :] <= positions[:, None]
        attention_mask = attention_mask[None, None, :, :]
    blocks = []
    for start in range(0, query.size(-2), 512):
        stop = start + 512
        output, _ = sdpa_attention_forward(
            module, query[:, :, start:stop], key, value, attention_mask[..., start:stop, :],
            **{**kwargs, "is_causal": False},
        )
        blocks.append(output)
    return torch.cat(blocks, dim=1), None


AttentionInterface.register("pt_chunked_sdpa", chunked_sdpa)
ALL_MASK_ATTENTION_FUNCTIONS.register("pt_chunked_sdpa", ALL_MASK_ATTENTION_FUNCTIONS["sdpa"])


def load_model(directory, stage, dtype, device):
    loader = AutoModel if stage == "embedding" else AutoModelForCausalLM
    options = {"local_files_only": True, "trust_remote_code": False, "dtype": dtype,
               "attn_implementation": "pt_chunked_sdpa"}
    try:
        return loader.from_pretrained(directory, **options, use_safetensors=True, device_map={"": device})
    except OSError as exc:
        if "os error 1455" not in str(exc):
            raise
    # Windows can deny mapping the F32 checkpoint twice because of its page-file limit.
    # Stream the already verified weights one tensor at a time into the same architecture.
    print(f"{stage}: loading verified weights sequentially", flush=True)
    config = AutoConfig.from_pretrained(directory, local_files_only=True, trust_remote_code=False)
    with init_empty_weights():
        model = loader.from_config(config, dtype=dtype, attn_implementation="pt_chunked_sdpa")
    remaining = set(model.state_dict())
    with (directory / "model.safetensors").open("rb") as stream:
        size = int.from_bytes(stream.read(8), "little")
        header = json.loads(stream.read(size))
        for name, record in header.items():
            if name == "__metadata__":
                continue
            start, end = record["data_offsets"]
            if name not in remaining or record["dtype"] != "F32":
                raise ValueError(f"Unexpected released tensor: {name}")
            raw = bytearray(end - start)
            stream.seek(8 + size + start)
            if stream.readinto(raw) != len(raw):
                raise OSError(f"Incomplete released tensor: {name}")
            tensor = torch.frombuffer(raw, dtype=torch.float32).reshape(record["shape"])
            set_module_tensor_to_device(model, name, device, value=tensor, dtype=dtype)
            remaining.remove(name)
            del tensor, raw
    if remaining - ({"lm_head.weight"} if stage == "rerank" and config.tie_word_embeddings else set()):
        raise ValueError(f"Missing released tensors: {sorted(remaining)}")
    model.tie_weights()
    return model.to(device).eval()


def load_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def ranked_batches(tokenizer, texts, max_batch, token_budget):
    encoded = tokenizer(texts, add_special_tokens=True, truncation=True, max_length=ATTENTION_LIMIT)["input_ids"]
    order = sorted(range(len(texts)), key=lambda index: len(encoded[index]), reverse=True)
    start = 0
    while start < len(order):
        longest = len(encoded[order[start]])
        count = min(max_batch, max(1, token_budget // longest))
        indices = order[start:start + count]
        yield indices, tokenizer.pad({"input_ids": [encoded[i] for i in indices]}, padding=True, return_tensors="pt")
        start += count


@torch.inference_mode()
def encode(model, tokenizer, texts, max_batch, token_budget):
    rows = np.empty((len(texts), model.config.hidden_size), dtype=np.float32)
    processed = 0
    for indices, inputs in ranked_batches(tokenizer, texts, max_batch, token_budget):
        features = model(**inputs.to(model.device), use_cache=False).last_hidden_state[:, -1].float()
        rows[indices] = torch.nn.functional.normalize(features, p=2, dim=-1).cpu().numpy()
        processed += len(indices)
        if processed % 192 < len(indices):
            print("encoded", processed, "/", len(texts), flush=True)
    return rows


def published_metrics(path: Path):
    spec = importlib.util.spec_from_file_location("r3_published_reproduction", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.metrics, module.PAPER


@torch.inference_mode()
def rerank(directory, scores, docs, queries, rows, dtype, device, max_batch, token_budget):
    tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True, padding_side="left", trust_remote_code=False)
    model = load_model(directory, "rerank", dtype, device)
    candidates = np.argsort(-scores[rows], axis=1, kind="stable")[:, :20]
    template = (directory / "chat_template.jinja").read_text(encoding="utf-8")
    config = json.loads((directory / "1_LogitScore/config.json").read_text(encoding="utf-8"))
    token_ids = [config["false_token_id"], config["true_token_id"]]
    if token_ids != tokenizer.convert_tokens_to_ids(["no", "yes"]):
        raise ValueError("The shipped reranker labels do not match the tokenizer")
    head = model.lm_head.weight[token_ids]

    def prompt(query, doc):
        return tokenizer.apply_chat_template(
            [{"role": "system", "content": RERANK_TASK}, {"role": "query", "content": query},
             {"role": "document", "content": doc}], tokenize=False, chat_template=template,
        )

    prompts = []
    for row, skill_ids in zip(rows, candidates, strict=True):
        query = queries[row]
        prefix_length = len(tokenizer.encode(prompt(query, ""), add_special_tokens=False))
        document_limit = ATTENTION_LIMIT - prefix_length - 8
        for index in skill_ids:
            doc = docs[index]
            tokens = tokenizer.encode(doc, add_special_tokens=False)
            if len(tokens) > document_limit:
                doc = tokenizer.decode(tokens[:document_limit], skip_special_tokens=True)
            prompts.append(prompt(query, doc))
    values = np.empty(len(prompts), dtype=np.float32)
    processed = 0
    for indices, inputs in ranked_batches(tokenizer, prompts, max_batch, token_budget):
        hidden = model.model(**inputs.to(model.device), use_cache=False).last_hidden_state[:, -1]
        predictions = torch.nn.functional.linear(hidden, head).float()
        values[indices] = (predictions[:, 1] - predictions[:, 0]).cpu().numpy()
        processed += len(indices)
        if processed % 96 < len(indices):
            print("reranked", processed, "/", len(prompts), flush=True)
    output = np.full_like(scores[rows], -1e9)
    np.put_along_axis(output, candidates, values.reshape(candidates.shape), axis=1)
    return output


def evaluate(args):
    started = time.perf_counter()
    model_path = args.models / "embedding"
    weights = verified_weights(model_path, "embedding")
    hashes = {name: checksum(args.data / name) for name in DATA_DIGESTS}
    if hashes != DATA_DIGESTS:
        raise ValueError("The downloaded corpus or metric code differs from the pinned R3 release")
    method, paper = published_metrics(args.data / "reproduce.py")
    skills = load_rows(args.data / "skills_test.jsonl")
    records = load_rows(args.data / "test.jsonl")
    if args.smoke:
        records = records[:args.smoke]
    skill_ids, documents = [row["id"] for row in skills], [row["text"] for row in skills]
    task_text, gold = [row["query"] for row in records], [row["skill_ids"] for row in records]
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device.startswith("cuda") else torch.float32
    if args.scores_input:
        with np.load(args.scores_input, allow_pickle=False) as cache:
            if (cache["ordered_skill_ids"].tolist() != skill_ids or cache["query_count"].item() != len(records)
                    or cache["test_sha256"].item() != hashes["test.jsonl"] or cache["model_sha256"].item() != weights["sha256"]):
                raise ValueError("Cached scores are from another release or query split")
            scores = cache["scores"]
        if scores.shape != (len(records), len(skills)) or not np.isfinite(scores).all():
            raise ValueError("Invalid shape or values in cached release scores")
    else:
        tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True, padding_side="left", trust_remote_code=False)
        model = load_model(model_path, "embedding", dtype, device)
        dv = encode(model, tokenizer, documents, args.batch, args.token_budget)
        qv = encode(model, tokenizer, [EMBED_QUERY + item for item in task_text], args.batch, args.token_budget)
        del model
        torch.cuda.empty_cache()
        scores = qv @ dv.T
    measured = method(gold, scores, skill_ids)
    result = {
        "schema": "pt-r3-release-reproduction-v1",
        "data_revision": DATA_REVISION,
        "hashes": hashes,
        "queries": len(records), "skills": len(skills), "model": weights,
        "device": torch.cuda.get_device_name(0) if device.startswith("cuda") else device,
        "torch": torch.__version__, "dtype": str(dtype), "sequence_limit": ATTENTION_LIMIT,
        "embedding": measured, "author_v3_full_reference": paper,
        "embedding_seconds": None if args.scores_input else round(time.perf_counter() - started, 3),
    }
    if args.scores:
        np.savez_compressed(args.scores, scores=scores, ordered_skill_ids=skill_ids, query_count=len(records),
                            test_sha256=hashes["test.jsonl"], model_sha256=weights["sha256"])
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("embedding", measured, flush=True)
    if args.rerank_sample:
        weights = verified_weights(args.models / "rerank", "rerank")
        picked = sorted(range(len(records)), key=lambda i: hashlib.sha256(f"pt-r3-rerank-v1/{i}".encode()).hexdigest())
        rows = np.asarray(sorted(picked[:args.rerank_sample]))
        reranked = rerank(args.models / "rerank", scores, documents, task_text, rows, dtype, device,
                          args.batch, args.token_budget)
        short_gold = [gold[i] for i in rows]
        result["rerank_sample"] = {"indices": rows.tolist(), "model": weights, "shortlist": 20,
                                   "truncation": "trim document before rendering to retain classifier suffix",
                                   "embedding": method(short_gold, scores[rows], skill_ids),
                                   "reranked": method(short_gold, reranked, skill_ids)}
        result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print("rerank sample", result["rerank_sample"]["reranked"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    cache = parser.add_mutually_exclusive_group()
    cache.add_argument("--scores", type=Path)
    cache.add_argument("--scores-input", type=Path)
    parser.add_argument("--batch", type=int, default=20)
    parser.add_argument("--token-budget", type=int, default=8192)
    parser.add_argument("--smoke", type=int, default=0)
    parser.add_argument("--rerank-sample", type=int, default=0)
    evaluate(parser.parse_args())


if __name__ == "__main__":
    main()
