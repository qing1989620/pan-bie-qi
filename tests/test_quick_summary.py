"""Cover exact conclusions and scope shown in the simple view."""

from fastapi.testclient import TestClient

from skill_discriminator import adoption
from skill_discriminator.api import create_app

WORKFLOW = """---
name: ai-engineering-workflow
description: >
  AI 编程工作流。当用户要求新增功能时触发。
  先做任务分类，再通过质量门禁。不触发于纯问答。
---
# 开发工作流

1. 识别任务。
2. 用验收办法检验结果。
"""


def test_simple_view_route_and_folded_language_evidence():
    with TestClient(create_app()) as client:
        view = client.get("/user")
        assert view.status_code == 200 and 'id="quick-result"' in view.text
        assert 'id="report-region"' in client.get("/").text
        report = client.post("/api/discriminator/inspect", json={"files": {"SKILL.md": WORKFLOW}}).json()
    summary = report["quick_summary"]
    assert "软件开发任务" in summary["purpose"]
    assert summary["verdict"] == "可按说明初筛"
    assert "运行准备" in summary["reason"]
    assessment = report["assessment"]
    assert assessment["agent"]["status"] == "runtime_review_required"
    assert not assessment["agent"]["automatic_execution_authorized"]
    scores = {row["id"]: row for row in assessment["dimensions"]}
    assert scores["scope"]["score"] == 75
    assert scores["scope"]["checks"][1]["evidence"]["start_line"] == 4
    assert scores["scope"]["checks"][3]["evidence"] is None
    assert scores["task"]["score"] is None
    checks = {check["id"]: check for check in report["skill_profile"]["documentation"]["checks"]}
    assert checks["input"]["evidence"]["start_line"] == 4
    assert report["introduction"]["declared_exclusions"][0]["start_line"] == 5


def test_confirmed_missing_resource_gets_a_short_actionable_verdict():
    document = WORKFLOW + "\n使用[模板](templates/example.md)。\n"
    with TestClient(create_app()) as client:
        report = client.post("/api/discriminator/inspect", json={
            "files": {"SKILL.md": document}, "inventory_complete": True,
        }).json()
    assert report["quick_summary"]["verdict"] == "先修复再用"
    assert "templates/example.md" in report["quick_summary"]["reason"]
    assert report["assessment"]["agent"]["status"] == "repair_required"
    assert report["assessment"]["dimensions"][-2]["score"] == 0


def test_unmatched_inline_path_and_empty_heading_earn_no_credit():
    doc = "# Skill\n\n## Installation\n\n## Verification\nTODO. To be documented.\n\n## Notes\nUse \x60references/file.md\x60."
    with TestClient(create_app()) as client:
        report = client.post("/api/discriminator/inspect", json={
            "files": {"SKILL.md": doc}, "inventory_complete": True,
        }).json()
    scores = {row["id"]: row for row in report["assessment"]["dimensions"]}
    assert scores["setup"]["score"] == 25  # The guide names a resource even though its location is unverified.
    assert scores["acceptance"]["score"] == 0
    assert scores["package"]["score"] is None
    assert scores["package"]["counts"] == {"passed": 0, "failed": 0, "unknown": 1}
    assert report["assessment"]["agent"]["status"] == "runtime_review_required"


def test_registered_comparison_uses_measured_failure_bounds():
    url = adoption.catalog()["profiles"][0]["url"]
    with TestClient(create_app()) as client:
        response = client.post("/api/discriminator/import-url", json={"url": url, "prefer_main": True})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["quick_summary"]["verdict"] == "只适合部分任务"
    assert "8/8" in report["quick_summary"]["reason"]
    assert "0/8" in report["quick_summary"]["reason"]
    task = report["assessment"]["dimensions"][-1]
    assert (task["score"], task["sample_count"], task["lift"]) == (50, 16, 0)
    assert report["assessment"]["agent"]["status"] == "measured_scope_available"
