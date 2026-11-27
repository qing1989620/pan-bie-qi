"""Recompute paired execution evidence and test PT's set selector under a fixed budget."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import time
from collections import defaultdict
from pathlib import Path

from skill_discriminator.pt import complete_coverage

SKILLSBENCH_REVISION = "f104580363a9642563593c475620196ecd36687d"


def paired_outcomes(path: Path):
    payload = path.read_bytes()
    selected = json.loads(payload)["selected_cells"]
    cells = defaultdict(lambda: defaultdict(list))
    for trial in selected:
        group = trial["group"].split("__", 1)[0]
        harness = group.removesuffix("-with-skills").removesuffix("-no-skills")
        if trial["mode"] in {"with", "without"} and not trial["timeout"]:
            cells[(harness, trial["model"], trial["task"])][trial["mode"]].append(trial["reward"])
    comparisons = []
    for (harness, model, task), arms in sorted(cells.items()):
        if not all(mode in arms for mode in ("with", "without")):
            continue
        baseline = statistics.mean(arms["without"])
        enabled = statistics.mean(arms["with"])
        comparisons.append((task, harness, model, baseline, enabled))
    model_summaries = {}
    for model in sorted({row[2] for row in comparisons}):
        model_summaries[model] = _metrics([row for row in comparisons if row[2] == model])
    # Keep paired rows from the same test task together when estimating the interval.
    by_task = defaultdict(list)
    for comparison in comparisons:
        by_task[comparison[0]].append(comparison[4] - comparison[3])
    sampled = list(by_task.values())
    rng = random.Random(317)
    replicates = sorted(statistics.mean(value for task in rng.choices(sampled, k=len(sampled)) for value in task)
                        for _ in range(2000))
    return {
        "revision": SKILLSBENCH_REVISION,
        "manifest_sha256": hashlib.sha256(payload).hexdigest(),
        "selected_trials": len(selected),
        "matched_task_model_harness_cells": _metrics(comparisons),
        "model_breakdown": model_summaries,
        "cross_model_transfer": cross_model_transfer(comparisons),
        "task_cluster_bootstrap_95": [replicates[50], replicates[1949]],
        "repeated_trials": "Select official manifest rows, average rewards within each condition at the same task, model and harness.",
        "source": (
            "https://huggingface.co/datasets/benchflow/skillsbench-leaderboard/blob/"
            + SKILLSBENCH_REVISION + "/analysis/experiment_final_selected_manifest.json"
        ),
    }


def cross_model_transfer(comparisons):
    by_model = defaultdict(dict)
    for task, harness, model, baseline, enabled in comparisons:
        by_model[model][(task, harness)] = (baseline, enabled)
    results = []
    for reference in sorted(by_model):
        for target in sorted(set(by_model) - {reference}):
            known, held = by_model[reference], by_model[target]
            shared = sorted(known.keys() & held.keys())
            if not shared:
                continue
            chosen = {key for key in shared if known[key][1] > known[key][0]}
            results.append({
                "reference_model": reference, "target_model": target, "matched_tasks": len(shared),
                "adoptions_from_positive_reference": len(chosen),
                "adoptions_with_target_regression": sum(held[key][1] < held[key][0] for key in chosen),
                "adoptions_with_target_gain": sum(held[key][1] > held[key][0] for key in chosen),
                "target_without_skill": statistics.mean(held[key][0] for key in shared),
                "target_always_with_skill": statistics.mean(held[key][1] for key in shared),
                "target_reference_gated": statistics.mean(held[key][int(key in chosen)] for key in shared),
            })
    return results


def _metrics(rows):
    effects = [round(with_skill - baseline, 9) for _, _, _, baseline, with_skill in rows]
    return {
        "matched_cells": len(rows), "tasks": len({row[0] for row in rows}),
        "without_skill_mean": statistics.mean(row[3] for row in rows),
        "with_skill_mean": statistics.mean(row[4] for row in rows),
        "paired_delta": statistics.mean(effects),
        "improved": sum(gain > 0 for gain in effects),
        "degraded": sum(gain < 0 for gain in effects),
        "tied": sum(gain == 0 for gain in effects),
    }


def greedy(supported):
    covered, ranked = set(), []
    remaining = list(range(len(supported)))
    while remaining:
        number = min(remaining, key=lambda i: (-len(supported[i] - covered), -len(supported[i]), i))
        covered.update(supported[number])
        ranked.append(number)
        remaining.remove(number)
    return ranked


def coverage_trials():
    rng = random.Random(581)
    trials = 0
    results = {}
    for limit in (2, 3, 4):
        coverage_old, coverage_new, improved, timings = [], [], 0, []
        for probability in (.16, .28, .44):
            for _ in range(600):
                reqs = tuple(map(str, range(rng.randint(5, 14))))
                supported = [{key for key in reqs if rng.random() < probability} for _ in range(10)]
                ranked = greedy(supported)
                start = time.perf_counter()
                selected = complete_coverage(supported, ranked, limit)
                timings.append((time.perf_counter() - start) * 1000)
                prior = len(set().union(*(supported[i] for i in ranked[:limit])))
                current = len(set().union(*(supported[i] for i in selected)))
                if current < prior:
                    raise AssertionError("Exact selector lost coverage")
                coverage_old.append(prior)
                coverage_new.append(current)
                improved += current > prior
                trials += 1
        timings.sort()
        results[str(limit)] = {
            "trials": len(coverage_old), "improved": improved, "degraded": 0,
            "mean_greedy_coverage": statistics.mean(coverage_old),
            "mean_exact_coverage": statistics.mean(coverage_new),
            "p95_exact_time_ms": timings[round(len(timings) * .95) - 1],
        }
    return {"synthetic_trials": trials, "candidate_count": 10, "seed": 581, "by_limit": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = {"schema": "pt-2026-external-evidence-and-coverage-v1",
              "paired_skillsbench": paired_outcomes(args.manifest), "selection": coverage_trials()}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
