"""Bind author-published paired results to the matching public Skill snapshot."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path, PurePosixPath

CATALOG = Path(__file__).resolve().parent / "data/unseen-skill-study.json"
REPOSITORY = "NVIDIA/skills"
TITLES = {"Correctness": "正确性", "Effectiveness": "任务效果"}


@lru_cache(maxsize=1)
def benchmark_index() -> tuple[dict, dict[str, dict]]:
    try:
        data = json.loads(CATALOG.read_bytes())
        if data.get("schema") == "unseen-skill-study-v1":
            return data, {row["path"]: row for row in data["observations"]}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {}, {}


def attach(report: dict, source: dict) -> None:
    if source.get("repository", "").casefold() != REPOSITORY.casefold():
        return
    data, index = benchmark_index()
    if source.get("commit") != data.get("revision"):
        return
    path = source.get("skill_path")
    observed = index.get(path)
    if not observed:
        return
    fetched = source.get("file_hashes", {}).get("SKILL.md", {}).get("raw_bytes_sha256")
    analyzed = report["package"]["file_sha256"].get("SKILL.md")
    if fetched != observed["document_sha256"] or analyzed != fetched:
        return

    recorded = {tuple(axis): value for axis, value in zip(data["axes"], observed["observed"], strict=True)}
    measures = []
    for dimension, title in TITLES.items():
        score, gain = recorded[(dimension, "with_skill")], recorded[(dimension, "lift")]
        measures.append({"dimension": dimension, "label": title, "with_skill": round(score, 8),
                         "without_skill": round(score - gain, 8), "lift": round(gain, 8)})

    card = PurePosixPath(path).with_name("BENCHMARK.md").as_posix()
    scope = (f"NVIDIA 发布的 {data['agent']} 配对评测，{data['environment']}，共 {observed['tasks']} 个任务。"
             "结果与本次所读说明来自同一固定发布快照，评分口径见原始报告。")
    profile = report["skill_profile"]
    profile["public_benchmark"] = {
        "state": "published_evidence", "provider": "NVIDIA SkillEvaluator",
        "url": f"https://github.com/{REPOSITORY}/blob/{data['revision']}/{card}",
        "tasks": observed["tasks"], "dataset_digest": observed["dataset_digest"],
        "evaluator_version": data["evaluator_version"], "agent": data["agent"],
        "environment": data["environment"], "scope": scope, "measures": measures,
    }
    if profile["functional_quality"]["state"] == "unverified":
        profile["functional_quality"] = {
            "state": "published_benchmark", "title": "已有公开功能评测",
            "reason": "可查看有无 Skill 的得分、净增益和作者发布的验收报告。", "scope": None,
        }
    evidence = report.get("evidence_assessment", {})
    receipt = {"source": profile["public_benchmark"]["url"], "receipt_id": observed["dataset_digest"]}
    for criterion in evidence.get("criteria", ()):
        if criterion["id"] == "functional_correctness":
            criterion.update(state="published_benchmark", display=f"公开配对评测中，有 Skill 的正确性得分 {100 * measures[0]['with_skill']:.1f}%",
                             reason="得分由作者在自己的任务集上发布。", formula="沿用原报告的 Correctness 指标。",
                             observed_score=measures[0]["with_skill"], scope=scope, evidence=[receipt])
        if criterion["id"] == "marginal_utility":
            criterion.update(state="published_benchmark", display=f"公开配对评测的任务效果变化 {100 * measures[1]['lift']:+.1f} 个百分点",
                             reason="在原报告所列的配对条件下计算差值。", formula="Effectiveness 得分（有 Skill）减去得分（无 Skill）。",
                             published_effectiveness_lift=measures[1]["lift"], scope=scope, evidence=[receipt])
