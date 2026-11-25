"""Train and compare document-only predictors on versioned public Skill outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import sklearn
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import TfidfTransformer
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from unseen_skill_study import (
    AGENT,
    ARCHIVE_SHA256,
    BASELINE,
    DIMENSIONS,
    ENVIRONMENT,
    REVISION,
    ROOT,
    SUITE,
    WORD,
    components,
    digest,
    get,
    group_folds,
    group_interval,
    metrics,
    numeric_features,
    parse_document,
    published_rows,
)

NEW_VERSIONS = ("1.6.8", "1.6.9", "1.6.10")
NEW_COMPONENTS = frozenset({
    "ALCHEMI Toolkit", "Auto Ontology", "CompileIQ", "CUDA-Q", "cuDNN Frontend",
    "FastGen", "Ising", "Model Optimizer", "NeMo Fabric", "Newton", "NIXL",
    "NVIDIA Skills", "TensorRT-LLM", "Triton Inference Server", "Video Search and Summarization",
})
PER_COMPONENT = 8
SEED = 20261125
AXES = tuple((name, metric) for name in DIMENSIONS for metric in ("with_skill", "lift"))
CANDIDATES = ("median", BASELINE, "structure_ridge", "diverse_top8", "rubric_paired_ridge", "rubric_paired_trees")
ABLATIONS = ("paired_ridge_without_rubric", "paired_trees_without_rubric")
METHODS = CANDIDATES + ABLATIONS + ("selected",)
PAIRED = np.array([[1, 0, 0, 0], [1, -1, 0, 0], [0, 0, 1, 0], [0, 0, 1, -1]])
LOW = np.array([0., -1., 0., -1.])


def new_rows(archive: dict) -> list[dict]:
    """Freeze the sample by package names before reading outcomes."""
    eligible = defaultdict(list)
    for item in archive["skills"]:
        if (item["evaluator_version"] in NEW_VERSIONS and item["component"] in NEW_COMPONENTS
                and item["environment"] == ENVIRONMENT and (item["tasks"] or 0) >= 3 and item["dataset_digest"]
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,160}", item["catalog_dir"])):
            eligible[item["component"]].append(item)
    metadata = {
        item["catalog_dir"]: item
        for cohort in eligible.values()
        for item in sorted(cohort, key=lambda x: digest(f"{SEED}/{x['catalog_dir']}".encode()))[:PER_COMPONENT]
    }
    scores = defaultdict(dict)
    for result in archive["results"]:
        key, dimension = result["catalog_dir"], result["dimension"]
        score, gain = result["score_pct"], result["uplift_pct"]
        if key in metadata and result["agent"] == AGENT and dimension in DIMENSIONS and score is not None and gain is not None:
            if dimension in scores[key]:
                raise ValueError(f"Duplicate outcome for {key}/{dimension}")
            scores[key][dimension] = {"with_skill": score / 100, "lift": gain / 100}
    return [{**metadata[key], "scores": scores[key]} for key in sorted(scores) if len(scores[key]) == len(DIMENSIONS)]


def dataset(cache: Path) -> tuple[list[dict], list[str], list[bytes]]:
    archive = get("benchmarks.json", cache, 4_000_000)
    if digest(archive) != ARCHIVE_SHA256:
        raise ValueError("Outcome archive hash changed.")
    outcomes = json.loads(archive)
    rows = published_rows(outcomes) + new_rows(outcomes)
    paths = [f"skills/{row['catalog_dir']}/SKILL.md" for row in rows]
    with ThreadPoolExecutor(max_workers=8) as executor:
        documents = list(executor.map(lambda path: get(path, cache, 600_000), paths))
    tree_path = cache / "git-tree.json"
    if not tree_path.is_file():
        request = Request(f"https://api.github.com/repos/NVIDIA/skills/git/trees/{REVISION}?recursive=1")
        with urlopen(request, timeout=20) as response:
            tree_path.write_bytes(response.read(8_000_000))
    tree = json.loads(tree_path.read_bytes())
    if tree.get("sha") != REVISION or tree.get("truncated"):
        raise ValueError("GitHub tree does not cover the frozen revision.")
    blobs = {entry["path"]: entry["sha"] for entry in tree["tree"] if entry["type"] == "blob"}
    for path, raw in zip(paths, documents, strict=True):
        actual = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False).hexdigest()
        if actual != blobs.get(path):
            raise ValueError(f"Git blob hash changed for {path}")
    return rows, paths, documents


def paired_predictions(inputs, grouped_weights, y, train, test):
    standard = StandardScaler().fit(inputs[train])
    relative = y[train] @ PAIRED.T
    ridge = Ridge(alpha=24).fit(standard.transform(inputs[train]), relative, sample_weight=grouped_weights)
    trees = ExtraTreesRegressor(n_estimators=96, min_samples_leaf=6, max_features=.75, random_state=SEED)
    trees.fit(inputs[train], relative, sample_weight=grouped_weights)
    return (
        np.clip(ridge.predict(standard.transform(inputs[test])), 0, 1) @ PAIRED.T,
        np.clip(trees.predict(inputs[test]), 0, 1) @ PAIRED.T,
    )


def predict(terms, structure, rubric, y, groups, train, test, *, ablations=False):
    vectorizer = DictVectorizer()
    ids = TfidfTransformer()
    x_train = ids.fit_transform(vectorizer.fit_transform([terms[i] for i in train]))
    x_test = ids.transform(vectorizer.transform([terms[i] for i in test]))
    similarities = (x_test @ x_train.T).toarray()
    neighbors = np.argsort(-similarities, kind="stable", axis=1)
    medians = np.median(y[train], axis=0)

    def pool(indices, sim):
        weights = sim[indices]
        return np.average(y[train[indices]], weights=weights, axis=0) if weights.sum() else medians

    top8, diverse = [], []
    for order, sim in zip(neighbors, similarities, strict=True):
        top8.append(pool(order[:8], sim))
        chosen, counts = [], Counter()
        for index in order:
            group = groups[train[index]]
            if counts[group] < 2:
                chosen.append(index)
                counts[group] += 1
            if len(chosen) >= 8:
                break
        diverse.append(pool(chosen, sim))

    standard = StandardScaler().fit(structure[train])
    direct = Ridge(alpha=16).fit(standard.transform(structure[train]), y[train])
    direct_predictions = direct.predict(standard.transform(structure[test]))
    group_count = Counter(groups[train])
    weights = np.array([1 / group_count[group] ** .5 for group in groups[train]])
    weights /= weights.mean()
    outputs = (
        np.tile(medians, (len(test), 1)), top8, direct_predictions, diverse,
        *paired_predictions(np.column_stack((structure, rubric)), weights, y, train, test),
    )
    if ablations:
        outputs += paired_predictions(structure, weights, y, train, test)
    return np.clip(np.stack(outputs, axis=1), LOW, 1)


def fit(terms, structure, rubric, y, groups, train, test):
    inside_predictions = np.empty((len(train), len(CANDIDATES), len(AXES)))
    for inside, outside in group_folds(groups[train], min(4, len(set(groups[train])))):
        inside_predictions[outside] = predict(terms, structure, rubric, y, groups, train[inside], train[outside])
    errors = np.abs(inside_predictions - y[train, None, :]).mean(axis=0)
    chosen = np.argmin(errors, axis=0)
    estimates = predict(terms, structure, rubric, y, groups, train, test, ablations=True)
    point = estimates[:, chosen, np.arange(len(AXES))]
    return np.concatenate((estimates, point[:, None, :]), axis=1), [CANDIDATES[key] for key in chosen]


def summarize(actual, estimated, groups):
    results = {}
    for axis, (dimension, measure) in enumerate(AXES):
        truth = actual[:, axis]
        residual = np.abs(estimated[:, :, axis] - truth[:, None])
        results[f"{dimension}/{measure}"] = {
            "methods": {method: metrics(truth, estimated[:, i, axis]) for i, method in enumerate(METHODS)},
            "selected_minus_top8_pp_ci95": [100 * edge for edge in group_interval(residual[:, -1] - residual[:, 1], groups)],
            "selected_minus_median_pp_ci95": [100 * edge for edge in group_interval(residual[:, -1] - residual[:, 0], groups)],
            "rubric_ridge_minus_without_rubric_pp_ci95": [100 * edge for edge in group_interval(residual[:, 4] - residual[:, 6], groups)],
            "rubric_trees_minus_without_rubric_pp_ci95": [100 * edge for edge in group_interval(residual[:, 5] - residual[:, 7], groups)],
        }
    return results


def evaluate(rows, paths, raw):
    from skill_discriminator.ratings import rubric_evidence

    parsed = [parse_document(item) for item in raw]
    terms = [Counter(WORD.findall("\n".join(item).casefold())) for item in parsed]
    structure = np.array([numeric_features(*item) for item in parsed])
    rubric = np.array([[evidence is not None for axis in rubric_evidence(item.decode("utf-8-sig")).values()
                       for _, _, evidence in axis] for item in raw], dtype=float)
    groups = components(rows, [item[2] for item in parsed])
    y = np.array([[row["scores"][dimension][metric] for dimension, metric in AXES] for row in rows])
    predictions = np.empty((len(rows), len(METHODS), len(AXES)))
    partitions = np.empty(len(rows), dtype=int)
    folds = []
    for number, (train, test) in enumerate(group_folds(groups, 5), 1):
        predictions[test], chosen = fit(terms, structure, rubric, y, groups, train, test)
        partitions[test] = number
        folds.append({"fold": number, "test_groups": sorted(set(groups[test])), "test": len(test),
                      "train": len(train), "selected": chosen})
    old = np.array([row["evaluator_version"] == SUITE for row in rows])
    common = np.isin(groups, groups[old])
    train, test = np.flatnonzero(old), np.flatnonzero(~old & ~common)
    transfer, chosen = fit(terms, structure, rubric, y, groups, train, test)
    return {
        "schema": "skill-assessment-training-v1", "seed": SEED, "revision": REVISION, "archive_sha256": ARCHIVE_SHA256,
        "agent": AGENT, "environment": ENVIRONMENT, "versions": dict(sorted(Counter(row["evaluator_version"] for row in rows).items())),
        "new_components": sorted(NEW_COMPONENTS), "new_component_cap": PER_COMPONENT,
        "axes": AXES, "methods": METHODS, "numpy_version": np.__version__, "sklearn_version": sklearn.__version__,
        "group_cv": {"count": len(rows), "groups": len(set(groups)), "folds": folds,
                     "summary": summarize(y, predictions, groups)},
        "new_release_transfer": {"training_skills": len(train), "training_groups": len(set(groups[train])),
                                 "count": len(test), "groups": len(set(groups[test])), "selected": chosen,
                                 "summary": summarize(y[test], transfer, groups[test])},
        "observations": [
            {"path": path, "suite": row["evaluator_version"], "component": row["component"], "group": str(groups[i]),
             "dataset_digest": row["dataset_digest"], "tasks": row["tasks"], "document_sha256": digest(content),
             "fold": int(partitions[i]), "observed": y[i].tolist(), "predicted": predictions[i].tolist()}
            for i, (row, path, content) in enumerate(zip(rows, paths, raw, strict=True))
        ],
        "transfer_predictions": {paths[i]: point.tolist() for i, point in zip(test, transfer, strict=True)},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / "tmp/quality-baselines/nvidia")
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    rows, paths, raw = dataset(options.cache)
    print(f"Verified {len(rows)} documents against the frozen tree.", flush=True)
    if options.output:
        report = evaluate(rows, paths, raw)
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        for cohort in ("group_cv", "new_release_transfer"):
            section = report[cohort]
            print(f"{cohort}: {section['count']} Skills, {section['groups']} groups")
            for axis, summary in section["summary"].items():
                errors = ", ".join(f"{method}={100 * score['mae']:.2f}" for method, score in summary["methods"].items())
                print(f"{axis}: {errors}. Selected vs top8 CI {summary['selected_minus_top8_pp_ci95']}")


if __name__ == "__main__":
    main()
