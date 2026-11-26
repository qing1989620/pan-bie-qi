"""Optional local multilingual E5 retrieval with a pinned, quantized ONNX export."""

from __future__ import annotations

import hashlib
import os
import threading
from functools import lru_cache
from pathlib import Path

MODEL_REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
MODEL_NAME = "multilingual-e5-small int8"
MODEL_DIR = "PAN_BIE_QI_E5_DIR"
CHECKSUMS = {
    "tokenizer.json": "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
    "model_quantized.onnx": "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193",
}
_LOCK = threading.Lock()


class Encoder:
    def __init__(self, directory: Path):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        for name, expected in CHECKSUMS.items():
            with (directory / name).open("rb") as stream:
                observed = hashlib.file_digest(stream, "sha256").hexdigest()
            if observed != expected:
                raise ValueError("Local semantic model does not match the expected revision")
        self.np = np
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        self.tokenizer.enable_truncation(max_length=160)
        config = ort.SessionOptions()
        config.intra_op_num_threads = min(6, os.cpu_count() or 1)
        config.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(directory / "model_quantized.onnx"), sess_options=config,
                                           providers=["CPUExecutionProvider"])
        self.inputs = {item.name for item in self.session.get_inputs()}

    def encode(self, texts: list[str], prefix: str):
        np = self.np
        vectors = []
        for start in range(0, len(texts), 24):
            batch = self.tokenizer.encode_batch([prefix + ": " + text[:4000] for text in texts[start:start + 24]])
            mask = np.asarray([row.attention_mask for row in batch], dtype=np.int64)
            ids = np.asarray([row.ids for row in batch], dtype=np.int64)
            features = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
            hidden, *_ = self.session.run(None, {name: features[name] for name in self.inputs})
            pooled = (hidden * mask[:, :, None]).sum(axis=1, dtype=np.float32)
            vectors.append(pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-8))
        return np.concatenate(vectors, axis=0)


@lru_cache(maxsize=1)
def _load(directory: str) -> Encoder:
    return Encoder(Path(directory))


def paragraph(doc) -> str:
    return f"{doc.name}. {doc.description}" if doc.description else f"{doc.name}. {doc.body[:700]}"


def fuse(dense: dict[int, float], lexical: dict[int, float]) -> dict[int, float]:
    if not dense:
        return lexical
    lexical_max = max(lexical.values(), default=1)
    best, worst = max(dense.values()), min(dense.values())
    span = max(best - worst, 1e-8)
    return {i: .82 * (score - worst) / span + .18 * lexical.get(i, 0) / lexical_max for i, score in dense.items()}


def rank(query: str, docs, lexical: dict[int, float]) -> tuple[dict[int, float], dict]:
    directory = os.environ.get(MODEL_DIR)
    if not directory or not docs:
        return lexical, {"state": "not_configured" if not directory else "no_candidates"}
    try:
        with _LOCK:
            encoder = _load(directory)
        query_vector = encoder.encode([query], "query")[0]
        values = encoder.encode([paragraph(doc) for doc in docs], "passage") @ query_vector
        return fuse(dict(enumerate(map(float, values))), lexical), {"state": "used", "model": MODEL_NAME}
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        return lexical, {"state": "unavailable", "reason": type(exc).__name__}
