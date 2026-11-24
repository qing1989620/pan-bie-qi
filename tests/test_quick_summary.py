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
    assert summary["verdict"] == "效果待验证"
    assert "验收" in summary["reason"]
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


def test_registered_comparison_uses_measured_failure_bounds():
    url = adoption.catalog()["profiles"][0]["url"]
    with TestClient(create_app()) as client:
        response = client.post("/api/discriminator/import-url", json={"url": url, "prefer_main": True})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["quick_summary"]["verdict"] == "只适合部分任务"
    assert "8/8" in report["quick_summary"]["reason"]
    assert "0/8" in report["quick_summary"]["reason"]
