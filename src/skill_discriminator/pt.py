"""Search, assess and recommend versioned public Skills for a specific task."""

from __future__ import annotations

import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import quote

from . import pt_model, pt_semantic, source_import
from .inspection import citation, clean, description_lines, frontmatter
from .profile import HEADER, HEADING, prose_lines
from .pt_discovery import Candidate, discover
from .pt_license import license_evidence
from .pt_retrieval import Document, Retrieval, Task, document, plan, terms
from .service import assess_imported

NETWORK_NEEDED = re.compile(
    r"(?:\b(?:requires?|needs?|must have)\b.{0,48}\b(?:internet|network) (?:access|connection)\b)"
    r"|(?:必须|需要).{0,12}(?:联网|访问外网|网络连接)", re.IGNORECASE,
)
OFFLINE_REQUEST = re.compile(r"\boffline\b|\b(?:without|no) (?:internet|network)\b|离线|不联网|不能联网|禁止联网", re.IGNORECASE)
EXCLUSION = re.compile(
    r"not support(?:ed)?|cannot|doesn't support|not for|do not use for|no ocr|不支持|无法|不适用|不能", re.IGNORECASE,
)
DOMAIN = {
    "pdf_table": re.compile(r"\bpdfs?\b", re.IGNORECASE),
    "pdf_text": re.compile(r"\bpdfs?\b", re.IGNORECASE),
    "ocr": re.compile(r"\b(?:pdfs?|ocr)\b|扫描", re.IGNORECASE),
    "xlsx": re.compile(r"\b(?:xlsx|excel|openpyxl|xlsxwriter)\b|Excel\s*(?:工作簿|文件)", re.IGNORECASE),
    "formulas": re.compile(r"\b(?:xlsx|spreadsheet|openpyxl|workbook|excel)\b|工作簿", re.IGNORECASE),
    "cudf": re.compile(r"\b(?:cudf|rapids)\b", re.IGNORECASE),
    "cudf_groupby": re.compile(r"\b(?:cudf|rapids)\b", re.IGNORECASE),
}
HOSTED_BINARIES = {"lark-cli": "需要联网访问飞书服务"}
ANTI_DETECTION = re.compile(r"\banti[- ]detect(?:ion)?\b|反检测|隐匿指纹|规避.{0,12}检测", re.IGNORECASE)
HARDWARE = re.compile(r"\b(?:requires?|needs?)\b.{0,50}\b(?:nvidia gpu|compatible driver)\b", re.IGNORECASE)
OVERVIEW_TIME_BUDGET = 25
TIME_BUDGET = 38
AUDIT_WORKERS = 5
INCOMPLETE_TEXT = {"source_read_incomplete", "text_read_budget", "not_utf8_text", "symlink_or_submodule_not_followed"}


@dataclass
class Review:
    candidate: Candidate
    document: Document
    report: dict
    license: dict
    matched: list[dict]
    conditions: list[str]
    excluded: str | None


def _publish_evidence(files, number, source):
    item = citation(files, "SKILL.md", number)
    item["url"] = f"{source['canonical_url']}#L{number}"
    return item


def _task_fit(task: Task, doc: Document, files: dict, source: dict) -> tuple[list[dict], set[str]]:
    raw = files["SKILL.md"]
    header = HEADER.match(raw)
    rows = description_lines(raw) + prose_lines(raw, len(header[0].splitlines()) if header else 0)
    synopsis = f"{doc.name} {doc.description}"
    matched, excluded_facets = [], set()
    for item in task.requirements:
        domain = DOMAIN.get(item.id)
        if item.id == "browser_test" and ANTI_DETECTION.search(synopsis):
            excluded_facets.add(item.id)
            continue
        if item.id in {"cudf", "cudf_groupby"} and not domain.search(synopsis):
            continue
        support = None
        for number, line in rows:
            if domain and not domain.search(synopsis + " " + line):
                continue
            if item.explicit_exclusion and item.explicit_exclusion.search(line):
                excluded_facets.add(item.id)
            elif support is None and not HEADING.match(line) and item.evidence.search(line) and not EXCLUSION.search(line):
                support = number
        if support is not None and item.id not in excluded_facets:
            matched.append({"id": item.id, "label": item.label, "evidence": _publish_evidence(files, support, source)})
    if not task.requirements:
        query_terms = set(terms(task.ranking_text))
        if len(query_terms) >= 2:
            candidates = [(len(query_terms.intersection(terms(line))), number) for number, line in rows
                          if not EXCLUSION.search(line)]
            coverage, best_line = max(candidates, key=lambda row: (row[0], -row[1]), default=(0, 0))
            if coverage >= 2 and query_terms.intersection(terms(synopsis)):
                matched.append({"id": "task_terms", "label": "任务关键词呼应",
                                "evidence": _publish_evidence(files, best_line, source)})
    return matched, excluded_facets


