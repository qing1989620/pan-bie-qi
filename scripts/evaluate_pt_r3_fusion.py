"""Calibrate lexical reranking on disjoint groups from the released R3 corpus."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from evaluate_pt_retrieval import FILES, REVISION, load, observations
from train_pt_ranker import SPLIT_SEED, split_groups

from skill_discriminator.pt_retrieval import BM25, Document, terms

WEIGHTS = (0.0, .005, .01, .02, .04, .08, .12)
SHORTLIST = 20


def metrics(query_indices, gold, hits, scores):
    order = np.argsort(-scores[query_indices], axis=1, kind="stable")
    ranked = np.take_along_axis(hits[query_indices], order, axis=1)
    return [observations(gold[q], list(row)) for q, row in zip(query_indices, ranked, strict=True)]


def aggregate(rows):
    columns = defaultdict(list)
    for row in rows:
        for key, value in row.items():
            columns[key].append(value)
    return {key: float(np.mean(values)) for key, values in columns.items()}


def difference_interval(reference, fused, query_indices, query_groups):
    by_group = defaultdict(list)
    for query_index, left, right in zip(query_indices, reference, fused, strict=True):
        by_group[query_groups[query_index]].append(right["ndcg@10"] - left["ndcg@10"])
    totals = np.asarray([sum(group) for group in by_group.values()])
    counts = np.asarray([len(group) for group in by_group.values()])
    draw = np.random.default_rng(837).integers(0, len(totals), size=(2000, len(totals)))
    return np.quantile(totals[draw].sum(axis=1) / counts[draw].sum(axis=1), [.025, .975]).tolist()


def evaluate(data_dir, score_path):
    docs = [Document(row["id"], *row["text"].split(" | ", 2)) for row in load(data_dir, "skills_test.jsonl")]
    queries = load(data_dir, "test.jsonl")
    ids = np.asarray([row.key for row in docs])
    with np.load(score_path, allow_pickle=False) as archive:
        if (not np.array_equal(archive["ordered_skill_ids"], ids)
                or archive["query_count"].item() != len(queries)
                or archive["test_sha256"].item() != FILES["test.jsonl"][1]):
            raise ValueError("R3 embeddings were computed on a different corpus")
        dense = archive["scores"]
        weight_hash = archive["model_sha256"].item()
    if dense.shape != (len(queries), len(docs)) or not np.isfinite(dense).all():
        raise ValueError("Invalid shape or values in R3 scores")

    pool = np.argpartition(-dense, SHORTLIST - 1, axis=1)[:, :SHORTLIST]
    values = np.take_along_axis(dense, pool, axis=1)
    order = np.argsort(-values, axis=1, kind="stable")
    similarities = np.take_along_axis(values, order, axis=1)
    pool = np.take_along_axis(pool, order, axis=1)
    hits = ids[pool]
    del dense

    bm25 = BM25([terms(f"{row.name} {row.description}") for row in docs])
    lexical = np.zeros_like(similarities)
    for i, record in enumerate(queries):
        matches = bm25.score(terms(record["query"]))
        scores = np.asarray([matches.get(index, 0) for index in pool[i]])
        if scores.max() > 0:
            lexical[i] = scores / scores.max()

    groups, held_groups = split_groups(docs, queries)
    query_groups = [groups[query["skill_ids"][0]] for query in queries]
    train_ids = np.asarray([i for i, group in enumerate(query_groups) if group not in held_groups])
    held_ids = np.asarray([i for i, group in enumerate(query_groups) if group in held_groups])
    gold = [set(record["skill_ids"]) for record in queries]
    gaps = similarities[:, 0] - similarities[:, 1]
    cutoff = max(float(np.quantile(gaps[train_ids], .75)), 1e-8)
    gated = np.maximum(1 - gaps / cutoff, 0)[:, None] * lexical
    reference = metrics(held_ids, gold, hits, similarities)
    variants = {}
    for label, values in (("constant", lexical), ("margin_gated", gated)):
        candidate_scores = {}
        for alpha in WEIGHTS:
            current = metrics(train_ids, gold, hits, similarities + alpha * values)
            candidate_scores[alpha] = aggregate(current)["ndcg@10"]
        weight = max(WEIGHTS, key=lambda a: (candidate_scores[a], -a))
        evaluated = metrics(held_ids, gold, hits, similarities + weight * values)
        variants[label] = {
            "alpha": weight, "train_ndcg_grid": candidate_scores, "holdout": aggregate(evaluated),
            "ndcg_delta_ci95": difference_interval(reference, evaluated, held_ids, query_groups),
        }
    return {
        "schema": "pt-r3-lexical-fusion-v1", "r3_revision": REVISION, "model_sha256": weight_hash,
        "group_split": SPLIT_SEED, "train_queries": len(train_ids), "holdout_queries": len(held_ids),
        "holdout_groups": len(held_groups), "shortlist": SHORTLIST, "gap_cutoff": cutoff,
        "reference_holdout": aggregate(reference), "variants": variants,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.data, args.scores)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
