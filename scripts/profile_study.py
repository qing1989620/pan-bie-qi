"""Rebuild the fixed public comparison from documentation and local PDF outputs."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pdfplumber
from reportlab import Version
from reportlab.pdfgen import canvas

from skill_discriminator import profile
from skill_discriminator.inspection import inspect
from skill_discriminator.pdf_table import grade, grid_candidates, probe

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "docs/skill-discriminator-paper.pdf"
REFERENCES = ROOT / "docs/examples/paper-table-6.csv"
PUBLIC = [
    ("anthropic-pdf", "Anthropic PDF", "anthropics/skills", "e4bb5d960d87126ff3b3a8af1a104b7db67560ba", "skills/pdf",
     [(93, "给出了默认表格提取示例。"), (197, "版面文本和扫描 OCR 分别有示例。")]),
    ("openai-pdf", "OpenAI PDF", "openai/skills", "49f948faa9258a0c61caceaf225e179651397431", "skills/.curated/pdf",
     [(15, "强调把页面渲染成图片做目视复核。"), (57, "明确列出排版和最终交付检查。")]),
    ("hermes-pdf", "Nous Research PDF", "NousResearch/hermes-agent", "a15b9710395aa1c4858fb6dd9e4c5014c5ced4e9",
     "skills/productivity/pdf", [(88, "给出导出 CSV 的入口。"), (146, "明确提示无边框和合并单元格可能需要调参。")]),
    ("anthropic-xlsx", "Anthropic XLSX", "anthropics/skills", "e4bb5d960d87126ff3b3a8af1a104b7db67560ba", "skills/xlsx",
     [(20, "检查公式错误、现有公式保留及工作簿交付规范。"), (30, "解释公式重算、缓存和重算失败。")]),
    ("hermes-xlsx", "Nous Research XLSX", "NousResearch/hermes-agent", "dc82bec3bd4d2d63e25c1854f5f2b081f05aad38",
     "skills/productivity/xlsx", [(22, "列出工作簿编辑与 CSV 转换。"), (174, "写明 CSV 类型推断与编码处理。")]),
]
SOURCE_HASHES = {
    "anthropic-pdf": "d1c7caf5f35eb7d37877dd7fc33a095037be2b34fb5d2f7f4d43bde3165bb4ee",
    "openai-pdf": "d108cf2b36355ab37eb5962933f4d09785ec002f3105c506129320209306b9d2",
    "hermes-pdf": "11126ee5460ff6b0699514198a46ac7c2ff2dd4c6c03ac99b81ac3f5b9d37cf3",
    "anthropic-xlsx": "f32b1c2b5d5d31e02964df110d6489613d9345f86830c118cfe24159d8448ce6",
    "hermes-xlsx": "ffe15d1907e1f68b4579dcee0cb1da2193a28c3ff03ec6294061bc9e19e0ff4e",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pdf_bytes(write_pages, content) -> bytes:
    buffer = io.BytesIO()
    sheet = canvas.Canvas(buffer, pagesize=(612, 792), invariant=1)
    sheet.setFont("Helvetica", 11)
    write_pages(sheet, content)
    sheet.save()
    return buffer.getvalue()


def text_cases() -> list[tuple[str, str, bytes, list[str]]]:
    specifications = [
        ("lines", "单栏普通文字", [[(48, 720, "Packing register"), (48, 696, "Box count: 27")]],
         ["Packing register\nBox count: 27"]),
        ("pages", "多页独立段落", [[(48, 720, "First delivery")], [(48, 720, "Second delivery")]],
         ["First delivery", "Second delivery"]),
        ("meaning", "数字、负号与百分号", [[(48, 720, "Balance: -427.50 USD"), (48, 696, "Change: +12.4%")]],
         ["Balance: -427.50 USD\nChange: +12.4%"]),
        ("columns", "双栏阅读顺序", [[(48, 720, "Left alpha"), (48, 695, "Left beta"),
                                   (320, 720, "Right alpha"), (320, 695, "Right beta")]],
         ["Left alpha\nLeft beta\nRight alpha\nRight beta"]),
    ]

    def render(sheet, pages):
        for page in pages:
            for x, y, value in page:
                sheet.drawString(x, y, value)
            sheet.showPage()

    return [(key, label, pdf_bytes(render, pages), expected)
            for key, label, pages, expected in specifications]


def table_cases() -> list[tuple]:
    rows = [["Region", "Units", "Revenue"], ["North", "14", "12.40"], ["West", "7", "-3.20"], ["East", "11", "8.90"]]

    def render(sheet, mode):
        x, y, height, width = 44, 704, 25, 150
        if mode in {"grid", "merged", "grid-caption"}:
            for i in range(len(rows) + 1):
                sheet.line(x, y - i * height, x + len(rows[0]) * width, y - i * height)
            for i in range(len(rows[0]) + 1):
                limit = 2 if mode == "merged" and i == 2 else 0
                sheet.line(x + i * width, y - limit * height, x + i * width, y - len(rows) * height)
        if mode == "grid-caption":
            sheet.drawString(x, y + 34, "Delivery by region")
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                if mode == "merged" and i == 1 and j == 2:
                    continue
                if mode == "merged" and i == 1 and j == 1:
                    value = "14 / 12.40"
                sheet.drawString(x + j * width + 5, y - i * height - 15, value)
        if mode == "borderless-footer":
            sheet.drawString(x + 5, y - len(rows) * height - 15, "Source")
            sheet.drawString(x + width + 5, y - len(rows) * height - 15, "FY26")
            sheet.drawString(x + 2 * width + 5, y - len(rows) * height - 15, "Audit")

    fixtures = [
        ("grid", "有网格三列表格"), ("grid-caption", "带表题的网格表"),
        ("borderless", "无边框三列表格"), ("borderless-footer", "无边框表格紧接同列脚注"),
        ("merged", "跨列合并的数值单元格"),
    ]
    cases = [(key, label, pdf_bytes(render, key), 1, rows) for key, label in fixtures]
    paper_rows = list(csv.reader(io.StringIO(REFERENCES.read_text(encoding="utf-8-sig"))))
    return cases + [("paper", "真实中文论文表 6", PAPER.read_bytes(), 4, paper_rows)]


def score_text() -> list[dict]:
    outcomes = []
    for case, label, data, expected in text_cases():
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            actual = [page.extract_text() or "" for page in pdf.pages]
        expected_words = [page.split() for page in expected]
        actual_words = [page.split() for page in actual]
        outcomes.append({
            "id": case, "label": label, "input_sha256": sha(data),
            "expected": expected, "actual": actual, "passed": expected_words == actual_words,
        })
    return outcomes


def score_tables() -> tuple[list[dict], list[dict]]:
    default, verified = [], []
    for case, label, data, page, reference in table_cases():
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            found = grid_candidates(pdf.pages[page - 1], reference[0])
        actual = found[0] if len(found) == 1 else []
        check = grade(actual, reference)
        common = {"id": case, "label": label, "input_sha256": sha(data), "page": page,
                  "expected": reference, "reference_cells": check["reference_cells"]}
        default.append({**common, "actual": actual, "passed": check["state"] == "verified_match",
                        "matched_cells": check["matched_cells"]})
        headers = " | ".join(reference[0])
        result = probe(data, page, "", headers, len(reference) - 1)
        extracted = list(csv.reader(io.StringIO(result.get("csv", ""))))
        validation = grade(extracted, reference)
        verified.append({
            **common, "actual": extracted, "passed": validation["state"] == "verified_match",
            "matched_cells": validation["matched_cells"], "extraction_state": result["state"],
        })
    return default, verified


def summary(cases: list[dict], method: str, line: int) -> dict:
    result = {
        "method": method, "source_line": line, "passed": sum(row["passed"] for row in cases), "total": len(cases),
        "cases": [{key: row[key] for key in ("id", "label", "passed")} for row in cases],
    }
    if "matched_cells" in cases[0]:
        result["cells"] = sum(row["matched_cells"] for row in cases)
        result["total_cells"] = sum(row["reference_cells"] for row in cases)
    return result


def snapshot(key, repository, commit, directory, cache):
    locations = f"{repository}/{commit}/{directory}/SKILL.md"
    path = cache / f"{key}.md"
    cached = path.is_file()
    if cached:
        data = path.read_bytes()
    else:
        try:
            with urlopen("https://raw.githubusercontent.com/" + locations, timeout=15) as response:
                data = response.read(600_001)
        except (URLError, TimeoutError):
            url = f"https://api.github.com/repos/{repository}/contents/{directory}/SKILL.md?ref={commit}"
            with urlopen(url, timeout=15) as response:
                data = base64.b64decode(json.loads(response.read(1_000_000))["content"])
    if SOURCE_HASHES[key] != sha(data):
        raise ValueError(f"Document bytes do not match the pinned digest for {key}")
    if not cached:
        path.write_bytes(data)
    return data


def build(cache: Path) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    text, (grid, scoped) = score_text(), score_tables()
    text_lines = {"anthropic-pdf": 85, "openai-pdf": 19, "hermes-pdf": 87}
    table_lines = {"anthropic-pdf": 95, "hermes-pdf": 88}
    peers = []
    for key, name, repo, commit, directory, review in PUBLIC:
        path = directory + "/SKILL.md"
        raw = snapshot(key, repo, commit, directory, cache)
        document = raw.decode("utf-8-sig")
        response = inspect(key, {"SKILL.md": document}, [], False, {})
        desc = profile.describe({"SKILL.md": document}, response["introduction"], response["package"])
        url = f"https://github.com/{repo}/blob/{commit}/{path}"
        measure = {}
        if key in text_lines:
            measure["text"] = summary(text, "pdfplumber 默认逐页文字提取", text_lines[key])
        if key in table_lines:
            measure["table"] = summary(grid, "pdfplumber 默认网格表格提取", table_lines[key])
        peers.append({
            "name": name, "repository": repo, "commit": commit, "skill_path": path, "url": url, "sha256": sha(raw),
            "family": desc["family"], "characters": desc["size"]["document_characters"],
            "documentation": {"found": desc["documentation"]["found"], "total": desc["documentation"]["total"]},
            "documented": [row["id"] for row in desc["capabilities"] if row["state"] == "declared"],
            "review": [{"line": line, "text": remark} for line, remark in review], "measurements": measure or None,
        })
    return {
        "schema": "skill-profiles-v1", "protocol": {
            "scope": "按固定版本说明复跑公开的读取步骤，逐页文字按内容和顺序验收，表格按已登记的列名和数据格验收。",
            "environment": {"pdfplumber": pdfplumber.__version__, "reportlab": Version},
            "runtime": "使用本地库复跑已写明的步骤，未运行上游 Skill 包、模型、OCR 或人工视觉复核。",
            "hermes_pdf_read_source": (
                "https://github.com/NousResearch/hermes-agent/blob/a15b9710395aa1c4858fb6dd9e4c5014c5ced4e9/"
                "skills/productivity/pdf/scripts/pdf_read.py#L28-L48"
            ),
        },
        "peers": peers, "outcomes": {"text": text, "table": grid, "verified_table": scoped},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-cache", type=Path, default=ROOT / "tmp/profile-sources")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.docs_cache)
    args.output.write_bytes((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    for key, cases in result["outcomes"].items():
        print(f"{key}: {sum(row['passed'] for row in cases)}/{len(cases)}")


if __name__ == "__main__":
    main()
