"""Describe one Skill, keeping documented claims and observed results separate."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .inspection import clean, frontmatter

DATA = Path(__file__).resolve().parent / "data/skill-profiles.json"
HEADER = re.compile(r"\A\ufeff?---[ \t]*\r?\n.*?^---[ \t]*(?:\r?\n|$)", re.DOTALL | re.MULTILINE)
HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.+)")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
LIMITS = r"\b(?:not supported|unsupported|not for|limitations?|pitfalls?|requires? (?:an? )?(?:text layer|ocr)|heuristic|can (?:fail|miss)|do(?:es)? not (?:evaluate|support)|cannot|never evaluates)\b|不适用|不支持|限制|边界|无法|仅支持"
FAMILIES = (
    ("codebook", "工位标签与编码归一", r"codebook|故障(?:原因|标签).*归一"),
    ("pdf", "PDF 文档处理", r"\bpdfs?\b"),
    ("spreadsheet", "电子表格", r"\b(?:xlsx|spreadsheet|excel|workbooks?)\b|电子表格"),
    ("presentation", "演示文稿", r"\b(?:pptx|powerpoint|slide decks?|presentations?)\b|演示文稿"),
    ("word", "Word 文档", r"\b(?:docx|word documents?)\b|Word 文档"),
    ("web", "网页与界面", r"\b(?:frontend|web design|web app|landing page|user interface)\b|网页设计|前端"),
)
# Rules recognize evidence about a task. Success is determined by an independent result.
ABILITIES = {
    "pdf": (
        ("text", "提取 PDF 文本", r"extract(?:ing|ion)? (?:plain )?text|text extraction|extract_text\(|pdftotext|pdf_read\.py.*--text|提取.{0,8}(?:文本|文字)"),
        ("table", "提取 PDF 表格", r"extract(?:ing|ion)? (?:pdf )?tables?|table extraction|text/tables|extract_tables\(|pdf_read\.py.*--tables|提取.{0,8}表格"),
        ("create", "生成 PDF", r"(?:creat(?:e|ing|ion)|generat(?:e|ing|ion)|build)(?: a| new|ing)?\s+PDFs?\b|pdf_create\.py|生成.{0,8}PDF"),
        ("page_ops", "合并、拆分或调整页面", r"merg(?:e|ing) (?:multiple )?(?:pdfs?|pages)|split(?:ting)? (?:pdfs?|pages)|pdf_(?:merge|split)\.py|(?:合并|拆分) PDF"),
        ("forms", "读取、创建或填充表单", r"\bAcroForms?\b|fill(?:ing|able)? (?:pdf )?forms?|pdf_(?:make|fill)_form\.py|PDF 表单"),
        ("ocr", "扫描件文字识别", r"\bocr\b.*\b(?:pdfs?|scann?ed|images?)\b|\b(?:scanned|image-only)\b.*\bocr\b|扫描.{0,15}(?:识别|OCR)"),
        ("render", "渲染页面与检查版面", r"render(?:ing)? (?:pdf )?pages?|visual (?:reviews?|checks?|inspection)|pdftoppm|版面检查"),
    ),
    "spreadsheet": (
        ("sheet_create", "生成工作簿", r"(?:creat(?:e|ing)|build)\s+(?:a |new |styled |multi-sheet )*(?:\.xlsx|workbooks?|spreadsheets?)|xlsx creation|xlsx_create\.py|创建.{0,8}(?:工作簿|表格)"),
        ("sheet_edit", "编辑单元格与表结构", r"edit(?:ing)? (?:cells?|(?:an? |existing |\.)*(?:xlsx|files|workbooks?))|edit, or fix an existing|xlsx creation, editing|set cells|xlsx_edit\.py|编辑单元格"),
        ("formulas", "编写公式和检查计算结果", r"\brecalculat(?:e|ion|ing)|formulas? (?:must|should|errors)|formula (?:recalc|verification)|公式.{0,8}(?:重算|检查)"),
        ("csv", "CSV 导入、导出与类型转换", r"csv (?:interop|import|export)|convert .*csv|csv_to_xlsx|xlsx_to_csv|convert between tabular file formats|CSV.{0,8}(?:导入|导出)"),
        ("charts", "生成图表和数据展示", r"\bcharts?\b.*(?:creat|build|series|range)|\b(?:creat|build|styles?)\w*\b.*\bcharts?\b|\bcharting\b|(?:生成|创建)图表"),
    ),
    "codebook": (
        ("normalize", "将描述归一到标准编码", r"normalize.{0,90}(?:reasons?|codebooks?)|(?:standard|pred_code).{0,60}(?:codebook|label)|编码归一"),
        ("station", "按工位筛选候选编码", r"station scope|station compatibility|station validation|按工位"),
        ("ambiguity", "处理多个候选编码", r"(?:deterministic )?tie.break|ambiguous descriptions|多个.*候选"),
        ("unlisted", "处理没有可靠候选的记录", r"unknown handling|output .*unknown|weak evidence|无合法匹配"),
        ("confidence", "给出置信度和理由", r"confidence calibration|calibrat\w* confidence|置信度"),
    ),
}
NORMAL_SECTIONS = re.compile(
    r"overview|intro(?:duction)?|when to use|quick start|workflow|how to (?:use|run)|"
    r"setup|prerequisites|dependencies|installation|examples?|usage|reference|quality expectations|"
    r"final checks?|next steps|requirements?|limitations?|pitfalls?|概述|简介|快速开始|用法|"
    r"安装|依赖|限制|检查|示例", re.IGNORECASE,
)
DOC_CHECKS = (
    ("input", "说明输入或触发条件",
     r"\b(?:when to use|inputs?|file formats?|accepted files?|pdf files|excel workbooks)\b|\.(?:pdf|xlsx|docx|pptx)\b|输入|触发条件"),
    ("output", "说明输出或交付物",
     r"\b(?:output(?:s)?|deliverables?|(?:export|save|write|create|produce)s?\s+.{0,35}(?:pdf|csv|json|docx|xlsx|png))\b|输出|交付物"),
    ("steps", "给出操作步骤",
     r"\b(?:quick start|workflow|procedure|how to run|step \d+)\b|^\s*\d+[.)]\s+|操作步骤|工作流程"),
    ("verification", "给出验收或核对方法",
     r"\b(?:final checks?|quality expectations|verif(?:y|ication)|validat(?:e|ion)|(?:visual|quality) checks?|compare (?:with|against))\b|验收|质量检查|校验|核对"),
    ("limits", "写明限制与失败条件", LIMITS),
    ("dependencies", "交代工具或环境",
     r"\b(?:prerequisites|requirements|dependencies|pip install|python \d|install if missing|requires python|python libraries|command-line tools)\b|运行环境|安装依赖|运行依赖"),
)
STRATUM_ABILITY = {"unambiguous": "normalize", "station": "station", "ambiguous": "ambiguity", "unlisted": "unlisted"}


def _match(pattern: str, value: str) -> bool:
    return re.search(pattern, value, re.IGNORECASE) is not None


def _lines(document: str, header_length: int) -> list[tuple[int, str]]:
    rows, fence = [], ""
    for number, line in enumerate(document.splitlines(), 1):
        marker = FENCE.match(line)
        if marker:
            token = marker[1]
            if not fence:
                fence = token
            elif len(token) >= len(fence) and fence[0] == token[0] and not line[marker.end():].strip():
                fence = ""
            continue
        if number > header_length and not fence and line.strip():
            rows.append((number, line.strip()))
    return rows


def _evidence(line: tuple[int, str], sha: str) -> dict:
    return {"path": "SKILL.md", "start_line": line[0], "sha256": sha, "quote": line[1][:280]}


def _first(rows: list[tuple[int, str]], pattern: str, sha: str) -> dict | None:
    return next((_evidence(line, sha) for line in rows if _match(pattern, line[1].replace("*", ""))), None)


def describe(files: dict[str, str], introduction: dict, package: dict) -> dict:
    document = files["SKILL.md"]
    header = HEADER.match(document)
    body = document[header.end():] if header else document
    header_rows = header[0].splitlines() if header else []
    metadata, _ = frontmatter(document.removeprefix("\ufeff"))
    has_metadata = bool(header and all(isinstance(metadata.get(key), str) and metadata[key].strip()
                                       for key in ("name", "description")))
    rows = _lines(document, len(header_rows))
    sha = package["file_sha256"]["SKILL.md"]
    description_line = next(((i, line) for i, line in enumerate(header_rows, 1) if line.startswith("description:")), None)
    observations = ([description_line] if description_line else []) + rows
    name, description = introduction["name"], introduction["description"]
    family, category = next(
        ((key, label) for scope in (name, description) for key, label, pattern in FAMILIES if _match(pattern, scope)),
        ("general", "其他任务"),
    )
    clauses = ((number, span) for number, line in rows + ([description_line] if description_line else [])
               for span in re.split(r"(?<=[.!?。！？])\s+|[;；]", line) if span.strip())
    claims = [line for line in clauses if not _match(
        r"\b(?:not for|not supported|do(?:es)? not|never|cannot|unsupported)\b|不支持|不适用|不要",
        line[1],
    )]
    capabilities = []
    for key, label, pattern in ABILITIES.get(family, ()):
        if evidence := _first(claims, pattern, sha):
            capabilities.append({"id": key, "title": label, "state": "declared", "evidence": evidence})
    if not capabilities:
        for i, line in rows:
            heading = HEADING.match(line)
            if heading and line.startswith("##") and not NORMAL_SECTIONS.fullmatch(heading[1].strip(" #")) and not _match(LIMITS, heading[1]):
                capabilities.append({"id": f"section-{i}", "title": heading[1].strip(" #")[:100],
                                     "state": "document_topic", "evidence": _evidence((i, line), sha)})
            if len(capabilities) == 6:
                break
    if not capabilities and description_line:
        capabilities.append({"id": "purpose", "title": "元数据中的用途", "state": "declared",
                             "evidence": _evidence(description_line, sha)})

    checks = [{"id": "summary", "title": "提供名称和用途", "found": has_metadata,
               "evidence": _evidence(description_line, sha) if has_metadata and description_line else None}]
    for key, title, pattern in DOC_CHECKS:
        evidence = _first(observations, pattern, sha)
        checks.append({"id": key, "title": title, "found": evidence is not None, "evidence": evidence})
    confirmed = sum(check["found"] for check in checks)
    metadata_stats = {
        "document_characters": len(document), "body_characters": len(body),
        "document_utf8_bytes": len(document.encode("utf-8")), "document_lines": len(document.splitlines()),
        "read_text_characters": sum(len(text) for text in files.values()),
        "files_read": package["files_analyzed"], "inventory_files": package["inventory_files"],
        "inventory_complete": package["inventory_complete"],
    }
    return clean({
        "name": name, "description": description, "family": family, "category": category,
        "size": metadata_stats,
        "documentation": {
            "found": confirmed, "total": len(checks), "checks": checks,
            "label": "说明覆盖较完整" if confirmed == len(checks) else "说明有缺项" if confirmed >= 4 else "说明较简略",
        },
        "capabilities": capabilities,
        "documented_limits": [_evidence(row, sha) for row in rows if _match(LIMITS, row[1])][:5],
        "functional_quality": {
            "state": "unverified", "title": "功能质量待验证",
            "reason": "当前版本尚无登记的任务产物与验收结果。", "scope": None,
        },
        "comparison": {"peers": [], "scope": "暂未登记同类公开 Skill 的对照版本。"},
        "measurements": None,
    })


@lru_cache(maxsize=1)
def reference_catalog() -> dict:
    data = json.loads(DATA.read_bytes())
    if data["schema"] != "skill-profiles-v1":
        raise ValueError("无法读取 Skill 对照表")
    return data


def _binding(source: dict) -> tuple:
    return (source.get("repository", "").casefold(), source.get("skill_path"), source.get("commit"),
            source.get("file_hashes", {}).get("SKILL.md", {}).get("raw_bytes_sha256"))


def _reference_binding(peer: dict) -> tuple:
    return (peer["repository"].casefold(), peer["skill_path"], peer["commit"], peer["sha256"])


def _paired(profile: dict, proof: dict) -> None:
    result, holdout = proof["result"], proof.get("holdout")
    tested = holdout if holdout and holdout["pairs"] else result
    strata = proof.get("strata", [])
    scope_term = "留出" if tested is holdout else "登记"
    rate = tested["with_passed"] / tested["pairs"]
    if rate >= .9 and tested["pairs"] >= 8 and strata and all(row["with_passed"] / row["pairs"] >= .75 for row in strata):
        state, title = "strong", "优秀（在实测范围内）"
    elif rate < .5:
        state, title = "weak", "表现欠佳（在实测范围内）"
    else:
        state, title = "conditional", "有条件可用（在实测范围内）"
    reason = (f"{scope_term}输入中，加载 Skill 后通过 {tested['with_passed']}/{tested['pairs']}，"
              f"无 Skill 通过 {tested['without_passed']}/{tested['pairs']}。")
    failures = [row["title"] for row in strata if row["with_passed"] == 0]
    if failures:
        reason += "下列输入未通过任何一例：" + "、".join(failures) + "。"
    scope = proof["scope"]
    if proof.get("profile_version") == "codebook-exact-v1":
        scope += "本次验收仅接受同工位的唯一精确别名匹配。歧义或无匹配时须返回 UNKNOWN。"
    profile["functional_quality"] = {"state": state, "title": title, "reason": reason, "scope": scope}
    by_id = {STRATUM_ABILITY.get(row["stratum"]): row for row in strata}
    for ability in profile["capabilities"]:
        if row := by_id.get(ability["id"]):
            ability["observation"] = {"passed": row["with_passed"], "total": row["pairs"],
                                      "without": row["without_passed"], "title": row["title"]}


def attach(profile: dict, report: dict, source: dict | None = None) -> None:
    """Bind on exact original and analyzed hashes before exposing measured outcomes."""
    catalog = reference_catalog()
    candidates = [peer for peer in catalog["peers"] if peer["family"] == profile["family"]]
    source = source or {}
    binding = _binding(source)
    matched = next((peer for peer in candidates if binding == _reference_binding(peer)
                    and peer["sha256"] == report["package"]["file_sha256"]["SKILL.md"]), None)
    if matched:
        profile["measurements"] = matched.get("measurements")
        capabilities_by_id = {entry["id"]: entry for entry in profile["capabilities"]}
        for key, title, _ in ABILITIES.get(profile["family"], ()):
            if key in (profile["measurements"] or {}) and key not in capabilities_by_id:
                profile["capabilities"].append({"id": key, "title": title, "state": "measured_workflow", "evidence": None})
        for ability in profile["capabilities"]:
            if measurement := (profile["measurements"] or {}).get(ability["id"]):
                ability["workflow_observation"] = measurement
        if profile["measurements"]:
            profile["functional_quality"].update(
                title="部分流程已有实测",
                state="workflow_measured",
                reason="下方列出对应版本的公开方法通过了哪些任务、在哪些任务失效。整包运行效果仍待测。",
                scope=catalog["protocol"]["runtime"],
            )
    if candidates:
        current = ((source.get("repository") or "").casefold(), source.get("skill_path"))
        current_documented = {row["id"] for row in profile["capabilities"] if row["state"] == "declared"}
        names = {key: title for key, title, _ in ABILITIES.get(profile["family"], ())}
        peers = []
        for peer in candidates:
            if (peer["repository"].casefold(), peer["skill_path"]) == current:
                continue
            peer_documented = set(peer["documented"])
            peers.append({
                "name": peer["name"], "url": peer["url"], "commit": peer["commit"], "sha256": peer["sha256"],
                "size": peer["characters"], "documentation": peer["documentation"],
                "current_more": [title for key, title in names.items() if key in current_documented - peer_documented],
                "peer_more": [title for key, title in names.items() if key in peer_documented - current_documented],
                "review": peer["review"], "measurements": peer.get("measurements"),
            })
        profile["comparison"] = {
            "peers": peers, "scope": "固定版本的同类公开 Skill。说明覆盖按相同规则检查，流程实测按各行注明的任务和方法对照。",
            "method": catalog["protocol"]["runtime"] if any(peer.get("measurements") for peer in candidates) else None,
        }
    proof = report.get("verified_adoption", {})
    if proof.get("state") == "measured_on_registered_inputs":
        _paired(profile, proof)
    if report["package"]["findings"]:
        profile["functional_quality"] = {
            "state": "blocked", "title": "有已确认缺陷，建议修复后再用",
            "reason": report["package"]["findings"][0]["explanation"], "scope": "针对当前读取的版本。",
        }
    elif any(condition["state"] == "unmet" for condition in report["conditions"]):
        profile["functional_quality"] = {
            "state": "environment_blocked", "title": "当前环境缺少运行条件",
            "reason": "所填环境缺少该 Skill 明确声明的运行条件。", "scope": "针对当前填写的运行环境。",
        }
