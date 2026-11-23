"""Local extraction and independent comparison for text PDF tables."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from collections import Counter

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError
from pdfminer.psparser import PSEOF
from pdfplumber.utils.exceptions import PdfminerException

MAX_PDF_BYTES = 4_000_000
MAX_CSV_BYTES = 64_000
MAX_TABLE_ROWS = 500
MAX_TABLE_COLUMNS = 30
MAX_PAGE_CHARS = 40_000
MAX_PAGE_EDGES = 1_200
METHOD_LABELS = {"grid": "网格表格识别", "layout": "保留版面的文本提取"}


def normalize(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def safe_csv_cell(value: str) -> str:
    stripped = value.lstrip()
    is_number = re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?%?", stripped)
    if stripped.startswith(("=", "+", "-", "@")) and not is_number:
        return "'" + value
    return value


def reference_rows(text: str) -> list[list[str]] | None:
    if not text.strip():
        return None
    if len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise ValueError("参考 CSV 需小于 64 KB。")
    try:
        reader = csv.reader(io.StringIO(text.removeprefix("\ufeff")), strict=True)
        rows = [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]
    except csv.Error as exc:
        raise ValueError("参考 CSV 格式无法读取。") from exc
    if len(rows) < 2 or len(rows) > MAX_TABLE_ROWS + 1:
        raise ValueError("参考 CSV 需要一行列名和 1 至 500 行数据。")
    header = [normalize(cell) for cell in rows[0]]
    if not 2 <= len(header) <= MAX_TABLE_COLUMNS or not all(header) or len(set(header)) != len(header):
        raise ValueError("参考 CSV 需要 2 至 30 个互不重复的列名。")
    if any(len(row) != len(header) for row in rows[1:]):
        raise ValueError("参考 CSV 中各行的列数必须与表头一致。")
    keys = [normalize(row[0]) for row in rows[1:]]
    if not all(keys) or len(set(keys)) != len(keys):
        raise ValueError("参考 CSV 的第一列需要包含非空且不重复的行名。")
    return rows


def header_matches(row: list[str], wanted: list[str]) -> bool:
    allowed = (r"(?:\d+|n=\d+|%|％|百分点|百分比|元|秒|万元|千元|人|项|件|个|例|组|次|分|ms|s|kg|g|m|cm|mm|km)")
    if len(row) != len(wanted):
        return False
    for source, expected in zip(row, wanted, strict=True):
        raw, key = normalize(source), normalize(expected)
        suffix = raw.removeprefix(key)
        if raw != key and not (raw.startswith(key) and re.fullmatch(r"\(" + allowed + r"\)|\[" + allowed + r"\]", suffix)):
            return False
    return True


def layout_candidates(page, wanted: list[str], expected_count: int | None) -> list[list[list[str]]]:
    parts = [re.split(r"[ \t\u3000]{2,}", line.strip()) for line in page.extract_text(layout=True).splitlines() if line.strip()]
    candidates = []
    for index, header in enumerate(parts):
        if not header_matches(header, wanted):
            continue
        rows = []
        for row in parts[index + 1:index + MAX_TABLE_ROWS + 1]:
            if len(row) != len(header) or not row[0] or header_matches(row, wanted):
                break
            rows.append(row)
            if expected_count is not None and len(rows) > expected_count:
                break
        if rows:
            candidates.append([header, *rows])
    return candidates


def grid_candidates(page, wanted: list[str]) -> list[list[list[str]]]:
    tables = []
    for raw_table in page.extract_tables():
        table = [[(cell or "").strip() for cell in row] for row in raw_table if any(cell and cell.strip() for cell in row)]
        if len(table) > 1 and (not wanted or header_matches(table[0], wanted)):
            tables.append(table)
    return tables


def grade(actual: list[list[str]], reference: list[list[str]]) -> dict:
    comparable = all(len(row) == len(reference[0]) for row in actual)
    expected_keys = {normalize(row[0]) for row in reference[1:]}
    index = {normalize(row[0]): row[1:] for row in actual[1:] if row}
    main_keys = Counter(normalize(row[0]) for row in actual[1:] if row)
    missing = [row[0] for row in reference[1:] if normalize(row[0]) not in index]
    unexpected = list(dict.fromkeys(row[0] for row in actual[1:] if row and normalize(row[0]) not in expected_keys))
    duplicates = [key for key, n in main_keys.items() if n > 1]
    differences = []
    same_cells = same_rows = 0
    for label, *values in reference[1:]:
        key = normalize(label)
        got = index.get(key)
        if not got or main_keys[key] != 1 or len(got) != len(values):
            continue
        correct = 0
        for column, expected, observed in zip(reference[0][1:], values, got, strict=True):
            if unicodedata.normalize("NFKC", expected).strip() == unicodedata.normalize("NFKC", observed).strip():
                correct += 1
            elif len(differences) < 12:
                differences.append({"row": label, "column": column, "expected": expected, "actual": observed})
        same_cells += correct
        same_rows += correct == len(values)
    passed = comparable and len(actual) == len(reference) and same_rows == len(reference) - 1
    return {
        "state": "verified_match" if passed else "disagreement",
        "matched_rows": same_rows,
        "reference_rows": len(reference) - 1,
        "matched_cells": same_cells,
        "reference_cells": (len(reference) - 1) * (len(reference[0]) - 1),
        "missing_rows": missing[:12],
        "unexpected_rows": unexpected[:12],
        "duplicate_rows": duplicates[:12],
        "differences": differences,
    }


def probe(data: bytes, page_number: int, csv_reference: str, header_text: str, expected_count: int | None) -> dict:
    if len(data) > MAX_PDF_BYTES or b"%PDF-" not in data[:1024]:
        raise ValueError("请选择不超过 4 MB 的 PDF 文件。")
    if page_number < 1:
        raise ValueError("页码从 1 开始。")
    reference = reference_rows(csv_reference)
    wanted = reference[0] if reference else [s.strip() for s in re.split(r"[|｜]", header_text) if s.strip()]
    if header_text.strip() and not reference and (
        not 2 <= len(wanted) <= MAX_TABLE_COLUMNS or len({normalize(name) for name in wanted}) != len(wanted)
    ):
        raise ValueError("请用 | 分隔 2 至 30 个互不重复的目标列名。")
    frame = {"file_sha256": hashlib.sha256(data).hexdigest(), "page": page_number,
             "model_api_calls": 0, "skill_code_executed": False, "skill_effect_measured": False}
    try:
        with pdfplumber.open(io.BytesIO(data)) as document:
            if page_number > len(document.pages):
                raise ValueError("页码超出 PDF 范围。")
            page = document.pages[page_number - 1]
            frame["pages"] = len(document.pages)
            # No rendering or image decoding is required for a text-layer probe.
            chars = page.chars
            if len(chars) > MAX_PAGE_CHARS:
                return {**frame, "state": "page_too_complex", "reason": "所选页的字符数超出试跑范围。请选择更小的页面。"}
            text_layer = any(char.get("text", "").strip() for char in chars)
            if not text_layer:
                return {**frame, "state": "needs_ocr", "reason": "所选页没有可读文字层。请准备 OCR 工具及中文等对应语言包，生成带文字层的 PDF 后重试。"}
            grid = grid_candidates(page, wanted) if len(page.edges) <= MAX_PAGE_EDGES else []
            if len(grid) == 1:
                method, candidates = "grid", grid
            elif len(grid) > 1:
                return {**frame, "state": "ambiguous_table", "reason": "找到多张符合条件的网格表。请补充目标列名或换到更具体的页面。"}
            elif wanted:
                method, candidates = "layout", layout_candidates(page, wanted, len(reference) - 1 if reference else expected_count)
            else:
                return {**frame, "state": "needs_headers", "reason": "网格识别未找到表格。请填写目标列名或上传参考 CSV，以尝试版面提取。"}
    except (PdfminerException, PDFSyntaxError, PDFEncryptionError, PDFPasswordIncorrect, PSEOF, KeyError, IndexError, TypeError,
            OverflowError, RecursionError) as exc:
        raise ValueError("PDF 无法解析，文件可能损坏或需要解密。") from exc
    if len(candidates) != 1:
        reason = "在指定页上找到了重复表头。请换页或使用更具体的目标列。" if candidates else "指定页未提取到目标表。请核对页码和列名。"
        return {**frame, "state": "ambiguous_table" if candidates else "table_not_found", "reason": reason}

    header, *rows = candidates[0]
    if len(header) > MAX_TABLE_COLUMNS or len(rows) > MAX_TABLE_ROWS:
        return {**frame, "state": "table_too_large", "reason": "已提取的表格超过 500 行或 30 列。请缩小输入范围。"}
    if method == "layout" and not reference and expected_count is None:
        return {**frame, "state": "needs_row_count", "reason": "识别到目标表头。请填写预期的数据行数，或上传参考 CSV，以检查提取范围。"}
    if any(len(row) != len(header) for row in rows):
        return {**frame, "state": "inconsistent_table", "reason": "提取出的行列不一致。请检查合并单元格或提供更明确的表头。"}
    verification = grade([header, *rows], reference) if reference else {"state": "no_reference"}
    shape_matches = expected_count is None or expected_count == len(rows)
    if not shape_matches:
        verification["state"] = "disagreement" if reference else "shape_mismatch"
        verification["expected_rows"] = expected_count
        verification["actual_rows"] = len(rows)
    report_header = reference[0] if reference else header
    table_csv = io.StringIO(newline="")
    csv.writer(table_csv).writerows([[safe_csv_cell(cell) for cell in row] for row in [report_header, *rows]])
    return {**frame, "state": "extracted", "method": method, "method_label": METHOD_LABELS[method],
            "column_count": len(header), "row_count": len(rows), "header": report_header, "preview": rows[:10],
            "csv": table_csv.getvalue(), "verification": verification,
            "reference_sha256": hashlib.sha256(csv_reference.encode()).hexdigest() if reference else None}
