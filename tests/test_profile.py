"""Check that one link gets a readable profile and only its own measured results."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from skill_discriminator import profile, source_import
from skill_discriminator.adoption import catalog
from skill_discriminator.api import create_app
from skill_discriminator.service import assess

PORTAL = "/api/discriminator"
DOCUMENT = """---
name: pdf-example
description: 读取 PDF 文档并提取文字。
---
# PDF 示例

输入为普通 PDF。输出为 TXT。
1. 提取文字，然后核对页数。
本程序不支持扫描件 OCR。
```markdown
## Extract Tables
This text is an example, not a declaration.
```
"""


def test_local_profile_uses_unicode_and_excludes_negative_claims_and_snippets():
    document = DOCUMENT.replace("\n", "\r\n") + "🍐"
    with TestClient(create_app()) as client:
        response = client.post(PORTAL + "/inspect", json={"files": {"SKILL.md": document}})
    assert response.status_code == 200
    view = response.json()["skill_profile"]
    assert view["size"]["document_characters"] == len(document)
    assert view["size"]["document_utf8_bytes"] == len(document.encode())
    assert view["size"]["body_characters"] < len(document)
    assert {row["id"] for row in view["capabilities"]} == {"text"}
    assert "不支持" in view["documented_limits"][0]["quote"]
    assert view["functional_quality"]["state"] == "unverified"
    assert view["measurements"] is None
    assert len(view["comparison"]["peers"]) >= 2
    assert all(entry["evidence"]["start_line"] for entry in view["documentation"]["checks"] if entry["found"])


def test_static_package_failure_dominates_in_profile():
    with TestClient(create_app()) as client:
        response = client.post(PORTAL + "/inspect", json={"files": {
            "SKILL.md": DOCUMENT, "scripts/example.py": "for x\n pass",
        }})
    assert response.status_code == 200
    assert response.json()["skill_profile"]["functional_quality"]["state"] == "blocked"


@pytest.mark.parametrize("member", [None, "repository", "skill_path", "commit", "sha256", "analyzed"])
def test_pdf_measurements_remain_bound_to_exact_source(member):
    peer = next(row for row in profile.reference_catalog()["peers"] if row["name"] == "Anthropic PDF")
    report = assess("generic", {"SKILL.md": DOCUMENT}, [], False, "", {})
    package = report["package"]
    package["file_sha256"]["SKILL.md"] = peer["sha256"]
    source = {key: peer[key] for key in ("repository", "skill_path", "commit")}
    source["file_hashes"] = {"SKILL.md": {"raw_bytes_sha256": peer["sha256"]}}
    if member in {"repository", "skill_path", "commit"}:
        source[member] += "-changed"
    elif member == "sha256":
        source["file_hashes"]["SKILL.md"]["raw_bytes_sha256"] = "0" * 64
    elif member == "analyzed":
        package["file_sha256"]["SKILL.md"] = "0" * 64

    view = report["skill_profile"]
    profile.attach(view, report, source)

    assert (view["measurements"] is not None) is (member is None)
    if member is None:
        assert view["functional_quality"]["state"] == "workflow_measured"
        assert any(row["id"] == "table" and row["workflow_observation"]["passed"] == 2
                   for row in view["capabilities"])


def test_codebook_has_three_distinct_quality_conclusions(monkeypatch):
    evidence = catalog()["profiles"][0]
    package_doc = Path(source_import.__file__).parent / "data/manufacturing-codebook-document.md"
    source = {key: evidence[key] for key in ("repository", "skill_path", "commit")}
    source["file_hashes"] = {"SKILL.md": {"raw_bytes_sha256": evidence["skill_doc_sha256"]}}
    mocked = {"state": "imported", "source": source, "package": {
        "identity": "codebook", "files": {"SKILL.md": package_doc.read_bytes().decode()},
        "inventory": ["SKILL.md"], "inventory_complete": True,
    }}
    monkeypatch.setattr(source_import, "import_link", lambda *_: mocked)
    with TestClient(create_app()) as client:
        response = client.post(PORTAL + "/import-url", json={"url": evidence["url"]})
    assert response.status_code == 200
    view = response.json()["report"]["skill_profile"]
    assert view["functional_quality"]["state"] == "conditional"
    assert "8/16" in view["functional_quality"]["reason"]
    results = {row["id"]: row["observation"]["passed"] for row in view["capabilities"] if "observation" in row}
    assert results == {"normalize": 8, "station": 8, "ambiguity": 0, "unlisted": 0}
    assert view["measurements"] is None
