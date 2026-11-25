"""Score traceable evidence for one Skill version."""

from __future__ import annotations

import re
from itertools import pairwise

from .inspection import (
    DECLARED_EXCLUSION,
    citation,
    clean,
    description_lines,
    frontmatter,
)
from .profile import HEADER, HEADING, prose_lines

RUBRIC_VERSION = "skill-evidence-v1"
AXES = {
    "scope": "用途与边界",
    "workflow": "流程与产出",
    "setup": "运行准备",
    "acceptance": "验收与异常",
}

# A section title alone does not earn credit.
RULES = {
    "scope": (
        ("trigger", "触发条件或具体输入", r"\b(?:use|trigger|activate) (?:this skill )?(?:when|for) .{4,}|\bwhen (?:the user|you) .{4,}|当用户(?:说|要求|请求|询问)|(?:适用场景|触发条件|输入)\s*[:：]\s*\S",
         r"when to use|use cases?|inputs?|适用场景|触发条件|输入"),
        ("boundary", "不适用范围", DECLARED_EXCLUSION + r"|\b(?:unsupported|out of scope for) [\w.-]+|不触发于\S|仅支持\S",
         r"limitations?|when not to use|out of scope|限制|不适用|边界"),
        ("example", "使用示例", r"\b(?:example|sample (?:input|task))\s*[:：]\s*\S|(?:示例|例子)\s*[:：]\s*\S",
         r"(?:usage |worked )?examples?|使用示例|示例"),
    ),
    "workflow": (
        ("output", "可交付的结果",
         r"\b(?:output|deliverable)s?\s*[:：]\s*\S|\b(?:return|export|save|produce|write)s?\s+(?:a |the )?[\w./-]+[ .:/\w-]{3,}|(?:输出|交付|生成)\s*(?:[:：]\s*)?[\x60\w][^\n]{2,}",
         r"outputs?|deliverables?|产出|输出|交付物|完成报告"),
        ("operation", "操作动作或材料",
         r"\b(?:run|execute|open|load|call)\s+\x60[^\x60\n]+\x60|(?:使用|加载|读取|运行|执行|调用|打开)\s*\x60[^\x60\n]+\x60",
         r"quick start|(?:installation and )?usage|how to (?:run|use)|快速开始|使用方法"),
        ("branch", "流程分支",
         r"\bif .{4,100}(?:,\s*|:\s*|\bthen\b)\S|\b(?:otherwise|on success)\s*[,：:]\s*\S|(?:若|如果|否则|仅当|当.{1,70}时).{3,100}(?:[，：→]|则|必须|跳过|转入)|(?:跳过|转入|改用)\s+\S",
         r"decision tree|conditional steps?|分支条件|任务路由"),
    ),
    "setup": (
        ("requirements", "依赖或免依赖声明",
         r"\b(?:dependencies|prerequisites?|requirements?)\s*[:：]\s*\S|\brequires? (?:a |an |the )?[\w.\x60-]+(?: [0-9][\w.-]*| (?:installed|account|key|runtime))|\bno (?:external |additional )?(?:dependencies|installation|required setup)\b|(?:运行依赖|前置条件|所需工具)\s*[:：]\s*\S|(?:无需|无)额外依赖",
         r"dependencies|prerequisites?|requirements?|依赖|前置条件|所需工具"),
        ("setup_action", "安装或配置办法",
         r"\b(?:pip|conda|apt|brew|npm|uv pip) install\s+\S|\b(?:set|export|configure)\s+[A-Z][A-Z0-9_]{2,}|(?:配置|安装)\s*\x60[^\x60\n]+\x60|(?:无需|无)额外依赖|\bno (?:external |additional )?(?:dependencies|installation|required setup)\b",
         r"installation|setup|configuration|安装|配置方法|环境配置"),
        ("environment", "适用的运行环境",
         r"\b(?:python|node(?:\.js)?|cuda)\s+(?:>=?\s*)?[0-9][.\w+-]*|\b(?:on|under|only|for|with|requires)\s+(?:windows|macos|linux|bash|powershell|a network connection)\b|(?:联网|运行环境|适用版本|兼容性)\s*[:：]\s*\S",
         r"environment(?: notes)?|compatibility|supported platforms?|环境注意|运行环境|版本兼容性"),
        ("resources", "所需的包内资源", r"\b(?:scripts|references|assets|templates|examples)/[^\x60\s)>,，。]+", ""),
    ),
    "acceptance": (
        ("verify", "可操作的核查方法",
         r"\b(?:verify|validate|check|compare|inspect)\s+(?:that|against|the|every|each|all|with|a|whether|[\w/-]{4,}\s+(?:and|for|using))\s+\S|(?:检查|校验|核对|验收)\s*(?:每项|每个|所有|是否|输出|产物|文件|差异|结果)|运行.{0,22}(?:的验证|targeted tests)|验收办法.{0,10}(?:检验|验证)",
         r"verification|validation|final checks?|质量检查|验收方法|验证步骤"),
        ("expected", "可观察的通过条件",
         r"\b(?:expected (?:result|output|value)|acceptance criteria)\s*[:：]\s*\S|\b(?:must (?:equal|contain|match)|should (?:equal|contain|match)|exit code)\s+\S|\ball tests (?:must )?pass\b|(?:预期(?:输出|结果)|通过标准|验收条件)\s*[:：]\s*\S|(?:必须|应)(?:等于|包含|匹配|返回)\s*\S|(?:未通过|不通过).{0,32}(?:输出|标记|阻止|不得)",
         r"expected (?:results?|outputs?)|acceptance criteria|验收条件|通过标准|预期结果"),
        ("failure", "失败时的处理",
         r"\b(?:on failure|if .{3,100}(?:fails?|not found)|otherwise return)\b.{0,100}(?:return|retry|stop|prompt|report|fallback|use|abort)\b|(?:失败|异常|无法|未通过).{0,42}(?:改用|求助|中止|转入|回退|停止|返回|输出|标注|重试)",
         r"troubleshooting|error handling|failure handling|异常处理|失败处理|故障排查"),
        ("repeatable", "可重复的测试动作",
         r"\b(?:pytest|npm test|cargo test|gradle test|go test|re-?run (?:the )?tests)\b|(?:运行|重跑|(?<!可)执行).{0,28}(?:测试|\btests?\b|构建|typecheck)|(?:回归|单元|集成)测试\s*[:：]\s*\S",
         r"tests?|testing|regression tests?|测试步骤|回归测试"),
    ),
}
INLINE_PATH = re.compile(r"(?<!\x60)\x60(?:\./)?((?:scripts|references|assets|templates|examples)/[\w./-]{1,180})\x60(?!\x60)")
ORDERED = re.compile(r"^(?:#{1,6}\s+)?(?:[1-9]\d*[.)]|步骤\s*[1-9]\d*)\s+\S")
HEADING_INDEX = re.compile(r"^(?:[0-9]+[.)]|步骤\s*[0-9]+)\s+")
PLACEHOLDER = re.compile(r"^(?:[-*]\s*)?(?:todo|tbd|待补充|略)(?:\b|[：:。])", re.IGNORECASE)


