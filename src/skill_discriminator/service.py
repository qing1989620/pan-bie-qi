"""Build an assessment from package observations and matching measured evidence."""

from __future__ import annotations

import hashlib
import time

from . import __version__
from .assurance import static_capabilities
from .contracts import check as contract_review
from .explanation import build
from .inspection import clean, inspect
from .profile import describe
from .task_guidance import pdf_guidance


def assess(
    identity: str, files: dict[str, str], inventory: list[str], complete: bool, task: str, environment: dict
) -> dict:
    started = time.perf_counter()
    report = inspect(identity, files, inventory, complete, environment)
    report["capability_analysis"] = static_capabilities(files)
    report["task_contract_review"] = contract_review(task, files["SKILL.md"], files) if task.strip() else []
    report["task_guidance"] = pdf_guidance(task, files, report["third_party_imports"])
    report["tested_evidence"] = None

    if report["package"]["findings"]:
        verdict = ("repair_required", "先修复已确认的问题", "发现可定位的文件、语法或接口问题。")
    elif any(c["state"] == "unmet" for c in report["conditions"]):
        verdict = ("configuration_required", "当前运行条件不满足", "已声明的配置或联网条件尚不具备。")
    elif report["task_contract_review"]:
        verdict = ("task_contract_review", "先核对任务与实现", "检查到具体声明或实现与所填任务的差异。")
    else:
        verdict = ("inspection_complete", "已完成技能包检查", "已检查可读取的材料。功能效果需要配对测试记录。")

    decision, title, reason = verdict
    submitted = bool(task.strip())
    report.update(
        version=__version__, decision=decision, decision_zh=title, reason=reason,
        task_sha256=hashlib.sha256(task.encode()).hexdigest() if submitted else None,
        task_fit={"state": "current_task_unverified" if submitted else "task_not_provided"},
        current_task={
            "state": "unverified" if submitted else "not_provided",
            "title": "当前任务待验证" if submitted else "当前任务未指定",
            "reason": "提交了任务描述。请用相应输入和验收条件做一次验证。" if submitted else "可补充任务，用于确定验证范围。",
        },
        new_model_api_calls=0, new_skill_executions=0,
        environment_provenance="运行条件由用户提供。",
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
    )
    report["evidence_assessment"] = build(report, files)
    report["skill_profile"] = describe(files, report["introduction"], report["package"])
    return clean(report)