def _runtime_requirements(raw: str) -> tuple[str | None, bool]:
    metadata, _ = frontmatter(raw.removeprefix("\ufeff"))
    settings = metadata.get("metadata")
    requires = settings.get("requires") if isinstance(settings, dict) else None
    bins = requires.get("bins") if isinstance(requires, dict) else None
    network = next((HOSTED_BINARIES[item] for item in bins if isinstance(item, str) and item in HOSTED_BINARIES), None) if isinstance(bins, list) else None
    lines = raw.splitlines()
    if network is None and any(NETWORK_NEEDED.search(line) and not EXCLUSION.search(line) for line in lines):
        network = "需要联网"
    return network, any(HARDWARE.search(line) and not EXCLUSION.search(line) for line in lines)


def _assess_candidate(candidate: Candidate, source: dict, task: Task, environment: dict, licence: dict) -> Review:
    package = source["package"]
    raw = package["files"]["SKILL.md"]
    doc = document(candidate.key, raw, candidate.slug)
    report = assess_imported(source, task.text, environment)
    fit, unsupported = _task_fit(task, doc, package["files"], source["source"])
    checks = report["assessment"]["agent"]["status"]
    explicit_network, hardware = _runtime_requirements(raw)
    notes = [item["requirement"] for item in report["conditions"] if item["state"] == "unknown"]
    excluded = None
    if licence["state"] != "recognized":
        excluded = "license_restricted" if licence["state"] == "restricted" else "license_unconfirmed"
    elif checks in {"repair_required", "environment_mismatch", "sensitive_material_review", "task_review_required"}:
        excluded = checks
    elif explicit_network and environment.get("network_allowed") is False:
        excluded = "environment_mismatch"
    elif unsupported and not fit:
        excluded = "declared_out_of_scope"
    elif not fit:
        excluded = "insufficient_task_evidence"
    if explicit_network and environment.get("network_allowed") is None and "允许访问网络" not in notes:
        notes.append(explicit_network)
    if hardware:
        notes.append("核对 NVIDIA GPU、驱动和所需软件版本")
    if not task.requirements:
        notes.append("说明与任务有关键词重合，采用前请确认具体输入和产出")
    if unsupported and fit:
        notes.extend(f"说明限制了「{item.label}」" for item in task.requirements if item.id in unsupported)
    if source["source"].get("inspection_scope") == "package_text_partial":
        notes.append("部分包内材料未能检查，使用前请复核")
    return Review(candidate, doc, report, licence, fit, notes, excluded)


def _review(candidate: Candidate, task: Task, environment: dict, deadline: float) -> tuple[Review | None, dict | None]:
    try:
        source = source_import.import_link(candidate.url, overview_only=True, deadline=deadline)
        if source["state"] != "imported":
            return None, {"key": candidate.key, "status": "ambiguous_identity", "reason": "目录里有多个匹配的 Skill。"}
        return _assess_candidate(candidate, source, task, environment, license_evidence(source)), None
    except ValueError:
        return None, {"key": candidate.key, "status": "source_unavailable", "reason": "本次未能核全固定来源。"}


def _review_package(overview: Review, task: Task, environment: dict, deadline: float) -> tuple[Review | None, str | None]:
    bound = overview.report["source_import"]
    if len(bound["file_hashes"]) == overview.report["package"]["inventory_files"]:
        bound["inspection_scope"] = "package_text"
        return overview, None
    try:
        imported = source_import.import_link(bound["canonical_url"], deadline=deadline)
        if imported["state"] != "imported" or not imported["package"]["inventory_complete"]:
            return None, "package_review_unavailable"
        source = imported["source"]
        if (any(source[key] != bound[key] for key in ("canonical_url", "commit", "skill_path"))
                or source["file_hashes"]["SKILL.md"]["raw_bytes_sha256"] != bound["file_hashes"]["SKILL.md"]["raw_bytes_sha256"]):
            return None, "package_version_mismatch"
        limited = source.get("omissions_truncated") or any(
            item["reason"] in INCOMPLETE_TEXT for item in source.get("omitted", [])
        )
        source["inspection_scope"] = "package_text_partial" if limited else "package_text"
        # The license already applies to this commit, including inherited terms.
        return _assess_candidate(overview.candidate, imported, task, environment, overview.license), None
    except ValueError:
        return None, "package_review_unavailable"


