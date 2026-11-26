"""Train a compact ranker with disjoint gold-Skill groups on the R3 release."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import Counter
from pathlib import Path

import numpy as np
import sklearn
from evaluate_pt_retrieval import FILES, REVISION, ROOT, load, observations
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from skill_discriminator.pt_retrieval import BM25, Document, Retrieval, terms

SPLIT_SEED = "pt-r3-group-split-v1"
SHORTLIST_PER_FIELD = 48
FEATURES = (
    "log_metadata", "log_body", "log_name", "metadata_relative", "body_relative", "name_relative",
    "name_term_coverage", "query_coverage_in_metadata", "exact_name", "query_and_document_cjk",
    "metadata_relative_cjk", "body_relative_cjk",
)


def split_groups(documents: list[Document], queries: list[dict]) -> tuple[dict[str, str], set[str]]:
    parent = {item.key: item.key for item in documents}

    def root(key):
        while parent[key] != key:
            key = parent[key]
        return key

    def merge(first, second):
        one, other = sorted((root(first), root(second)))
        parent[other] = one

    equivalent = {}
    for item in documents:
        normalized = " ".join(terms(item.name + " " + item.description + " " + item.body))
        if normalized:
            merge(item.key, equivalent.setdefault(normalized, item.key))
    for query in queries:
        first, *other = query["skill_ids"]
        for key in other:
            merge(first, key)
    groups = {key: root(key) for key in parent}
    holdout = {group for group in groups.values()
               if int(hashlib.sha256(f"{SPLIT_SEED}/{group}".encode()).hexdigest()[:8], 16) % 4 == 0}
    if not holdout or len(holdout) == len(set(groups.values())):
        raise ValueError("Both group partitions are required")
    return groups, holdout


def feature_matrix(indices, scores, title_scores, title_terms, meta_terms, query_terms, query_language):
    meta, body = scores["metadata"], scores["full_text"]
    max_meta, max_body, max_title = (max(item.values(), default=1) for item in (meta, body, title_scores))
    features = []
    for index in indices:
        m, b, n = meta.get(index, 0), body.get(index, 0), title_scores.get(index, 0)
        exact = title_terms[index] and title_terms[index] <= query_terms
        same_cjk = query_language and any(not token.isascii() for token in meta_terms[index])
        features.append((math.log1p(m), math.log1p(b), math.log1p(n), m / max_meta, b / max_body, n / max_title,
                         len(title_terms[index] & query_terms) / max(len(title_terms[index]), 1),
                         len(meta_terms[index] & query_terms) / max(len(query_terms), 1),
                         float(bool(exact)), float(same_cjk), (m / max_meta) * same_cjk, (b / max_body) * same_cjk))
    return np.asarray(features, dtype=np.float64).reshape(len(indices), len(FEATURES))


def ranker(cache: Path) -> dict:
    started = time.perf_counter()
    documents = [Document(item["id"], *item["text"].split(" | ", 2)) for item in load(cache, "skills_test.jsonl")]
    queries = load(cache, "test.jsonl")
    group, test_groups = split_groups(documents, queries)
    keys = {row.key: i for i, row in enumerate(documents)}
    index = Retrieval(documents)
    title_words = [terms(row.name) for row in documents]
    title_terms = [set(items) for items in title_words]
    meta_terms = [set(terms(row.name + " " + row.description)) for row in documents]
    titles = BM25(title_words)
    training_deltas = {"forced_positive": [], "shortlist_pairwise": []}
    holdout = []
    train_cases = 0
    for query in queries:
        text = query["query"]
        gold = set(query["skill_ids"])
        test = group[query["skill_ids"][0]] in test_groups
        scores = index.scores(text)
        sparse = [index.rank(scores[method], SHORTLIST_PER_FIELD) for method in ("metadata", "full_text")]
        title_scores = titles.score(terms(text))
        title_hits = index.rank(title_scores, SHORTLIST_PER_FIELD)
        pooled = list(dict.fromkeys(sparse[0] + sparse[1] + title_hits))
        words = set(terms(text))
        is_cjk = query["direction"].endswith("zh")
        if test:
            matrix = feature_matrix(pooled, scores, title_scores, title_terms, meta_terms, words, is_cjk)
            holdout.append((query["direction"], gold, pooled, matrix, sparse[0][:10], sparse[1][:10], title_hits[:10]))
            continue
        train_cases += 1
        negatives = [i for i in pooled if documents[i].key not in gold and group[documents[i].key] not in test_groups]
        if not negatives:
            continue
        positives = [keys[key] for key in sorted(gold)]
        distributed = [negatives[i] for i in np.linspace(0, len(negatives) - 1, min(16, len(negatives)), dtype=int)]
        for variant, (selected, contrasts) in {
            "forced_positive": (positives, negatives[:16]),
            "shortlist_pairwise": ([i for i in positives if i in pooled], distributed),
        }.items():
            if not selected:
                continue
            values = feature_matrix(selected + contrasts, scores, title_scores, title_terms, meta_terms, words, is_cjk)
            for positive in values[:len(selected)]:
                training_deltas[variant].extend(positive - values[len(selected):])
    coefficients = {}
    for variant, pairs in training_deltas.items():
        raw = np.asarray(pairs)
        variants = np.concatenate((raw, -raw))
        target = np.repeat([1, 0], len(raw))
        standard = StandardScaler().fit(variants)
        model = LogisticRegression(C=.5, fit_intercept=False, max_iter=250).fit(standard.transform(variants), target)
        coefficients[variant] = model.coef_[0] / standard.scale_
    labels = ("metadata_bm25", "body_bm25", "name_bm25", "forced_positive", "shortlist_pairwise", "anchored_pairwise")
    summary = {label: Counter() for label in labels}
    multi = 0
    for _, gold, pooled, matrix, meta, body, title in holdout:
        multi += len(gold) > 1
        margins = {variant: matrix @ weights for variant, weights in coefficients.items()}
        base = margins["shortlist_pairwise"]
        anchor = .65 * matrix[:, 3] + .35 * (base - base.min()) / max(float(np.ptp(base)), 1e-8) if pooled else base
        predicted = [[pooled[i] for i in np.argsort(-score, kind="stable")[:10]]
                     for score in (*margins.values(), anchor)]
        for label, hits in zip(labels, (meta, body, title, *predicted), strict=True):
            summary[label].update(observations(gold, [documents[i].key for i in hits]))
    results = {method: {metric: value / (multi if metric == "set_compat" else len(holdout)) for metric, value in row.items()}
               for method, row in summary.items()}
    return {"schema": "pt-r3-gold-group-ranker-v1", "revision": REVISION, "data_sha256": {k: v[1] for k, v in FILES.items()},
            "seed": SPLIT_SEED, "group_count": len(set(group.values())), "holdout_groups": len(test_groups),
            "train_queries": train_cases, "holdout_queries": len(holdout),
            "holdout_directions": dict(Counter(row[0] for row in holdout)), "holdout_multi": multi,
            "pairs": {variant: len(rows) for variant, rows in training_deltas.items()},
            "shortlist_per_field": SHORTLIST_PER_FIELD, "features": FEATURES,
            "results": results, "numpy_version": np.__version__, "sklearn_version": sklearn.__version__,
            "weights": {variant: dict(zip(FEATURES, weights.tolist(), strict=True)) for variant, weights in coefficients.items()},
            "elapsed_seconds": round(time.perf_counter() - started, 2)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / "tmp/pt-benchmark/r3")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    result = ranker(args.cache)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
