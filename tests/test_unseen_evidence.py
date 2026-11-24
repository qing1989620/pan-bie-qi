"""Check that newly supplied documents get bounded, reproducible evaluations."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from skill_discriminator.api import create_app
from skill_discriminator.published_benchmarks import attach, benchmark_index

DOCUMENT = """---
name: format-reminders
description: Create plain text reminders from task entries.
---
# Formatter

Use the [format specification](references/format.md#syntax).
The [default template](<templates/basic%20reminder.txt>) sets the layout.
See [external help](https://example.test/references/help.md).

```markdown
[an example link](references/placeholder.md)
```
"""


@pytest.mark.parametrize("complete", [False, True])
def test_unknown_bundle_link_only_confirms_missing_with_full_inventory(complete):
    with TestClient(create_app()) as client:
        result = client.post("/api/discriminator/inspect", json={
            "files": {"SKILL.md": DOCUMENT}, "inventory": ["SKILL.md", "references/format.md"],
            "inventory_complete": complete,
        })
    assert result.status_code == 200
    report = result.json()
    records = report["package"]["resource_links"]
    assert [(row["resource"], row["state"]) for row in records] == [
        ("references/format.md", "present"), ("templates/basic reminder.txt", "missing" if complete else "unknown"),
    ]
    assert records[1]["evidence"]["start_line"] == 8
    assert report["skill_profile"]["functional_quality"]["state"] == ("blocked" if complete else "unverified")


@pytest.mark.parametrize("change", [None, "commit", "repository", "path", "raw", "text"])
def test_published_benchmark_is_bound_to_public_snapshot(change):
    dataset, index = benchmark_index()
    path = "skills/accelerated-computing-cudf/SKILL.md"
    expected = index[path]["document_sha256"]
    source = {"repository": "NVIDIA/skills", "commit": dataset["revision"], "skill_path": path,
              "file_hashes": {"SKILL.md": {"raw_bytes_sha256": expected}}}
    report = {"package": {"file_sha256": {"SKILL.md": expected}},
              "skill_profile": {"functional_quality": {"state": "unverified"}}}
    if change in {"commit", "repository"}:
        source[change] += "-changed"
    elif change == "path":
        source["skill_path"] = str(Path("other") / path)
    elif change == "raw":
        source["file_hashes"]["SKILL.md"]["raw_bytes_sha256"] = "0" * 64
    elif change == "text":
        report["package"]["file_sha256"]["SKILL.md"] = "0" * 64
    attach(report, source)
    profile = report["skill_profile"]
    assert ("public_benchmark" in profile) is (change is None)
    if change is None:
        assert profile["functional_quality"]["state"] == "published_benchmark"
        assert profile["public_benchmark"]["measures"] == [
            {"dimension": "Correctness", "label": "正确性", "with_skill": 1, "without_skill": 1, "lift": 0},
            {"dimension": "Effectiveness", "label": "任务效果", "with_skill": .921, "without_skill": .885, "lift": .036},
        ]