def _requirements(review: Review) -> set[str]:
    return {item["id"] for item in review.matched}


def select(reviews: list[Review], task: Task, limit: int) -> tuple[list[tuple[Review, list[str]]], set[str], dict]:
    """Cover uncovered task requirements before showing overlapping alternatives."""
    if not reviews:
        return [], set(), {"state": "no_candidates"}
    candidates = [review.document for review in reviews]
    scores = Retrieval(candidates).scores(task.ranking_text)
    relevance, semantic = pt_semantic.rank(task.text, candidates, scores["metadata"])
    covered = set()
    remaining = list(range(len(reviews)))
    order = {item.id: position for position, item in enumerate(task.requirements)}
    results = []
    while remaining and len(results) < limit:
        number = min(remaining, key=lambda i: (
            -len(_requirements(reviews[i]) - covered), -len(_requirements(reviews[i])),
            reviews[i].report["assessment"]["dimensions"][-1]["score"] is None, bool(reviews[i].conditions),
            min((order.get(key, len(order)) for key in _requirements(reviews[i]) - covered),
                                                 default=len(order)),
            -relevance.get(i, 0), -scores["pt"].get(i, 0), reviews[i].candidate.key,
        ))
        remaining.remove(number)
        review = reviews[number]
        increment = sorted(_requirements(review) - covered)
        results.append((review, increment))
        covered.update(increment)
    return results, covered, semantic


FILTER_REASONS = {
    "license_restricted": "许可条款限制使用或分发。",
    "license_unconfirmed": "该固定版本的开源许可尚未核实。",
    "repair_required": "包内或接口核查发现已确认问题。",
    "environment_mismatch": "当前目标环境缺少该 Skill 的运行条件。",
    "sensitive_material_review": "包内配置材料需要复核。",
    "task_review_required": "声明和当前任务之间有待解决的差异。",
    "declared_out_of_scope": "说明明确排除所需任务。",
    "insufficient_task_evidence": "未找到所需能力的来源行。",
    "duplicate_document": "已找到可核查文本相同的固定版本。",
    "package_review_unavailable": "未能取得固定版本的包内材料以完成复核。",
    "package_version_mismatch": "读取的包与已匹配的来源版本不一致。",
}


def _outcome(review: Review, state: str) -> dict:
    report = review.report
    source = report["source_import"]
    outcome = {"key": review.candidate.key, "status": state,
               "license": review.license["id"], "license_state": review.license["state"],
               "url": source["canonical_url"], "matched": sorted(_requirements(review)),
               "scope": source.get("inspection_scope"), "reason": FILTER_REASONS.get(state)}
    if state == "repair_required":
        finding = report["package"]["findings"][0]
        root = source["canonical_url"].removesuffix("SKILL.md")
        outcome["reason"] = finding["explanation"]
        outcome["evidence"] = [
            {**item, "url": f"{root}{quote(item['path'], safe='/')}#L{item['start_line']}"}
            for item in (finding["evidence"], finding.get("contract_evidence")) if item
        ]
    return outcome


def _content_key(review: Review) -> tuple:
    source = review.report["source_import"]
    files = tuple(sorted((path, hashes["raw_bytes_sha256"]) for path, hashes in source["file_hashes"].items()))
    return files, review.license["id"]


