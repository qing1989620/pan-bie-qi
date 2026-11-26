"""Recompute lexical PT baselines on the official R3-Skill test release."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.request import urlopen

from skill_discriminator.pt_retrieval import Document, Retrieval, plan
from skill_discriminator.pt_semantic import (
    CHECKSUMS,
    MODEL_NAME,
    MODEL_REVISION,
    Encoder,
    fuse,
    paragraph,
)

REVISION = "57d9285969cc293832c71ecbe42a75ac416bef93"
FILES = {
    "skills_test.jsonl": (18_000_000, "b80f88aa0d3826fb6456eb45f84be12e4ca6df23d60a1148ac92854a0ce2a6a5"),
    "test.jsonl": (4_500_000, "f1f241303379d313ca7904460c4395b3e80143f5b3ee4a1e0f4056135e30a947"),
}
METHODS = ("metadata", "full_text", "pt_fields", "pt_fields_with_task_terms")
ROOT = Path(__file__).resolve().parents[1]


def load(cache: Path, name: str) -> list[dict]:
    limit, digest = FILES[name]
    path = cache / name
    if path.is_file():
        raw = path.read_bytes()
    else:
        url = f"https://raw.githubusercontent.com/Tencent/R3-Skill/{REVISION}/data/{name}"
        with urlopen(url, timeout=40) as response:
            raw = response.read(limit + 1)
    if len(raw) > limit or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f"Upstream SHA-256 changed for {name}")
    if not path.is_file():
        path.write_bytes(raw)
    return [json.loads(line) for line in raw.splitlines()]


def observations(gold: set[str], hits: list[str]) -> dict[str, float]:
    ranks = [rank for rank, item in enumerate(hits[:10], 1) if item in gold]
    norm = sum(1 / math.log2(rank + 1) for rank in range(1, min(len(gold), 10) + 1))
    found = len(ranks)
    output = {"hit@1": float(bool(ranks and ranks[0] == 1)), "ndcg@10": sum(
        1 / math.log2(rank + 1) for rank in ranks
    ) / norm, "recall@10": found / len(gold), "full_coverage@10": float(found == len(gold))}
    if len(gold) > 1:
        output["set_compat"] = float(gold.issubset(hits[:len(gold)]))
    return output


def evaluate(cache: Path, encoder_path: Path | None = None) -> dict:
    docs = load(cache, "skills_test.jsonl")
    queries = load(cache, "test.jsonl")
    parsed = []
    for item in docs:
        name, description, body = item["text"].split(" | ", 2)
        parsed.append(Document(item["id"], name, description, body[:120_000]))
    index = Retrieval(parsed)
    catalog = {row.key for row in parsed}
    if len(catalog) != len(docs):
        raise ValueError("Duplicate published Skill IDs")
    values = defaultdict(Counter)
    counts = Counter()
    elapsed = Counter()
    methods = METHODS
    dense_rows = None
    if encoder_path is not None:
        print("Encoding the pinned public catalogue and query set on CPU.", flush=True)
        started = time.perf_counter()
        encoder = Encoder(encoder_path)
        passage_vectors = encoder.encode([paragraph(row) for row in parsed], "passage")
        query_vectors = encoder.encode([row["query"] for row in queries], "query")
        dense_rows = query_vectors @ passage_vectors.T
        elapsed["e5_forward_seconds"] = time.perf_counter() - started
        methods += ("e5", "pt_semantic_fusion")
        print(f"Finished embeddings in {elapsed['e5_forward_seconds']:.1f} s.", flush=True)
    for query_no, query in enumerate(queries):
        text = query["query"]
        gold = set(query["skill_ids"])
        if not gold or not gold <= catalog:
            raise ValueError("Published gold is missing from the frozen candidate pool")
        started = time.perf_counter()
        scores = index.scores(text)
        elapsed["all_field_scores"] += time.perf_counter() - started
        expanded = plan(text)
        if expanded.requirements:
            started = time.perf_counter()
            adjusted = index.scores(expanded.ranking_text)["pt"]
            elapsed["extra_field_scores_for_expansion"] += time.perf_counter() - started
        else:
            adjusted = scores["pt"]
        if expanded.requirements:
            counts["matched_expansion_rules"] += 1
        directions = ["all", query["direction"], "multi" if len(gold) > 1 else "single"]
        for cohort in directions:
            counts[cohort] += 1
            if len(gold) > 1:
                counts[cohort + "_multi"] += 1
        rankings = (scores["metadata"], scores["full_text"], scores["pt"], adjusted)
        if dense_rows is not None:
            dense = dict(enumerate(map(float, dense_rows[query_no])))
            rankings += (dense, fuse(dense, scores["metadata"]))
        for method, values_for_query in zip(methods, rankings, strict=True):
            hits = [parsed[i].key for i in index.rank(values_for_query, 10)]
            metrics = observations(gold, hits)
            for cohort in directions:
                values[method, cohort].update(metrics)
    results = {
        cohort: {method: {metric: value / counts[cohort + "_multi" if metric == "set_compat" else cohort]
                          for metric, value in sorted(values[method, cohort].items())}
                 for method in methods}
        for cohort in ("all", "single", "multi", "en2en", "en2zh", "zh2en", "zh2zh")
    }
    return {"schema": "pt-r3-skill-lexical-v1", "revision": REVISION, "data_sha256": {k: v[1] for k, v in FILES.items()},
            "skills": len(docs), "queries": len(queries), "cohort_counts": dict(counts), "results": results,
            "scoring_seconds": dict(elapsed), "external_model_calls": 0,
            "local_model": {"name": MODEL_NAME, "revision": MODEL_REVISION, "asset_sha256": CHECKSUMS} if dense_rows is not None else None,
            "published_neural_reference": {"source": "https://arxiv.org/html/2606.03565v2#S4.SS2",
                                           "model": "R3-Embedding (paper Table 5)",
                                           "hit@1": .7024, "ndcg@10": .8064, "recall@10": .9180, "set_compat": .2218}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / "tmp/pt-benchmark/r3")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--encoder", type=Path)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    report = evaluate(args.cache, args.encoder)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for method, metrics in report["results"]["all"].items():
        print(method, " ".join(f"{name}={100 * value:.2f}%" for name, value in metrics.items()))


if __name__ == "__main__":
    main()
