"""Attach limited PDF task guidance with citations from inspected source text."""

from __future__ import annotations

import re
import shutil

from .inspection import citation


def _first_line(files: dict[str, str], pattern: str) -> dict | None:
    for path, text in files.items():
        if not path.casefold().endswith(".md"):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if re.search(pattern, line, re.IGNORECASE):
                return citation(files, path, number)
    return None


def pdf_guidance(task: str, files: dict[str, str], python_imports: list[str]) -> dict | None:
    asks_for_pdf = re.search(r"\bpdf\b|\.pdf|扫描件|扫描版|\bocr\b", task, re.IGNORECASE)
    if not asks_for_pdf:
        return None
    asks_for_scan = re.search(r"扫描|无文字层|没有文字层|纯图|image.only|\bocr\b", task, re.IGNORECASE)
    ocr_evidence = _first_line(files, r"\bimport\s+pytesseract\b|\btesseract\s+['\w-]|\bpytesseract\.")
    layout_evidence = _first_line(files, r"\bpdftotext\s+-layout\b|extract_text\(\s*layout\s*=\s*True")
    table_evidence = _first_line(files, r"\bextract_tables?\s*\(") or _first_line(files, r"\bpdfplumber\b")
    if not (table_evidence or layout_evidence or ocr_evidence):
        return None

    evidence = [x for x in (table_evidence, layout_evidence, ocr_evidence) if x]
    if asks_for_scan:
        tesseract = shutil.which("tesseract") is not None
        return {
            "kind": "pdf_scan",
            "title": "扫描 PDF 的 OCR 依赖",
            "summary": "先确认页面是否有可读文字层。图像页需要 OCR 工具和匹配的语言包。",
            "checks": [
                {"name": "Tesseract OCR", "status": "available_on_demo_host" if tesseract else "not_found_on_demo_path",
                 "detail": "本机 PATH 已找到 Tesseract。" if tesseract else "本机 PATH 未发现 Tesseract。"},
                {"name": "中文等对应语言包", "status": "unverified", "detail": "还需核对目标环境的语言包和 OCR 结果。"},
            ],
            "evidence": evidence,
            "probe_available": True,
        }
    if not re.search(r"表格|表\s*\d|列名|双栏|\bcsv\b|\btable", task, re.IGNORECASE) or not (table_evidence or layout_evidence):
        return None
    suggested = []
    if table_evidence or "pdfplumber" in python_imports:
        suggested.append({"name": "pdfplumber 表格识别", "status": "documented", "detail": "先从网格表中识别行列。"})
    suggested.append({"name": "保留版面的文本提取", "status": "local_validator", "detail": "双栏或无竖线的表，按目标列名和行数定位。"})
    return {
        "kind": "pdf_table",
        "title": "PDF 表格提取和验证计划",
        "summary": "选择目标页，先试网格识别，再按版面和列名提取。可用参考 CSV 逐格核对。",
        "checks": suggested,
        "evidence": evidence,
        "probe_available": True,
    }