def _result(review: Review, increment: list[str], task: Task) -> dict:
    report, profile = review.report, review.report["skill_profile"]
    src = report["source_import"]
    axes = report["assessment"]["dimensions"]
    available = _requirements(review)
    missing = [{"id": item.id, "label": item.label} for item in task.requirements if item.id not in available]
    task_proof = {key: axes[-1].get(key) for key in ("score", "basis", "reason", "scope", "source_url", "sample_count")}
    return {
        "key": review.candidate.key,
        "name": profile["name"],
        "purpose": report["quick_summary"]["purpose"],
        "role": "covers_requirement" if increment else "alternative",
        "fit": "conditional" if review.conditions else "documented",
        "matches": review.matched,
        "missing": missing,
        "added_coverage": increment,
        "conditions_to_check": review.conditions,
        "license": review.license,
        "source": {key: src[key] for key in ("canonical_url", "repository", "commit", "skill_path")},
        "document_sha256": src["file_hashes"]["SKILL.md"]["raw_bytes_sha256"],
        "discovery": {"channel": review.candidate.discovery, "query": review.candidate.query},
        "assessment": {"inventory_files": profile["size"]["inventory_files"],
                       "characters": profile["size"]["document_characters"],
                       "files_analyzed": report["package"]["files_analyzed"],
                       "scope": src["inspection_scope"],
                       "verdict": report["quick_summary"]["verdict"],
                       "reason": report["quick_summary"]["reason"],
                       "dimensions": [{key: row[key] for key in ("id", "label", "score", "basis", "reason")} for row in axes],
                       "agent": report["assessment"]["agent"], "task_evidence": task_proof},
    }


def recommend(task_text: str, environment: dict, *, limit: int, search_online: bool, use_model: bool) -> dict:
    start = time.monotonic()
    overview_deadline = start + OVERVIEW_TIME_BUDGET
    deadline = start + TIME_BUDGET
    task = plan(clean(task_text))
    if not task.text or not task.queries:
        raise ValueError("请描述要处理的输入和期望产出。")
    constraints = dict(environment)
    if constraints.get("network_allowed") is None and OFFLINE_REQUEST.search(task.text):
        constraints["network_allowed"] = False
    extra, model = pt_model.suggestions(task.text, requested=use_model and search_online)
    if extra and not task.requirements:
        task = Task(task.text, task.requirements, task.queries, " ".join([task.ranking_text, *extra]))
    planned = time.monotonic()
    candidates, discovery = discover(task, extra, overview_deadline, search_online=search_online)
    discovered = time.monotonic()
    reviews = []
    overviews = []
    outcomes = {}
    seen = set()
    with ThreadPoolExecutor(max_workers=AUDIT_WORKERS, thread_name_prefix="pt-assess") as workers:
        for review, failure in workers.map(lambda ref: _review(ref, task, constraints, overview_deadline), candidates):
            if failure:
                outcomes[failure["key"]] = failure
            elif review.excluded:
                outcomes[review.candidate.key] = _outcome(review, review.excluded)
            else:
                outcomes[review.candidate.key] = None
                overviews.append(review)
        audited = workers.map(lambda row: _review_package(row, task, constraints, deadline), overviews)
        for overview, (review, failure) in zip(overviews, audited, strict=True):
            if failure:
                outcomes[overview.candidate.key] = _outcome(overview, failure)
                continue
            state = review.excluded or ("conditional" if review.conditions else "eligible")
            if not review.excluded:
                key = _content_key(review)
                if review.report["source_import"]["inspection_scope"] == "package_text_partial":
                    reviews.append(review)
                elif key in seen:
                    state = "duplicate_document"
                else:
                    reviews.append(review)
                    seen.add(key)
            outcomes[review.candidate.key] = _outcome(review, state)
    assessed = time.monotonic()
    selected, covered, semantic = select(reviews, task, limit)
    ended = time.monotonic()
    return clean({
        "state": "recommendations" if selected else "no_supported_candidates",
        "requirements": [{"id": item.id, "label": item.label, "covered": item.id in covered} for item in task.requirements],
        "target_network_allowed": constraints.get("network_allowed"),
        "pipeline": {"discovery": discovery, "assessment": {"counts": dict(Counter(item["status"] for item in outcomes.values())),
                                                            "candidates": list(outcomes.values())}, "model": model, "semantic": semantic},
        "recommendations": [_result(review, increment, task) for review, increment in selected],
        "elapsed_ms": round((ended - start) * 1000),
        "stage_ms": {"planning": round((planned - start) * 1000), "discovery": round((discovered - planned) * 1000),
                     "assessment": round((assessed - discovered) * 1000), "recommendation": round((ended - assessed) * 1000)},
        "automatic_execution_authorized": False,
    })
