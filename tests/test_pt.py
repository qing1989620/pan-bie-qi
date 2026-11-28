"""Validate PT's decision path using public-source-shaped, controlled candidates."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from threading import Event

import pytest
from fastapi.testclient import TestClient

from skill_discriminator import pt, pt_discovery, pt_model, source_import
from skill_discriminator.api import create_app
from skill_discriminator.pt_discovery import Candidate
from skill_discriminator.pt_license import license_evidence
from skill_discriminator.pt_retrieval import plan

REVISION = "1" * 40
PDF = "Use pdfplumber to extract tables from text-layer PDF pages."
XLSX = "Create .xlsx workbooks and recalculate spreadsheet formulas using openpyxl."


def snapshot(name, description, body="", *, licence="MIT", license_text=None, metadata="", materials=None):
    text = f"---\nname: {name}\ndescription: {json.dumps(description)}\nlicense: {licence}\n{metadata}---\n\n{body}"
    files = {"SKILL.md": text, **(materials or {})}
    if license_text is not None:
        files["LICENSE.txt"] = license_text
    repository = f"example/{name}"
    url = f"https://github.com/{repository}/blob/{REVISION}/SKILL.md"
    return {"state": "imported", "package": {"identity": name, "files": files, "inventory": list(files), "inventory_complete": True},
            "source": {"canonical_url": url, "repository": repository, "commit": REVISION, "skill_path": "SKILL.md",
                       "package_root": "",
                       "file_hashes": {path: {"raw_bytes_sha256": hashlib.sha256(value.encode()).hexdigest()} for path, value in files.items()}}}


def install_collection(monkeypatch, **documents):
    references = {name: Candidate(f"https://skills.sh/example/{name}/{name}", f"example/{name}", name, "skills.sh", "test")
                  for name in documents}
    lookup = {references[name].url: record for name, record in documents.items()}
    fixed = {record["source"]["canonical_url"]: record for record in documents.values()}
    monkeypatch.setattr(pt, "discover", lambda *_a, **_k: (
        list(references.values()), {"provider": "fixture", "searched": [], "candidates": len(references), "enabled": True},
    ))
    def import_link(url, *_args, overview_only=False, **_kwargs):
        if not overview_only:
            return fixed[url]
        record = lookup[url]
        source, package = record["source"], record["package"]
        retained = {"SKILL.md"} | {path for path in package["files"] if source_import.is_package_license(path)}
        return {**record,
                "source": {**source, "inspection_scope": "guide_license_inventory",
                           "file_hashes": {path: source["file_hashes"][path] for path in retained}},
                "package": {**package, "files": {path: package["files"][path] for path in retained}}}

    monkeypatch.setattr(pt.source_import, "import_link", import_link)
    monkeypatch.delenv("PAN_BIE_QI_E5_DIR", raising=False)
    return references


def test_covers_two_capabilities_and_filters_similar_ineligible_skills(monkeypatch):
    install_collection(monkeypatch,
                       pdf=snapshot("pdf", PDF, "Export rows to a local file."),
                       pdf_second=snapshot("pdf-second", PDF, "Normalize cell whitespace before output."),
                       workbook=snapshot("workbook", XLSX, "Write and reopen the local workbook."),
                       online=snapshot("online", XLSX, "Requires internet access to use the hosted workbook."),
                       cloud_bin=snapshot("cloud-bin", XLSX, "The workbook is stored on the service.",
                                          metadata="metadata:\n  requires:\n    bins: [lark-cli]\n"),
                       restricted=snapshot("restricted", PDF, "Choose a page first.", license_text="Proprietary. All rights reserved."),
                       broken=snapshot("broken", PDF, "Follow the [parser](scripts/parser.py) for each page."))
    with TestClient(create_app()) as client:
        assert client.get("/recommend").status_code == 200
        response = client.post("/api/discriminator/recommend", json={
            "task": "离线从PDF提取表格，再整理成Excel工作簿", "limit": 2,
        })
    assert response.status_code == 200
    report = response.json()
    assert report["target_network_allowed"] is False
    assert not report["automatic_execution_authorized"]
    assert {row["id"] for row in report["requirements"] if row["covered"]} == {"pdf_table", "xlsx"}
    assert [row["added_coverage"] for row in report["recommendations"]] == [["pdf_table"], ["xlsx"]]
    assert report["pipeline"]["assessment"]["counts"] == {
        "eligible": 3, "environment_mismatch": 2, "license_restricted": 1, "repair_required": 1,
    }
    for item in report["recommendations"]:
        assert item["source"]["commit"] == REVISION
        assert item["assessment"]["scope"] == "package_text"
        assert item["matches"][0]["evidence"]["url"].startswith(item["source"]["canonical_url"] + "#L")
        assert item["assessment"]["task_evidence"]["score"] is None


def test_same_package_discriminator_drives_link_assessment_and_recommendation(monkeypatch):
    command = "```bash\npython scripts/build.py --output report.xlsx\n```"
    parser = "import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument({!r}, required=True)\nparser.parse_args()\n"
    good = snapshot("xlsx", XLSX, command, materials={"scripts/build.py": parser.format("--output")})
    bad = snapshot("xlsx", XLSX, command, materials={"scripts/build.py": parser.format("--target")})
    bad["source"]["repository"] = "example/broken"
    bad["source"]["canonical_url"] = f"https://github.com/example/broken/blob/{REVISION}/SKILL.md"
    install_collection(monkeypatch, broken=bad, xlsx=good)

    with TestClient(create_app()) as client:
        task = "把数据导出成 Excel 工作簿"
        ranked = client.post("/api/discriminator/recommend", json={"task": task, "limit": 1}).json()
        direct = [client.post("/api/discriminator/import-url", json={"task": task, "url": item["source"]["canonical_url"]})
                  .json()["report"] for item in (bad, good)]

    rejected, accepted = ranked["pipeline"]["assessment"]["candidates"]
    assert rejected["status"] == direct[0]["assessment"]["agent"]["status"] == "repair_required"
    assert direct[0]["package"]["findings"][0]["kind"] == "argparse_command_rejected"
    assert [row["path"] for row in rejected["evidence"]] == ["SKILL.md", "scripts/build.py"]
    assert rejected["evidence"][1]["url"].startswith(bad["source"]["canonical_url"].removesuffix("SKILL.md"))
    assert accepted["status"] == "eligible"
    [item] = ranked["recommendations"]
    assessment = item["assessment"]
    assert item["source"]["canonical_url"] == good["source"]["canonical_url"]
    assert assessment["agent"] == direct[1]["assessment"]["agent"]
    assert assessment["verdict"] == direct[1]["quick_summary"]["verdict"]
    assert assessment["files_analyzed"] == 2
    assert [(row["id"], row["score"], row["reason"]) for row in assessment["dimensions"]] == [
        (row["id"], row["score"], row["reason"]) for row in direct[1]["assessment"]["dimensions"]
    ]


def test_changed_snapshot_cannot_be_recommended_using_overview(monkeypatch):
    install_collection(monkeypatch, xlsx=snapshot("xlsx", XLSX, materials={"scripts/build.py": "print('ready')\n"}))
    read = pt.source_import.import_link

    def changed(url, *, overview_only=False, **kwargs):
        result = read(url, overview_only=overview_only, **kwargs)
        return result if overview_only else {**result, "source": {**result["source"], "commit": "2" * 40}}

    monkeypatch.setattr(pt.source_import, "import_link", changed)
    result = pt.recommend("把数据导出成 Excel 工作簿", {}, limit=1, search_online=False, use_model=False)
    assert result["recommendations"] == []
    assert result["pipeline"]["assessment"]["counts"] == {"package_version_mismatch": 1}


def test_full_audit_starts_before_other_overviews_finish(monkeypatch):
    references = install_collection(monkeypatch,
                                    xlsx=snapshot("xlsx", XLSX, materials={"examples/notes.md": "Example."}),
                                    slow=snapshot("slow", XLSX))
    audit_started = Event()
    import_link = pt.source_import.import_link

    def coordinated_import(url, *, overview_only=False, **options):
        if overview_only and url == references["slow"].url:
            assert audit_started.wait(timeout=3)
        if not overview_only:
            audit_started.set()
        return import_link(url, overview_only=overview_only, **options)

    monkeypatch.setattr(pt.source_import, "import_link", coordinated_import)
    result = pt.recommend("把数据导出成 Excel 工作簿", {}, limit=2, search_online=False, use_model=False)
    assert [row["status"] for row in result["pipeline"]["assessment"]["candidates"]] == ["eligible", "eligible"]


def test_optional_usage_syntax_does_not_disqualify_public_candidate(monkeypatch):
    body = "```bash\npython scripts/build.py --output report.xlsx [--formulas]\n```"
    script = ("import argparse\nparser = argparse.ArgumentParser()\n"
              "parser.add_argument('--output', required=True)\n"
              "parser.add_argument('--formulas', action='store_true')\nparser.parse_args()\n")
    skill = snapshot("xlsx", XLSX, body, materials={"scripts/build.py": script})
    install_collection(monkeypatch, xlsx=skill)
    with TestClient(create_app()) as client:
        task = "把数据导出成 Excel 工作簿"
        direct = client.post("/api/discriminator/import-url", json={
            "task": task, "url": skill["source"]["canonical_url"],
        }).json()["report"]
        ranked = client.post("/api/discriminator/recommend", json={"task": task, "limit": 1}).json()
    assert direct["package"]["findings"] == []
    assert direct["package"]["command_observations"][0]["result"]["reasons"] == ["bracketed_optional_argument_notation"]
    assert ranked["pipeline"]["assessment"]["counts"] == {"eligible": 1}
    assert len(ranked["recommendations"]) == 1


@pytest.mark.parametrize(("task", "description", "body", "expected"), [
    ("识别扫描PDF中的文字", "A guide for reading text from PDF files.", "OCR is not supported here.", "declared_out_of_scope"),
    ("提取PDF表格", PDF, "Nested cells are not supported.", "eligible"),
    ("用cuDF做分组聚合", "Optimize GPU workloads for a variety of frameworks.", "cuDF can also perform a groupby.", "insufficient_task_evidence"),
    ("制作前端界面", "Review this frontend interface for layout compliance.", "Check keyboard focus first.", "insufficient_task_evidence"),
    ("审查前端界面可访问性", "Review this frontend interface for accessibility.", "Check keyboard focus first.", "eligible"),
    ("用浏览器测试本地网页表单", "Anti-detect browser with helpful Playwright browser tests.", "Inspect browser output.", "declared_out_of_scope"),
    ("编辑现有 Word DOCX 文档", "Convert DOCX inputs to Markdown.", "Preserve headings in the Markdown result.", "insufficient_task_evidence"),
    ("把记录导出成Excel工作簿", "Read .xlsx and list the number of rows.", "Formats: Excel workbook.", "insufficient_task_evidence"),
    ("把表格整理成Excel工作簿", "Convert an Excel workbook into a Markdown file.", "Output a Markdown table.", "insufficient_task_evidence"),
    ("把记录导出成Excel工作簿", "Create contact records from an Excel workbook.", "Import those records into the contacts list.", "insufficient_task_evidence"),
    ("制作 PPTX 演示文稿", "Publish an existing presentation to GitHub Pages.", "Install python-pptx to convert the input to HTML.", "insufficient_task_evidence"),
])
def test_matches_explicit_scope_instead_of_nearby_terms(monkeypatch, task, description, body, expected):
    install_collection(monkeypatch, selected=snapshot("selected", description, body))
    result = pt.recommend(task, {}, limit=3, search_online=False, use_model=False)
    assert result["pipeline"]["assessment"]["candidates"][0]["status"] == expected
    assert bool(result["recommendations"]) is (expected == "eligible")


def test_browser_automation_without_playwright_cannot_cover_named_tool(monkeypatch):
    install_collection(monkeypatch,
                       generic=snapshot("generic", "Test web pages by browser automation.", "Uses CDP without Playwright."),
                       playwright=snapshot("playwright", "Run Playwright tests against local web apps."))
    result = pt.recommend("用 Playwright 测试本地网页", {}, limit=2, search_online=False, use_model=False)
    assert [item["name"] for item in result["recommendations"]] == ["playwright"]
    assert result["pipeline"]["assessment"]["counts"]["declared_out_of_scope"] == 1


def test_named_plot_tool_and_output_require_own_evidence(monkeypatch):
    install_collection(monkeypatch,
                       generic=snapshot("generic", "Create scatter plots.", "Save the diagram as SVG."),
                       png=snapshot("png", "Use Matplotlib to make scatter plots.", "Save the plot as PNG."),
                       mixed=snapshot("mixed", "Use Matplotlib to make scatter plots.", "Plotly exports plots to SVG."),
                       svg=snapshot("svg", "Use Matplotlib to make scatter plots.", "Save the plot as SVG."))
    result = pt.recommend("Create a Matplotlib scatter plot and export it as SVG.", {},
                          limit=3, search_online=False, use_model=False)
    assert [item["name"] for item in result["recommendations"]] == ["svg"]
    match = result["recommendations"][0]["matches"][0]
    assert "Matplotlib" in match["evidence"]["quote"]
    assert "SVG" in match["output_evidence"]["quote"]
    assert result["pipeline"]["assessment"]["counts"]["insufficient_task_evidence"] == 3


def test_platform_specialization_limits_diagram_match(monkeypatch):
    install_collection(monkeypatch,
                       azure=snapshot("azure-diagrams", "Create Mermaid flowcharts for Azure resources."),
                       converter=snapshot("converter", "Create SVG flowcharts from existing Mermaid source.",
                                          "Imports draw.io, Mermaid, or PlantUML."),
                       standalone=snapshot("flowcharts", "Create Mermaid flowcharts for generic processes."))
    result = pt.recommend("用 Mermaid 生成流程图", {}, limit=2, search_online=False, use_model=False)
    assert [item["name"] for item in result["recommendations"]] == ["flowcharts"]
    assert result["pipeline"]["assessment"]["counts"]["declared_out_of_scope"] == 1
    assert result["pipeline"]["assessment"]["counts"]["insufficient_task_evidence"] == 1


def test_react_accessibility_needs_keyboard_evidence(monkeypatch):
    install_collection(monkeypatch,
                       vague=snapshot("vague", "Create React frontend interfaces with accessible design."),
                       keyboard=snapshot("keyboard", "Create React frontend interfaces.",
                                         "Use keyboard-accessible controls and visible focus."))
    result = pt.recommend("Build a React frontend with keyboard navigation.", {},
                          limit=2, search_online=False, use_model=False)
    assert [item["name"] for item in result["recommendations"]] == ["keyboard"]
    assert "visible focus" in result["recommendations"][0]["matches"][0]["output_evidence"]["quote"]


def test_name_and_description_can_limit_a_tool_skill_to_one_sdk(monkeypatch):
    install_collection(monkeypatch,
                       scoped=snapshot("orbit-proptest", "Write property tests for the Orbit SDK using proptest"),
                       general=snapshot("proptest", "Write Rust parser property tests using proptest"))
    generic = pt.recommend("Write Rust parser tests using `proptest`.", {}, limit=3, search_online=False, use_model=False)
    named = pt.recommend("Test the Orbit SDK with a parser using `proptest`.", {},
                         limit=3, search_online=False, use_model=False)
    assert [item["name"] for item in generic["recommendations"]] == ["proptest"]
    assert generic["pipeline"]["assessment"]["counts"]["declared_out_of_scope"] == 1
    assert {item["name"] for item in named["recommendations"]} == {"proptest", "orbit-proptest"}


def test_exact_selection_fills_a_gap_under_the_same_budget():
    supported = [set("abde"), set("abc"), set("def")]
    assert pt.complete_coverage(supported, [0, 1, 2], 2) == [1, 2]
    assert pt.complete_coverage(supported, [0, 1, 2], 3) == [0, 1, 2]


def test_positive_paired_lift_breaks_ties_for_known_conditions(monkeypatch):
    monkeypatch.delenv("PAN_BIE_QI_E5_DIR", raising=False)
    task = plan("Resolve Git merge conflicts")
    docs = [pt.document(name, "", name) for name in ("first", "second")]
    reviews = [pt.Review(
        Candidate("", name, name, "fixture", None), doc,
        {"assessment": {"dimensions": [{"score": 90, "lift": lift, "applies_to_current_task": True}]}},
        {}, [{"id": "git_conflict"}], [], None,
    ) for doc, name, lift in zip(docs, ("first", "second"), (-10, 8), strict=True)]
    (chosen, _), = pt.select(reviews, task, 1)[0]
    assert chosen.candidate.key == "second/second"
    reviews[1].report["assessment"]["dimensions"][-1]["applies_to_current_task"] = False
    (chosen, _), = pt.select(reviews, task, 1)[0]
    assert chosen.candidate.key == "first/first"


@pytest.mark.parametrize(("expression", "license_file", "expected", "identity"), [
    ("MIT", None, "recognized", "MIT"),
    ("CC-BY-4.0 AND Apache-2.0", None, "recognized", "CC-BY-4.0 AND Apache-2.0"),
    ("MIT AND Proprietary", None, "restricted", None),
    ("MIT", "Proprietary license.", "restricted", None),
    ("MIT", "Apache-2.0", "unconfirmed", None),
    ("README", None, "unconfirmed", None),
])
def test_license_evidence_checks_pinned_package_before_inheritance(expression, license_file, expected, identity):
    record = snapshot("sample", PDF, licence=expression, license_text=license_file)
    result = license_evidence(record)
    assert (result["state"], result["id"]) == (expected, identity)


def test_registry_only_accepts_valid_github_shape(monkeypatch):
    records = {"skills": [
        {"source": "example/works", "skillId": "sheet", "installs": 10},
        {"source": "example.internal", "skillId": "sheet", "installs": 11},
        {"source": "example/works", "skillId": "../secret", "installs": 11},
    ]}
    monkeypatch.setattr(pt_discovery, "fetch_url", lambda *_a, **_k: {"state": "fetched", "raw_body": json.dumps(records).encode()})
    listed, candidates = pt_discovery.discover(plan("帮我核对Excel工作簿中的公式"), [], float("inf"), search_online=True)
    remote = [row.key for row in listed if row.discovery == "skills.sh"]
    assert remote == ["example/works/sheet"]
    assert all(row["top_keys"] == remote for row in candidates["searched"])


def test_deepseek_search_adds_github_candidates_and_reuses_successful_search(monkeypatch):
    calls = []
    url = "https://github.com/example/diagrams/blob/main/skills/mermaid/SKILL.md"

    class Reply:
        status = 200

        def __init__(self, used_tool):
            self.used_tool = used_tool

        def read(self, limit):
            assert limit >= 100_000
            if not self.used_tool:
                return json.dumps({"choices": [{"message": {"content": url}}]}).encode()
            return json.dumps({
                "choices": [{"message": {
                    "content": '{"queries":["mermaid flowchart","https://invalid.test"]}',
                    "server_tool_calls": [{"calls": [{
                        "type": "deepseek_web_search", "status": "completed",
                        "sources": [
                            {"url": url + "?tracking=test#L13"},
                            {"url": url},
                            {"url": "https://github.com.example.net/example/diagrams"},
                            {"url": "https://github.com/example/diagrams/blob/main/README.md"},
                        ],
                    }]}],
                }}],
                "usage": {"total_tokens": 2500},
            }).encode()

    class Provider:
        def __init__(self, host, timeout):
            assert host == "api.deepseek.com" and 0 < timeout <= 11

        def request(self, method, route, body, headers):
            assert (method, route) == ("POST", "/chat/completions")
            assert headers["Authorization"] == "Bearer fixture-public-search"
            request = json.loads(body)
            assert request["tools"] == [{"type": "deepseek_web_search"}]
            assert request["thinking"] == {"type": "disabled"}
            assert request["max_tokens"] == 180
            assert "site:github.com" in request["messages"][0]["content"]
            calls.append(request)

        def getresponse(self):
            return Reply(used_tool=len(calls) > 1)

        def close(self):
            pass

    monkeypatch.setattr(pt_discovery.http.client, "HTTPSConnection", Provider)
    monkeypatch.setattr(pt_discovery, "_SEARCH_CACHE", OrderedDict())
    monkeypatch.setattr(pt_discovery, "fetch_url", lambda *_a, **_k: {
        "state": "fetched", "raw_body": b'{"skills":[{"source":"example/catalog","skillId":"flowchart"}]}',
    })
    task = plan("用 Mermaid 画部署流程图")
    initial, initial_meta = pt_discovery.discover(
        task, [], float("inf"), search_online=True, deepseek_api_key="fixture-public-search",
    )
    assert all(row.discovery != pt_discovery.DEEPSEEK_WEB for row in initial)
    assert initial_meta["searched"][0]["state"] == "not_used"
    for _ in range(2):
        listed, meta = pt_discovery.discover(
            task, [], float("inf"), search_online=True, deepseek_api_key="fixture-public-search",
        )
        online = [row for row in listed if row.discovery != "reference_catalog"]
        assert [row.discovery for row in online] == [pt_discovery.DEEPSEEK_WEB, "skills.sh"]
        assert online[0].url == url
        assert meta["searched"][0]["state"] == "searched"
        assert meta["searched"][0]["hits"] == 1
        assert meta["searched"][0]["terms"] == ["mermaid flowchart"]
    assert len(calls) == 2


def test_deepseek_quota_does_not_stop_catalog_search(monkeypatch):
    monkeypatch.setattr(pt_discovery, "_deepseek_web_search", lambda *_: (
        {"provider": pt_discovery.DEEPSEEK_WEB, "state": "balance_unavailable", "hits": 0}, [],
    ))
    monkeypatch.setattr(pt_discovery, "fetch_url", lambda *_a, **_k: {
        "state": "fetched", "raw_body": b'{"skills":[{"source":"example/catalog","skillId":"flowchart"}]}',
    })
    listed, meta = pt_discovery.discover(
        plan("用 Mermaid 画部署流程图"), [], float("inf"),
        search_online=True, deepseek_api_key="fixture-unavailable-key",
    )
    assert [row.discovery for row in listed if row.discovery != "reference_catalog"] == ["skills.sh"]
    assert meta["searched"][0]["state"] == "balance_unavailable"


def test_deepseek_planner_accepts_terms_and_discards_urls(monkeypatch):
    monkeypatch.setenv("PAN_BIE_QI_DEEPSEEK_API_KEY", "fixture-model-access")

    class Reply:
        status = 200

        def read(self, limit):
            assert limit == 24_000
            result = {"choices": [{"message": {"content": json.dumps({"queries": ["pdf OCR", "https://invalid.test"]})}}],
                      "usage": {"total_tokens": 83}}
            return json.dumps(result).encode()

    class Provider:
        def __init__(self, host, timeout):
            assert host == "api.deepseek.com" and timeout == 6

        def request(self, method, route, body, headers):
            parsed = json.loads(body)
            assert method == "POST" and route == "/chat/completions"
            assert parsed["max_tokens"] == 140 and len(parsed["messages"]) == 2
            assert headers["Authorization"] == "Bearer fixture-model-access"

        def getresponse(self):
            return Reply()

        def close(self):
            pass

    monkeypatch.setattr(pt_model.http.client, "HTTPSConnection", Provider)
    terms, metadata = pt_model.suggestions("识别扫描 PDF", requested=True)
    assert terms == ["pdf ocr"]
    assert metadata["state"] == "used" and metadata["calls"] == 1 and metadata["tokens"] == 83
