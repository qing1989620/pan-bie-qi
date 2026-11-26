"""Run predefined office and data tasks through a live PT instance."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

CASES = (
    ("pdf_to_excel", "离线从有文字层的 PDF 提取表格，再整理成 Excel 工作簿。",
     False, {"pdf_table", "xlsx"}, "anthropics/skills/xlsx"),
    ("excel_formulas", "离线编辑已有的 XLSX 工作簿，重算 Excel 公式并重新保存文件。",
     False, {"xlsx", "formulas"}, None),
    ("cudf_groupby", "用 cuDF 处理 pandas 风格的表格数据，并做分组聚合。",
     None, {"cudf", "cudf_groupby"}, "nvidia/skills/accelerated-computing-cudf"),
    ("playwright_form", "用 Playwright 做浏览器测试，检查本地网页表单的提交反馈。",
     None, {"browser_test"}, "microsoft/playwright-cli/playwright-cli"),
    ("word_report", "编辑一个 Word DOCX 文档，整理段落、标题和页码。",
     None, {"docx"}, None),
    ("team_slides", "制作团队周会的 PPTX 演示文稿，要有标题和三页进度。",
     None, {"pptx"}, None),
    ("scan_text", "从扫描版 PDF 中识别文字，需要 OCR。",
     None, {"ocr"}, None),
    ("unavailable_format", "Read a local quellvox q7n-format project and export a quellvox q7n archive.",
     False, set(), None),
)
UNRESOLVED = {"source_unavailable", "ambiguous_identity", "not_assessed",
              "package_review_unavailable", "package_version_mismatch"}
ADMISSIBLE = {"eligible", "conditional"}
ROOT = Path(__file__).resolve().parents[1]
NVIDIA_REVISION = "252ba53487b73eaafbfcccf3e750c7b350b93db9"
BENCHMARK = (
    f"https://raw.githubusercontent.com/NVIDIA/skills/{NVIDIA_REVISION}/"
    "skills/accelerated-computing-cudf/BENCHMARK.md"
)
OUT_OF_SCOPE = {
    "pdf_to_excel": {"github/awesome-copilot/convert-excel-to-md", "anthropics/claude-plugins-community/tres-import-contacts",
                     "larksuite/cli/lark-sheets"},
    "playwright_form": {"antibrow/anti-detect-browser-skills/anti-detect-browser"},
    "team_slides": {"github/awesome-copilot/publish-to-pages"},
}


def request(endpoint: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    call = Request(endpoint, data, {"Content-Type": "application/json"})
    with urlopen(call, timeout=42) as response:
        return json.load(response)


def crosscheck(endpoint: str, rows: list[dict]) -> dict:
    nvidia = next(row for row in rows if row["id"] == "cudf_groupby")
    observed = next((item["task_evidence"] for item in nvidia["recommendations"] if item["key"] == nvidia["expected_reference"]), {})
    try:
        with urlopen(BENCHMARK, timeout=12) as response:
            raw = response.read(200_000)
        text = raw.decode("utf-8")
        line = next(row for row in text.splitlines() if row.startswith("| Effectiveness |") and "→" in row)
        codex = [float(score) for score in re.findall(r"(\d+(?:\.\d+)?)%", line.strip("|").split("|")[-1])]
        tasks = int(re.search(r"Tasks:\s*(\d+)", text)[1])
        expected = {"tasks": tasks, "score": codex[1], "without_skill": codex[0]}
        author = {
            "expected": expected, "observed": observed,
            "state": "matched" if observed.get("sample_count") == tasks and observed.get("score") == codex[1] else "different",
            "source": BENCHMARK, "sha256": hashlib.sha256(raw).hexdigest(),
        }
    except (ValueError, OSError, StopIteration, TypeError, IndexError):
        author = {"state": "source_unavailable", "source": BENCHMARK, "observed": observed}
    paper = (ROOT / "docs/skill-discriminator-paper.pdf").read_bytes()
    reference = (ROOT / "docs/examples/paper-table-6.csv").read_text(encoding="utf-8-sig")
    probe = request(endpoint + "/task/pdf-table", {"pdf_base64": base64.b64encode(paper).decode(),
                                                   "page": 4, "reference_csv": reference, "expected_rows": 4})
    verification = probe["verification"]
    return {"public_nvidia_report": author,
            "local_pdf_contract": {"state": verification["state"], "matched_cells": verification.get("matched_cells"),
                                   "reference_cells": verification.get("reference_cells"), "page": 4,
                                   "input_sha256": hashlib.sha256(paper).hexdigest()}}


def check(base: str) -> dict:
    endpoint = base.rstrip("/") + "/api/discriminator"
    health = request(endpoint + "/health")
    rows = []
    baseline_status = Counter()
    for case, task, network, requirements, known in CASES:
        result = request(endpoint + "/recommend", {"task": task, "environment": {"network_allowed": network},
                                                   "limit": 3, "use_model": False})
        expected = requirements or {"abstain"}
        if requirements:
            observed = {item["id"] for item in result["requirements"] if item["covered"]}
        else:
            observed = {"abstain"} if not result["recommendations"] else set()
        lookup = {item["key"]: item for item in result["pipeline"]["assessment"]["candidates"]}
        searches = result["pipeline"]["discovery"]["searched"]
        top = searches[0].get("top_keys", []) if searches else []
        directory = [{"key": key, "status": lookup.get(key, {}).get("status", "not_assessed")} for key in top]
        baseline_status.update(item["status"] for item in directory)
        matches = result["recommendations"]
        unexpected = sorted({item["key"] for item in matches} & OUT_OF_SCOPE.get(case, set()))
        row = {
            "id": case, "task": task, "network_allowed": network, "expected": sorted(expected),
            "observed": sorted(observed), "expectation_met": expected <= observed and not unexpected,
            "out_of_scope_recommendations": unexpected,
            "expected_reference": known,
            "reference_found": any(item["key"] == known for item in matches) if known else None,
            "raw_directory_top3": directory,
            "model": result["pipeline"]["model"], "semantic": result["pipeline"]["semantic"],
            "elapsed_ms": result["elapsed_ms"], "stage_ms": result["stage_ms"],
            "discovery": result["pipeline"]["discovery"], "candidate_outcomes": list(lookup.values()),
            "recommendations": [{
                "key": item["key"], "repo": item["source"]["repository"], "commit": item["source"]["commit"],
                "url": item["source"]["canonical_url"], "document_sha256": item["document_sha256"],
                "license": item["license"]["id"], "fit": item["fit"], "conditions_to_check": item["conditions_to_check"],
                "matches": [{"id": match["id"], "line": match["evidence"]["start_line"], "url": match["evidence"]["url"]}
                            for match in item["matches"]],
                "assessment_scope": item["assessment"].get("scope"),
                "files_analyzed": item["assessment"].get("files_analyzed"),
                "task_evidence": item["assessment"]["task_evidence"],
            } for item in matches],
        }
        rows.append(row)
        print(f"{case}: {row['expectation_met']}, {len(matches)} recommendations, {row['elapsed_ms']} ms", flush=True)
    counts = dict(baseline_status)
    return {"schema": "pt-live-integration-v1", "version": health["version"],
            "measured_at_utc": datetime.now(UTC).isoformat(), "cases": rows, "crosschecks": crosscheck(endpoint, rows),
            "summary": {"passed_cases": sum(row["expectation_met"] for row in rows), "case_count": len(rows),
                        "successful_known_references": sum(row["reference_found"] is True for row in rows),
                        "known_references": sum(row["reference_found"] is not None for row in rows),
                        "baseline_slots": sum(counts.values()),
                        "baseline_excluded_by_policy": sum(value for key, value in counts.items() if key not in UNRESOLVED | ADMISSIBLE),
                        "baseline_unresolved": sum(value for key, value in counts.items() if key in UNRESOLVED),
                        "pt_slots": sum(len(row["recommendations"]) for row in rows),
                        "median_ms": round(statistics.median(row["elapsed_ms"] for row in rows)),
                        "max_ms": max(row["elapsed_ms"] for row in rows),
                        "paid_model_calls": sum(row["model"]["calls"] for row in rows)}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8853")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = check(args.base)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
