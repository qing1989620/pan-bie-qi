"""Replay the registered codebook experiment locally from the frozen artifacts."""

from __future__ import annotations

import json

from .adoption import CATALOG, catalog
from .inputs import digest


def artifact_failures(data: dict, artifact: dict) -> list[dict]:
    answers = artifact.get("result")
    ids = {event["id"] for event in data["events"]}
    if set(artifact) != {"result"} or not isinstance(answers, dict) or set(answers) != ids:
        return [{"rule": "exact_event_ids"}]

    failures = []
    for event in data["events"]:
        signal = event["signal"].strip().casefold()
        candidates = []
        for entry in data["codebook"]:
            if entry["stations"] is not None and event["station"] not in entry["stations"]:
                continue
            if any(alias.strip().casefold() == signal for alias in entry["aliases"]):
                candidates.append(entry["code"])
        expected = candidates[0] if len(candidates) == 1 else "UNKNOWN"
        actual = answers[event["id"]]
        if expected != actual:
            failures.append({"event": event, "eligible_codes": sorted(candidates), "expected": expected, "actual": actual})
    return failures


def replay_registered() -> dict:
    def read(name: str) -> dict | list:
        return json.loads((CATALOG.parent / name).read_bytes())

    profile = catalog()["profiles"][0]
    cases = read("codebook-cases.json")
    outcomes = read("codebook-outcomes.json")
    ablation = read("codebook-ablation.json")
    if digest(outcomes) != profile["raw_outcomes_sha256"] or digest(cases) != ablation["cases_hash"]:
        raise ValueError("Registered evidence hash differs")
    indexed = {case["id"]: case for case in cases}
    if len(indexed) != len(outcomes) or set(indexed) != {row["id"] for row in outcomes}:
        raise ValueError("Registered case roster differs")

    seen = {"no_skill": 0, "skill": 0}
    passed = dict.fromkeys(seen, 0)
    heldout = dict.fromkeys(seen, 0)
    holdout_size = agreement = 0
    for row in outcomes:
        item = indexed[row["id"]]
        if set(row["arms"]) != set(seen) or row["stratum"] != item["stratum"] or row["support"] != item["support"]:
            raise ValueError("Registered trial arms or trial partition differ")
        holdout_size += not item["support"]
        for arm, record in row["arms"].items():
            artifact = record["artifact"]
            reference = set(artifact) == {"result"} and artifact.get("result") == item["gold_by_construction"]
            accepted = not artifact_failures(item["data"], artifact)
            if reference != record["accepted"]:
                raise ValueError("Registered reference label differs")
            agreement += accepted == reference
            seen[arm] += 1
            passed[arm] += accepted
            heldout[arm] += accepted and not item["support"]
    return {"artifacts": sum(seen.values()), "correct": agreement, "pairs": len(outcomes), "passed": passed,
            "holdout": {"pairs": holdout_size, "passed": heldout},
            "artifacts_sha256": profile["raw_outcomes_sha256"], "new_model_api_calls": 0, "new_skill_executions": 0}


if __name__ == "__main__":
    print(json.dumps(replay_registered(), ensure_ascii=False, indent=2))