def _rule_evidence(rows, prose, text_pattern, section_pattern):
    for line, text in prose:
        if re.search(text_pattern, text.replace("*", ""), re.IGNORECASE):
            return line
    if section_pattern:
        for (_, heading), (line, body) in pairwise(rows):
            match = HEADING.match(heading)
            if not match or HEADING.match(body) or PLACEHOLDER.match(body) or len(body) < 8:
                continue
            title = HEADING_INDEX.sub("", match[1].strip(" #*"))
            if re.search(r"^(?:" + section_pattern + r")(?=$|\W)", title, re.IGNORECASE):
                return line
    return None


def rubric_evidence(document: str) -> dict[str, list[tuple[str, str, int | None]]]:
    """Return document checks and their one-based source lines, also used as training inputs."""
    header = HEADER.match(document)
    metadata, _ = frontmatter(document.removeprefix("\ufeff"))
    rows = prose_lines(document, len(header[0].splitlines()) if header else 0)
    description = description_lines(document) if header and isinstance(metadata.get("description"), str) else []
    prose = [(line, text) for line, text in description + rows if not HEADING.match(text) and not PLACEHOLDER.match(text)]
    axes = {
        axis: [(key, label, _rule_evidence(rows, prose, pattern, section))
               for key, label, pattern, section in rules]
        for axis, rules in RULES.items()
    }
    has_name = isinstance(metadata.get("name"), str) and metadata["name"].strip()
    named = None
    if description and has_name and metadata["description"].strip():
        named = description[1 if len(description) > 1 else 0][0]
    headings = [line for line, text in rows if HEADING.match(text) and ORDERED.match(text)]
    steps = headings if len(headings) >= 2 else [line for line, text in rows if ORDERED.match(text)]
    axes["scope"].insert(0, ("purpose", "名称与用途", named))
    axes["workflow"].insert(0, ("ordered", "至少两个有序步骤", steps[0] if len(steps) >= 2 else None))
    return axes


def _rubric_dimensions(files):
    axes = []
    for axis, rules in rubric_evidence(files["SKILL.md"]).items():
        checks = [
            {"id": key, "label": label, "state": "found" if line else "undocumented",
             "points": 25 if line else 0, "max_points": 25,
             "evidence": citation(files, "SKILL.md", line) if line else None}
            for key, label, line in rules
        ]
        found = [row["label"] for row in checks if row["points"]]
        missing = [row["label"] for row in checks if not row["points"]]
        if not found:
            reason = "说明里没有找到这项所需的内容。"
        elif not missing:
            reason = "4 项均有出处，包括" + "、".join(found[:2]) + "。"
        else:
            reason = f"找到 {len(found)} 项。缺少" + "、".join(missing) + "。"
        axes.append({"id": axis, "label": AXES[axis], "score": len(found) * 25, "basis": "说明覆盖",
                     "reason": reason, "checks": checks, "scope": "只按读到的 SKILL.md 给分。每条 25 分。"})
    return axes


