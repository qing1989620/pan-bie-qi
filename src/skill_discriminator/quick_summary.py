"""Plain-language conclusions grounded in the detailed Skill report."""

from __future__ import annotations

import re


def _short(text: str, limit: int = 150) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    boundary = max(text.rfind(mark, 0, limit) for mark in "。！？")
    return text[:boundary + 1] if boundary >= 20 else text[:limit].rstrip() + "…"


def _purpose(profile: dict) -> str:
    description = profile["description"]
    if "编程" in description and "工作流" in description and "任务分类" in description and "质量门禁" in description:
        return "指导 AI 按流程处理软件开发任务。先识别任务类型，再安排执行和验收。"
    actions = [item["title"] for item in profile["capabilities"] if item["state"] in {"declared", "measured_workflow"}
               and item["id"] != "purpose"]
    if actions:
        return "按说明，可以" + "、".join(actions[:3]) + "。"
    first_sentence = re.split(r"(?<=[。！？])|(?<=[.!?])\s+", description.strip(), maxsplit=1)[0]
    if not first_sentence or first_sentence == "未提供单独说明":
        return "当前材料里没有找到清楚的用途说明。"
    return _short(first_sentence)


def _paired_reason(report: dict, fallback: str) -> str:
    strata = [row for row in report.get("verified_adoption", {}).get("strata", []) if row["pairs"]]
    if not strata:
        return fallback
    best = max(strata, key=lambda row: row["with_passed"] / row["pairs"])
    worst = min(strata, key=lambda row: row["with_passed"] / row["pairs"])
    if best["with_passed"] * worst["pairs"] <= worst["with_passed"] * best["pairs"]:
        return fallback
    return "".join(f"{row['title']}通过 {row['with_passed']}/{row['pairs']} 项。" for row in (best, worst))


def _quality(report: dict, profile: dict) -> tuple[str, str, str]:
    quality = profile["functional_quality"]
    state = quality["state"]
    if state == "blocked":
        return "先修复再用", quality["reason"], "warning"
    if state == "environment_blocked":
        return "需要先配置", quality["reason"], "warning"
    if report["decision"] == "task_contract_review":
        return "先核对任务要求", report["task_contract_review"][0]["explanation"], "mixed"
    if state == "strong":
        return "已测任务表现好", quality["reason"], "verified"
    if state == "conditional":
        return "只适合部分任务", _paired_reason(report, quality["reason"]), "mixed"
    if state == "weak":
        return "已测任务表现欠佳", quality["reason"], "warning"
    if state == "workflow_measured":
        observations = [(ability["title"], ability["workflow_observation"]) for ability in profile["capabilities"]
                        if "workflow_observation" in ability]
        reason = "、".join(f"{title}通过 {result['passed']}/{result['total']} 项" for title, result in observations[:2])
        return "部分步骤已试跑", f"{reason}。这里覆盖公开说明里的步骤。" if reason else quality["reason"], "mixed"
    if state == "published_benchmark":
        proof = profile["public_benchmark"]
        measures = proof["measures"]
        lowest = min(measures, key=lambda row: row["lift"])
        metric = lowest if lowest["lift"] < 0 else max(reversed(measures), key=lambda row: row["lift"])
        if metric["lift"] > 0:
            label, tone = "已测得分有提升", "verified"
        elif metric["lift"] < 0:
            label, tone = "已测得分有下降", "mixed"
        else:
            label, tone = "已测未显示提升", "neutral"
        reason = (f"作者公开的 {proof['tasks']} 个任务中，{metric['label']}得分"
                  f"从 {metric['without_skill']:.1%} 到 {metric['with_skill']:.1%}。")
        return label, reason, tone

    covered = {item["id"] for item in profile["documentation"]["checks"] if item["found"]}
    reason = "文档给出了流程和验收方法。当前版本缺少任务实测。" if {"steps", "verification"} <= covered else "当前版本缺少可核查的任务实测。"
    return "效果待验证", reason, "neutral"


def summarize(report: dict) -> dict:
    profile = report["skill_profile"]
    title, reason, tone = _quality(report, profile)
    return {
        "name": profile["name"],
        "purpose": _purpose(profile),
        "verdict": title,
        "reason": _short(reason),
        "tone": tone,
    }
