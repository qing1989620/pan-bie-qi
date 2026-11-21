"""Version binding regressions for the user-facing link report."""
import hashlib
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from skill_discriminator import adoption
from skill_discriminator.api import create_app


@pytest.fixture
def frozen():
    body = "# Basic test skill\nThis source was covered by a local experiment."
    digest = hashlib.sha256(body.encode()).hexdigest()
    source = {"repository": "test/skill", "commit": "a"*40, "skill_path": "SKILL.md",
              "file_hashes": {"SKILL.md": {"raw_bytes_sha256": digest}}, "canonical_url": "https://github.com/test/skill"}
    package = {"identity": "test", "files": {"SKILL.md": body}, "inventory": ["SKILL.md"], "inventory_complete": True}
    profile = {"repository": source["repository"], "commit": source["commit"], "skill_path": source["skill_path"],
               "skill_doc_sha256": digest, "result": {"without_passed": 2, "with_passed": 3, "pairs": 4, "net_gain": .25},
               "records": "test-evidence.json", "raw_outcomes_sha256": "z"*64, "scope": "Only this test profile",
               "gain_bootstrap95": [0,.5], "assessment": "One of the known probes benefited."}
    return source, package, profile


def test_link_api_attaches_its_version_matched_observed_gain(monkeypatch, frozen):
    source, package, profile = frozen
    profile["holdout"] = {"without_passed": 1, "with_passed": 1, "pairs": 2, "beneficial": 1, "harmful": 1}
    monkeypatch.setattr(adoption, "catalog", lambda: {"profiles": [profile]})
    monkeypatch.setattr("skill_discriminator.source_import.import_link", lambda *args: {
        "state": "imported", "source": source, "package": package})
    with TestClient(create_app()) as session:
        result = session.post("/api/discriminator/import-url", json={"url": source["canonical_url"]})
    assert result.status_code == 200
    report = result.json()["report"]
    assert report["verified_adoption"]["state"] == "measured_on_registered_inputs"
    assert report["verified_adoption"]["holdout_observation_zh"] == "留出任务中未观察到净增益"
    assert report["decision"] == "paired_evidence_available"
    assert report["evidence_assessment"]["overall_score"] is None
    metric = next(x for x in report["evidence_assessment"]["criteria"] if x["id"] == "marginal_utility")
    assert metric["utility_gain"] == .25 and metric["case_count"] == 4
    function = next(x for x in report["evidence_assessment"]["criteria"] if x["id"] == "functional_correctness")
    assert (function["numerator"], function["denominator"]) == (3,4)
    assert result.json()["model_api_calls"] == 0 and not result.json()["skill_code_executed"]


@pytest.mark.parametrize("mismatch", ["commit", "repository", "skill_path", "bytes"])
def test_modified_source_cannot_inherit_another_snapshot_result(monkeypatch, frozen, mismatch):
    source, _package, profile = frozen
    monkeypatch.setattr(adoption, "catalog", lambda: {"profiles": [profile]})
    copied = deepcopy(source)
    if mismatch == "bytes":
        copied["file_hashes"]["SKILL.md"]["raw_bytes_sha256"] = "b"*64
    else:
        copied[mismatch] += "new"
    report = {"package": {"file_sha256": {"SKILL.md": profile["skill_doc_sha256"]}}}
    adoption.attach(report, copied)
    assert report["verified_adoption"]["state"] == "no_matching_record"
    assert report["verified_adoption"]["utility_gain"] is None
