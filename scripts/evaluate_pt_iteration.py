"""Compare PT variants on predeclared live tasks with a shared source capture."""

from __future__ import annotations

import argparse
import copy
import json
import threading
from dataclasses import asdict, replace
from pathlib import Path

from fastapi.testclient import TestClient

from skill_discriminator import pt, pt_discovery, source_import
from skill_discriminator.api import create_app

CASESETS = {
    "regression": (
        ("matplotlib", "Create a Matplotlib scatter plot with labeled axes and export it as SVG."),
        ("postgres", "Optimize slow PostgreSQL SELECT queries using EXPLAIN and indexes."),
        ("git-conflict", "Resolve Git merge conflicts while preserving changes from both branches."),
        ("accessibility", "审查前端界面可访问性，核对键盘焦点、语义标签和颜色对比度。"),
        ("mermaid", "用 Mermaid flowchart 画部署流程，展示步骤和条件分支。"),
        ("ci", "Diagnose a failing GitHub Actions workflow and explain the failing CI step."),
    ),
    "held_out_tasks": (
        ("plotly", "Use Plotly to create a scatter plot for two numerical fields and export it as HTML."),
        ("plantuml", "使用 PlantUML 绘制流程图，展示审批流程的两个条件分支。"),
        ("local-browser", "用 Playwright 浏览器测试本地页面里的搜索、键盘焦点和弹窗。"),
        ("local-json-schema", "Validate JSON records against a JSON Schema offline and show which entries failed."),
        ("react-interface", "Build a React frontend dashboard with accessible filters and keyboard navigation."),
        ("docker-compose", "Create a Docker Compose configuration for a local PostgreSQL development database."),
    ),
    "transfer": (
        ("altair", "Use Altair to make a heatmap from two categorical columns and save it as SVG."),
        ("duckdb", "Use DuckDB to join two local Parquet datasets and write Parquet offline."),
        ("rust-proptest", "Write property-based tests for a Rust parser using `proptest`."),
        ("ffmpeg", "用 FFmpeg 将 WebM 转成带字幕的 MP4，保留音频。"),
    ),
    "second_transfer": (
        ("polars", "Use Polars to group a local CSV by two fields, compute totals, and write Parquet offline."),
        ("pandoc", "用 Pandoc 把包含表格和脚注的 Markdown 转成可编辑 DOCX。"),
        ("playwright-form", "Use Playwright to test a local multi-step registration form and verify its error messages."),
        ("go-ci", "Create a GitHub Actions workflow to test Go modules and save JUnit test output."),
    ),
}


class Capture:
    """Record inputs, not answers, for shared catalog and versioned-file requests."""

    def __init__(self, path: Path, *, frozen: bool, retry_unavailable: bool):
        self.path = path
        self.frozen = frozen
        self.retry_unavailable = retry_unavailable
        self.lock = threading.Lock()
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"catalog": {}, "imports": {}}
        self.search = pt_discovery._catalog_search
        self.source = source_import.import_link

    def lookup(self, section, key, fetch, unavailable):
        with self.lock:
            prior = self.data[section].get(key)
        if prior is not None and (not self.retry_unavailable or not unavailable(prior)):
            return copy.deepcopy(prior)
        if self.frozen:
            return None
        fresh = fetch()
        with self.lock:
            if prior is not None or key not in self.data[section]:
                self.data[section][key] = copy.deepcopy(fresh)
        return fresh

    def catalog(self, query, deadline):
        def fetch():
            observation, candidates = self.search(query, deadline)
            return observation, [asdict(row) for row in candidates]

        recorded = self.lookup("catalog", query, fetch, lambda result: result[0]["state"] != "searched")
        if recorded is None:
            return {"query": query, "state": "uncaptured", "hits": 0}, []
        observation, rows = recorded
        return observation, [pt_discovery.Candidate(**row) for row in rows]

    def imported(self, url, *args, **kwargs):
        key = json.dumps([url, args, kwargs.get("overview_only")], ensure_ascii=False, sort_keys=True)

        def fetch():
            try:
                return {"value": self.source(url, *args, **kwargs)}
            except ValueError:
                return {"unavailable": True}

        data = self.lookup("imports", key, fetch, lambda result: result.get("unavailable"))
        if data is None or data.get("unavailable"):
            raise source_import.SourceError("Source not available in the captured run")
        return data["value"]

    def save(self):
        if self.frozen:
            return
        pending = self.path.with_suffix(".saving")
        pending.write_text(json.dumps(self.data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        pending.replace(self.path)


def run(case_sets: list[str], snapshot: Path, label: str, *,
        frozen: bool, retry_unavailable: bool, query_report: Path | None) -> dict:
    capture = Capture(snapshot, frozen=frozen, retry_unavailable=retry_unavailable)
    original_plan = pt.plan
    if query_report is not None:
        recorded = json.loads(query_report.read_text(encoding="utf-8"))["cases"]
        queries = {original_plan(row["task"]).text: tuple(
            item["query"] for item in row["report"]["pipeline"]["discovery"]["searched"]
        ) for row in recorded}

        def legacy_queries(task_text):
            parsed = original_plan(task_text)
            return replace(parsed, queries=queries.get(parsed.text, parsed.queries))

        pt.plan = legacy_queries
    pt_discovery._catalog_search = capture.catalog
    source_import.import_link = capture.imported
    results = []
    try:
        with TestClient(create_app()) as client:
            for split in case_sets:
                for key, task in CASESETS[split]:
                    response = client.post("/api/discriminator/recommend", json={
                        "task": task, "limit": 3, "use_model": False, "search_online": True,
                    })
                    response.raise_for_status()
                    report = response.json()
                    results.append({"id": key, "split": split, "task": task, "report": report})
                    print(key, report["state"], [row["key"] for row in report["recommendations"]], flush=True)
                    capture.save()
    finally:
        pt_discovery._catalog_search = capture.search
        source_import.import_link = capture.source
        pt.plan = original_plan
    return {"schema": "pt-iteration-comparison-v1", "variant": label, "frozen": frozen, "cases": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--snapshot", type=Path, required=True,
                        help="Private local capture of public source files, for replay across variants.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=CASESETS, action="append")
    parser.add_argument("--query-report", type=Path, help="Replace only the search queries with a previous variant's recorded plan.")
    capture_mode = parser.add_mutually_exclusive_group()
    capture_mode.add_argument("--frozen", action="store_true", help="Replay without making new catalog or source requests.")
    capture_mode.add_argument("--retry-unavailable", action="store_true",
                              help="Refresh only failed catalog and fixed-source reads before freezing.")
    args = parser.parse_args()
    report = run(args.split or list(CASESETS), args.snapshot, args.variant,
                 frozen=args.frozen, retry_unavailable=args.retry_unavailable, query_report=args.query_report)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