def _package_dimension(report, files, inventory):
    package = report["package"]
    present = set(inventory or files)
    references = {row["resource"]: {"label": row["resource"], "state": row["state"], "evidence": row["evidence"]}
                  for row in package["resource_links"]}
    document = files["SKILL.md"]
    header = HEADER.match(document)
    for number, text in prose_lines(document, len(header[0].splitlines()) if header else 0):
        for match in INLINE_PATH.finditer(text):
            path = match[1].rstrip("/")
            if len(references) >= 100 or path in references or any(part in {"", ".", ".."} for part in path.split("/")):
                continue
            resolved = path in present or any(item.startswith(path + "/") for item in present)
            references[path] = {"label": path, "state": "present" if resolved else "unknown",
                                "evidence": citation(files, "SKILL.md", number)}
        if len(references) >= 100:
            break
    checks = list(references.values())
    command_lines = set()
    statuses = {"consistent_static": "present", "inconsistent_static": "missing"}
    for observation in package["command_observations"]:
        line = observation["start_line"]
        command_lines.add(line)
        checks.append({"label": "文档命令的参数检查", "state": statuses.get(observation["result"]["status"], "unknown"),
                       "evidence": citation(files, "SKILL.md", line)})
    for finding in package["findings"]:
        if finding.get("layer") == "cli" and finding["evidence"]["start_line"] in command_lines:
            continue
        if finding.get("kind") == "bundled_link_missing" and finding.get("resource") in references:
            continue
        checks.append({"label": finding["explanation"], "state": "missing", "evidence": finding["evidence"]})
    passed = sum(row["state"] == "present" for row in checks)
    failed = sum(row["state"] == "missing" for row in checks)
    unknown = len(checks) - passed - failed
    checked = passed + failed
    reason = f"{passed}/{checked} 条可判的静态检查通过。" if checked else "当前材料里没有可判的资源、命令或语法检查项。"
    if unknown:
        reason += f"另有 {unknown} 条未核实。"
    return {"id": "package", "label": "包内一致性", "score": round(100 * passed / checked, 1) if checked else None,
            "basis": "静态核对", "reason": reason, "checks": checks,
            "counts": {"passed": passed, "failed": failed, "unknown": unknown},
            "scope": "分母只含能静态判定的检查。行内路径只验证存在，未找到的行内路径保留未核实。"}


def _task_dimension(report):
    profile = report["skill_profile"]
    task = {"id": "task", "label": "任务实测", "basis": "暂无实测", "score": None,
            "reason": "当前版本还没有登记任务得分。", "scope": None, "checks": []}
    proof = report.get("verified_adoption", {})
    if proof.get("state") == "measured_on_registered_inputs":
        holdout = proof.get("holdout")
        tested = holdout if holdout and holdout["pairs"] else proof["result"]
        count = tested["pairs"]
        if count:
            with_score, without = tested["with_passed"], tested["without_passed"]
            task.update(score=round(100 * with_score / count, 1), basis="固定任务验收",
                        reason=f"有 Skill 通过 {with_score}/{count}，无 Skill 通过 {without}/{count}。",
                        scope=proof["scope"], sample_count=count,
                        without_skill=round(100 * without / count, 1), lift=round(100 * (with_score - without) / count, 1))
    elif released := profile.get("public_benchmark"):
        measure = next(row for row in released["measures"] if row["dimension"] == "Effectiveness")
        task.update(score=round(100 * measure["with_skill"], 1), basis="公开任务效果",
                    reason=f"{released['tasks']} 项任务，原报告效果分 {100 * measure['without_skill']:.1f} → {100 * measure['with_skill']:.1f}。",
                    scope=released["scope"], source_url=released["url"], sample_count=released["tasks"],
                    without_skill=round(100 * measure["without_skill"], 1), lift=round(100 * measure["lift"], 1))
    elif measured := profile.get("measurements"):
        task.update(basis="部分步骤已实测", reason=f"{len(measured)} 类公开步骤已有复跑记录。详见完整报告。")
    return task


def build(report: dict, files: dict[str, str], inventory: list[str]) -> dict:
    dimensions = _rubric_dimensions(files)
    package = _package_dimension(report, files, inventory)
    task = _task_dimension(report)
    if report["package"]["findings"]:
        status = "repair_required"
    elif any(item["state"] == "unmet" for item in report["conditions"]):
        status = "environment_mismatch"
    elif any(item["kind"] == "embedded_credential" for item in report["package"]["review_items"]):
        status = "sensitive_material_review"
    elif any(item["state"] == "unknown" for item in report["conditions"]):
        status = "configuration_unknown"
    elif report["decision"] == "task_contract_review":
        status = "task_review_required"
    elif task["score"] is not None:
        status = "measured_scope_available"
    else:
        status = "runtime_review_required"
    return clean({
        "rubric_version": RUBRIC_VERSION,
        "dimensions": dimensions + [package, task],
        "agent": {"status": status, "task_scope": task["scope"],
                  "inventory_complete": report["package"]["inventory_complete"],
                  "bundle_fingerprint": report["bundle_fingerprint"],
                  "source_commit": report.get("source_import", {}).get("commit"),
                  "skill_path": report.get("source_import", {}).get("skill_path"),
                  "automatic_execution_authorized": False},
    })
