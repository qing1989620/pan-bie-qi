"""Test documentation-only quality estimates on held-out NVIDIA product groups."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from urllib.error import URLError
from urllib.request import Request, urlopen

import numpy as np

from skill_discriminator.inspection import frontmatter, inspect

ROOT = Path(__file__).resolve().parents[1]
REVISION = "252ba53487b73eaafbfcccf3e750c7b350b93db9"
RAW = f"https://raw.githubusercontent.com/NVIDIA/skills/{REVISION}/"
ARCHIVE_SHA256 = "87f35b34024b2ffc55bcd244200265835d8f61e771039559cf07776fd9c94e90"
BASELINE = "skillct_top8"
DIMENSIONS = ("Correctness", "Effectiveness")
AGENT = "codex"
SUITE = "1.5.6"
ENVIRONMENT = "k8s-sandbox"
# Fix the product subset by subject and execution configuration before scoring.
COMPONENTS = frozenset({
    "CDF AnomalyGen", "CDF Augmentation", "CDF Auto-Labeling", "CDF Curation and Retrieval", "CDF Simulation",
    "Cosmos Data Factory Orchestration", "CUDA-Q", "cuDF", "cuOpt", "DeepStream", "G-Assist", "HoloHub",
    "Holoscan SDK", "Jetson Device", "NeMo AutoModel", "NeMo Fabric", "NeMo Relay", "NeMo Retriever",
    "Nemotron Speech", "NVIDIA App", "NVIDIA Broadcast", "NVIDIA FLARE", "Nsight", "NVSHMEM",
    "ovnewton", "Portfolio Optimization", "RTX Remix", "Run:ai", "TAO Toolkit", "TileGym",
    "Video Search and Summarization",
})
METHODS = ("median", BASELINE, "structure_ridge", "nested_blend")
WORD = re.compile(r"[^\W_]+", re.UNICODE)
FEATURES = (
    ("trigger", r"when to use|use (?:this|when)|trigger|activate when|适用|触发"),
    ("inputs", r"\binputs?\b|prerequisites?|requirements?|输入|前置"),
    ("outputs", r"\boutputs?\b|expected results?|deliverables?|输出|交付"),
    ("verification", r"verif(?:y|ication)|validat(?:e|ion)|acceptance|expected results?|验收|校验"),
    ("boundaries", r"do not use|not for|out of scope|unsupported|limitation|不适用|边界"),
    ("failures", r"troubleshoot|common errors?|on failure|fallback|recover|失败|异常"),
    ("versions", r"\bcompatibility\b|supported versions?|version \d|版本|兼容"),
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get(path: str, cache: Path, limit: int) -> bytes:
    target = cache.joinpath(*path.split("/"))
    if target.exists():
        return target.read_bytes()
    try:
        with urlopen(RAW + path, timeout=14) as response:
            data = response.read(limit + 1)
    except (URLError, TimeoutError):
        request = Request(f"https://api.github.com/repos/NVIDIA/skills/contents/{path}?ref={REVISION}",
                          headers={"Accept": "application/vnd.github.raw+json"})
        with urlopen(request, timeout=14) as response:
            data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"Published document exceeds the study limit: {path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return data


def published_rows(archive: dict) -> list[dict]:
    metadata = {item["catalog_dir"]: item for item in archive["skills"]
                if item["component"] in COMPONENTS and item["environment"] == ENVIRONMENT
                and item["evaluator_version"] == SUITE and (item["tasks"] or 0) >= 3
                and item.get("dataset_digest")}
    scores = defaultdict(dict)
    for result in archive["results"]:
        directory = result["catalog_dir"]
        if directory in metadata and result["agent"] == AGENT and result["dimension"] in DIMENSIONS:
            score, uplift = result["score_pct"], result["uplift_pct"]
            if score is not None and uplift is not None:
                key = result["dimension"]
                if key in scores[directory]:
                    raise ValueError(f"Duplicate result row for {directory}/{key}")
                scores[directory][key] = {"with_skill": score / 100, "without_skill": (score - uplift) / 100,
                                          "lift": uplift / 100}
    return [{**metadata[key], "scores": scores[key]} for key in sorted(scores)
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,160}", key) and len(scores[key]) == len(DIMENSIONS)]


def parse_document(raw: bytes) -> tuple[str, str, str]:
    document = raw.decode("utf-8-sig")
    header = re.match(r"\A---[ \t]*\r?\n.*?^---[ \t]*(?:\r?\n|$)", document, re.MULTILINE | re.DOTALL)
    metadata, _ = frontmatter(document)
    body = document[header.end():] if header else document
    return str(metadata.get("name") or ""), str(metadata.get("description") or ""), body


def numeric_features(name: str, description: str, body: str) -> list[float]:
    lines = body.splitlines()
    headings = sum(bool(re.match(r"^\s{0,3}#{1,6}\s+\w", line)) for line in lines)
    ordered = sum(bool(re.match(r"^\s*\d+[.)]\s+", line)) for line in lines)
    code_fences = sum(line.lstrip().startswith(("```", "~~~")) for line in lines)
    refs = len(re.findall(r"\b(?:references|scripts|assets|examples)/[^\s)`]+", body))
    tokens = set(WORD.findall(body.casefold()))
    description_tokens = set(WORD.findall(description.casefold()))
    overlap = len(tokens & description_tokens) / max(1, len(description_tokens))
    return [
        math.log1p(len(body)), math.log1p(len(description)), math.log1p(headings), math.log1p(ordered),
        math.log1p(code_fences), math.log1p(refs), overlap, float(bool(name and description)),
        *(float(bool(re.search(pattern, body, re.IGNORECASE))) for _, pattern in FEATURES),
    ]


def components(rows: list[dict], bodies: list[str]) -> np.ndarray:
    """Keep product families and highly similar documents in a single fold."""
    groups = {row["component"]: row["component"] for row in rows}

    def root(group):
        while groups[group] != group:
            group = groups[group]
        return group

    shingles = []
    for body in bodies:
        words = WORD.findall(body.casefold())
        shingles.append(set(zip(words, words[1:], words[2:], words[3:], words[4:], strict=False)))
    for i, left in enumerate(shingles):
        for j, right in enumerate(shingles[:i]):
            category, other = root(rows[i]["component"]), root(rows[j]["component"])
            if category != other and left and right and len(left & right) / len(left | right) >= .8:
                groups[max(category, other)] = min(category, other)
    return np.array([root(row["component"]) for row in rows])


def group_folds(groups: np.ndarray, count: int):
    totals = Counter(groups)
    buckets = [set() for _ in range(count)]
    sizes = [0] * count
    for label in sorted(totals, key=lambda key: (-totals[key], key)):
        index = min(range(count), key=sizes.__getitem__)
        buckets[index].add(label)
        sizes[index] += totals[label]
    for bucket in buckets:
        mask = np.array([label in bucket for label in groups])
        yield np.flatnonzero(~mask), np.flatnonzero(mask)


def estimates(term_counts: list[Counter], features: np.ndarray, targets: np.ndarray, train, test) -> np.ndarray:
    document_frequency = Counter(term for index in train for term in term_counts[index])
    idf = {word: math.log((1 + len(train)) / (1 + count)) + 1 for word, count in document_frequency.items()}

    def normalized(index):
        weighted = {word: count * idf[word] for word, count in term_counts[index].items() if word in idf}
        norm = math.sqrt(sum(value * value for value in weighted.values()))
        return {word: value / norm for word, value in weighted.items()}

    references = [normalized(index) for index in train]
    relevant = np.array([[sum(weight * reference.get(word, 0) for word, weight in query.items())
                          for reference in references] for query in map(normalized, test)])
    neighbors = np.argsort(-relevant, axis=1, kind="stable")[:, :8]
    weights = np.take_along_axis(relevant, neighbors, axis=1)
    medians = np.median(targets[train], axis=0)
    sums = weights.sum(axis=1)
    nearest = np.einsum("ij,ijk->ik", weights, targets[train][neighbors])
    nearest = np.divide(nearest, sums[:, None], out=np.tile(medians, (len(test), 1)), where=sums[:, None] > 0)
    mean, std = features[train].mean(axis=0), features[train].std(axis=0)
    scaled = (features - mean) / np.where(std, std, 1)
    x, y = scaled[train], targets[train]
    intercept = y.mean(axis=0)
    coefficients = np.linalg.solve(x.T @ x + 16 * np.eye(x.shape[1]), x.T @ (y - intercept))
    structured = scaled[test] @ coefficients + intercept
    return np.stack((np.tile(medians, (len(test), 1)), nearest, structured), axis=1)


def train_blend(documents, features, targets, train, groups):
    """Fit each mixing weight on inner group-held-out predictions."""
    validation = np.empty((len(train), 3, targets.shape[1]))
    for inside, holdout in group_folds(groups[train], min(4, len(set(groups[train])))):
        validation[holdout] = estimates(documents, features, targets, train[inside], train[holdout])
    weights = np.array([(a / 4, b / 4, (4 - a - b) / 4) for a in range(4, -1, -1) for b in range(4 - a, -1, -1)])
    blends = np.einsum("nmd,km->nkd", validation, weights)
    errors = np.abs(blends - targets[train, None, :])
    selected = np.argmin(errors.mean(axis=0), axis=0)
    magnitudes = errors[:, selected, np.arange(targets.shape[1])]
    upper_rank = min(len(train), math.ceil(.9 * (len(train) + 1)))
    half_width = np.sort(magnitudes, axis=0)[upper_rank - 1]
    return weights[selected], half_width


def group_interval(differences: np.ndarray, groups: np.ndarray) -> list[float]:
    values = [differences[groups == key] for key in sorted(set(groups))]
    count = np.asarray([len(value) for value in values])
    sums = np.asarray([value.sum() for value in values])
    samples = np.random.default_rng(20261124).integers(len(count), size=(4000, len(count)))
    bootstraps = sums[samples].sum(axis=1) / count[samples].sum(axis=1)
    return np.quantile(bootstraps, [.025, .975]).tolist()


def metrics(actual, predicted) -> dict:
    errors = predicted - actual
    def ranks(values):
        _, groups, counts = np.unique(values, return_inverse=True, return_counts=True)
        return (counts.cumsum() - (counts - 1) / 2)[groups]

    correlation = np.corrcoef(ranks(actual), ranks(predicted))[0, 1] if np.ptp(predicted) and np.ptp(actual) else None
    return {"mae": float(np.abs(errors).mean()), "rmse": float(np.sqrt(np.mean(errors**2))),
            "bias": float(errors.mean()), "spearman": float(correlation) if correlation is not None else None}


def check_package_links(cache: Path, paths: list[str], documents: list[bytes]) -> dict:
    """Compare intact, missing and incomplete inventory evidence for the same docs."""
    cached = cache / "git-tree.json"
    if not cached.is_file():
        with urlopen(f"https://api.github.com/repos/NVIDIA/skills/git/trees/{REVISION}?recursive=1", timeout=25) as response:
            data = response.read(6_000_001)
        if len(data) > 6_000_000:
            raise ValueError("Git inventory was too large")
        cached.write_bytes(data)
    manifest = json.loads(cached.read_bytes())
    if manifest["truncated"]:
        raise ValueError("Expected a complete Git inventory")
    blobs = {entry["path"]: entry["sha"] for entry in manifest["tree"] if entry["type"] == "blob"}
    counts = Counter({"present": 0, "missing": 0})
    interventions = []
    for path, raw in zip(paths, documents, strict=True):
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if blobs.get(path) != blob:
            raise ValueError(f"Git object differs from cached source: {path}")
        prefix = PurePosixPath(path).parent.as_posix() + "/"
        inventory = [path.removeprefix(prefix) for path in blobs if path.startswith(prefix)]
        files = {"SKILL.md": raw.decode("utf-8-sig")}
        intact = inspect("study", files, inventory, True, {})["package"]["resource_links"]
        counts.update(row["state"] for row in intact)
        if not (reference := next((row for row in intact if row["state"] == "present"), None)):
            continue
        resource = reference["resource"]
        missing = [entry for entry in inventory if entry != resource and not entry.startswith(resource + "/")]
        states = []
        for complete in (True, False):
            checked = inspect("study", files, missing, complete, {})["package"]["resource_links"]
            states.append(next(row["state"] for row in checked if row["resource"] == resource))
        interventions.append({"path": path, "resource": resource, "line": reference["evidence"]["start_line"],
                              "after_removal": states[0], "in_incomplete_inventory": states[1]})
    return {"public_skills": len(paths), "intact_observations": dict(counts), "interventions": interventions,
            "removed_detected": sum(row["after_removal"] == "missing" for row in interventions),
            "incomplete_withheld": sum(row["in_incomplete_inventory"] == "unknown" for row in interventions)}


def evaluate(cache: Path) -> dict:
    published = get("benchmarks.json", cache, 4_000_000)
    if digest(published) != ARCHIVE_SHA256:
        raise ValueError("Published outcomes do not match the frozen archive")
    rows = published_rows(json.loads(published))
    paths = [f"skills/{row['catalog_dir']}/SKILL.md" for row in rows]
    with ThreadPoolExecutor(max_workers=10) as executor:
        raw = list(executor.map(lambda path: get(path, cache, 600_000), paths))
    parsed = [parse_document(item) for item in raw]
    docs = [Counter(WORD.findall("\n".join(item).casefold())) for item in parsed]
    feature_matrix = np.array([numeric_features(*item) for item in parsed])
    groups = components(rows, [item[2] for item in parsed])
    axes = [(dimension, measure) for dimension in DIMENSIONS for measure in ("with_skill", "lift")]
    target = np.array([[row["scores"][dimension][metric] for dimension, metric in axes] for row in rows])
    low = np.array([0, -1, 0, -1])
    high = np.ones(len(axes))
    predictions = np.empty((len(rows), len(METHODS), len(axes)))
    radii = np.empty_like(target)
    partition = np.empty(len(rows), dtype=int)
    folds = []
    for index, (train, test) in enumerate(group_folds(groups, 5), 1):
        base = estimates(docs, feature_matrix, target, train, test)
        weights, radii[test] = train_blend(docs, feature_matrix, target, train, groups)
        blend = np.einsum("nmd,dm->nd", base, weights)
        predictions[test] = np.clip(np.concatenate((base, blend[:, None, :]), axis=1), low, high)
        partition[test] = index
        folds.append({"id": index, "training_count": len(train), "test_count": len(test),
                      "test_groups": sorted(set(groups[test])), "blend_weights": weights.tolist()})
    summary = {}
    for i, (dimension, measure) in enumerate(axes):
        actual, blended = target[:, i], predictions[:, -1, i]
        errors = np.abs(blended - actual)
        summary[f"{dimension}/{measure}"] = {
            "methods": {key: metrics(actual, predictions[:, j, i]) for j, key in enumerate(METHODS)},
            "blend_vs_top8_group_bootstrap95": group_interval(errors - np.abs(predictions[:, 1, i] - actual), groups),
            "blend_vs_median_group_bootstrap95": group_interval(errors - np.abs(predictions[:, 0, i] - actual), groups),
            "interval_coverage": float(np.mean(errors <= radii[:, i] + 1e-9)),
            "mean_interval_width": float(np.mean(np.minimum(high[i], blended + radii[:, i])
                                                 - np.maximum(low[i], blended - radii[:, i]))),
        }
    return {
        "schema": "unseen-skill-study-v1",
        "revision": REVISION, "archive_sha256": digest(published), "agent": AGENT, "environment": ENVIRONMENT,
        "evaluator_version": SUITE, "count": len(rows), "groups": len(set(groups)), "axes": axes,
        "numpy_version": np.__version__,
        "methods": METHODS, "folds": folds, "summary": summary,
        "resource_check": check_package_links(cache, paths, raw),
        "observations": [{
            "path": paths[i], "component": row["component"], "group": str(groups[i]), "fold": int(partition[i]),
            "document_sha256": digest(raw[i]), "tasks": row["tasks"], "dataset_digest": row["dataset_digest"],
            "observed": target[i].tolist(), "prediction": predictions[i].tolist(), "blend_radius": radii[i].tolist(),
        } for i, row in enumerate(rows)],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / "tmp/quality-baselines/nvidia")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.cache)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    print(f"{report['count']} skills over {report['groups']} held-out groups")
    for key, row in report["summary"].items():
        values = ", ".join(f"{method}={100 * metric['mae']:.2f}" for method, metric in row["methods"].items())
        print(f"{key} (MAE pp): {values}")


if __name__ == "__main__":
    main()
