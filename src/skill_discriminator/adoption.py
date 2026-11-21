"""Attach independently measured adoption evidence to an exact linked snapshot."""
from __future__ import annotations

import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parent / "data/paired-evidence.json"
UTILITY_STUDY = CATALOG.with_name("utility-study.json")


def catalog() -> dict:
    try:
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
        if payload.get("schema") == "skill-adoption-evidence-v1":
            return payload
    except (OSError, ValueError):
        pass
    return {}


def utility_study() -> dict:
    try:
        data = json.loads(UTILITY_STUDY.read_bytes())
        if data.get("schema") == "skill-utility-comparison-v1":
            return data
    except (OSError, ValueError):
        pass
    return {}


def attach(report: dict, source: dict) -> None:
    """Evidence comes only from the local research catalog, not the submitted Skill."""
    measured = source.get("file_hashes", {}).get("SKILL.md", {}).get("raw_bytes_sha256")
    binding = (source.get("repository", "").casefold(), source.get("commit"), source.get("skill_path"), measured)
    for profile in catalog().get("profiles", []):
        expected = (profile["repository"].casefold(), profile["commit"], profile["skill_path"], profile["skill_doc_sha256"])
        if binding != expected:
            continue
        if measured != report["package"]["file_sha256"].get("SKILL.md"):
            continue
        report["verified_adoption"] = {"state": "measured_on_registered_inputs", **profile}
        evidence = report.get("evidence_assessment", {})
        result = profile["result"]
        holdout = profile.get("holdout")
        if holdout and holdout["pairs"]:
            difference = holdout["with_passed"] - holdout["without_passed"]
            if difference > 0:
                observation = "留出任务中观察到净增益"
            elif difference < 0:
                observation = "留出任务中观察到净回退"
            else:
                observation = "留出任务中未观察到净增益"
            report["verified_adoption"]["holdout_observation_zh"] = observation
        if report["decision"] == "inspection_complete":
            report.update(decision="paired_evidence_available", decision_zh="找到此版本的真实测试记录",
                          reason=profile["assessment"])
        evidence.get("decision_trace", []).append({
            "rule": "paired_evidence_exact_version",
            "effect": "report_observed_difference",
            "reason": "仓库、提交、技能路径和文档指纹均与测试记录一致。",
            "evidence": {"source": profile["records"], "receipt_id": profile["raw_outcomes_sha256"]},
        })
        for criterion in evidence.get("criteria", []):
            if criterion["id"] == "functional_correctness" and not report["tested_evidence"]:
                criterion.update(
                    state="tested_within_scope", display=f"读入说明后 {result['with_passed']}/{result['pairs']} 组输入通过",
                    reason="按登记任务规则验收已生成的模型产物。",
                    formula="读入 Skill 的有效且正确产物数 / 实测输入数。",
                    scope=profile["scope"], next_action="查看分组结果和具体反例。",
                    evidence=[{"source": profile["records"], "receipt_id": profile["raw_outcomes_sha256"]}],
                    numerator=result["with_passed"], denominator=result["pairs"],
                    observed_rate=result["with_passed"]/result["pairs"],
                )
            if criterion["id"] != "marginal_utility":
                continue
            criterion.update(
                state="paired_observation",
                display=f"独立验收通过数 {result['without_passed']}/{result['pairs']} → {result['with_passed']}/{result['pairs']}",
                reason="同一模型对相同输入分别读取和不读取这份 SKILL.md。验收器逐条核对工位、别名和歧义。",
                formula="收益 = (有 Skill 通过数 − 无 Skill 通过数) / 配对输入数。一起报告改善、回退和留出结果。",
                evidence=[{"source": profile["records"], "receipt_id": profile["raw_outcomes_sha256"]}],
                scope=profile["scope"],
                next_action="查看各类输入与未通过样本。换任务、模型、宿主或版本后重新测量。",
                utility_gain=result["net_gain"],
                case_count=result["pairs"],
                confidence_interval=profile["gain_bootstrap95"],
            )
        return
    report["verified_adoption"] = {
        "state": "no_matching_record",
        "reason": "未找到与此仓库、提交、技能路径和文档指纹匹配的配对记录。",
        "utility_gain": None,
    }
