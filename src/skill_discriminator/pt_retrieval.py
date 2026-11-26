"""Task cues and a corpus-independent lexical ranking used by PT."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .inspection import frontmatter
from .profile import HEADER

TOKEN_REGIONS = re.compile(r"[a-z][a-z0-9]*|[\u3400-\u9fff]+")
STOP = {
    "a", "about", "after", "all", "am", "an", "and", "any", "are", "as", "at", "be", "been", "before",
    "both", "by", "can", "data", "do", "does", "done", "each", "file", "files", "for", "from", "give",
    "go", "have", "helps", "here", "how", "i", "if", "in", "into", "is", "it", "like", "make", "me",
    "more", "my", "need", "new", "now", "of", "on", "one", "only", "or", "our", "output", "please",
    "project", "re", "report", "run", "should", "skill", "skills", "some", "step", "support", "task",
    "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "to", "use",
    "used", "using", "want", "was", "what", "when", "where", "which", "will", "with", "workflow",
    "working", "would", "you",
}
CHINESE_STOP = {"一个", "我们", "需要", "请帮", "可以", "希望", "用于", "生成", "输出", "完成", "如何", "使用", "进行", "同时"}
ALIAS = {
    "extracting": "extract", "extraction": "extract", "extracts": "extract",
    "tables": "table", "formulas": "formula", "workbooks": "workbook",
    "spreadsheets": "spreadsheet", "presentations": "presentation", "slides": "slide",
    "merged": "merge", "merging": "merge", "scanned": "scan",
    "rendering": "render", "testing": "test", "tests": "test",
}


def terms(text: str) -> list[str]:
    output = []
    for token in TOKEN_REGIONS.findall(text.casefold()):
        if not token.isascii():
            units = [token] if len(token) == 1 else [token[i:i + 2] for i in range(len(token) - 1)]
            output.extend(unit for unit in units if unit not in CHINESE_STOP)
        elif token not in STOP:
            output.append(ALIAS.get(token, token))
    return output


@dataclass(frozen=True)
class Document:
    key: str
    name: str
    description: str
    body: str


def document(key: str, raw: str, name: str = "") -> Document:
    text = raw.removeprefix("\ufeff")
    header = HEADER.match(text)
    metadata, _ = frontmatter(text)
    body = text[header.end():] if header else text
    title = metadata.get("name")
    desc = metadata.get("description")
    if not isinstance(title, str) or not title.strip():
        heading = re.search(r"^#{1,3}\s+([^\n]+)", body, re.MULTILINE)
        title = name or (heading[1] if heading else key)
    return Document(key, title[:200], desc[:2000] if isinstance(desc, str) else "", body[:120_000])


class BM25:
    def __init__(self, documents: list[list[str]]):
        self.idf = {}
        postings = defaultdict(list)
        lengths = [len(row) for row in documents]
        average = sum(lengths) / max(len(documents), 1)
        weights = [1.2 * (.25 + .75 * length / max(average, 1.0)) for length in lengths]
        for number, words in enumerate(documents):
            for term, count in Counter(words).items():
                postings[term].append((number, 2.2 * count / (count + weights[number])))
        for term, rows in postings.items():
            self.idf[term] = math.log1p((len(documents) - len(rows) + .5) / (len(rows) + .5))
        self.postings = dict(postings)

    def score(self, query: list[str]) -> dict[int, float]:
        result = defaultdict(float)
        for term in sorted(set(query).intersection(self.postings)):
            idf = self.idf[term]
            for number, weight in self.postings[term]:
                result[number] += idf * weight
        return dict(result)


class Retrieval:
    """Run the same name/description and full-text scorers in the service and experiments."""

    def __init__(self, documents: list[Document]):
        self.documents = documents
        descriptions = [terms(f"{row.name} {row.description}") for row in documents]
        full = [desc + terms(row.body) for row, desc in zip(documents, descriptions, strict=True)]
        self.metadata = BM25(descriptions)
        self.full_text = BM25(full)
        self.vocabulary = [set(words) for words in full]

    def scores(self, query: str) -> dict[str, dict[int, float]]:
        words = terms(query)
        metadata, full = self.metadata.score(words), self.full_text.score(words)
        max_head = max(metadata.values(), default=1)
        max_body = max(full.values(), default=1)
        idf = {word: self.full_text.idf[word] for word in sorted(set(words).intersection(self.full_text.idf))}
        total = sum(idf.values()) or 1
        mixed = {
            i: .68 * full_value / max_body + .22 * metadata.get(i, 0) / max_head
            + .10 * math.fsum(idf[word] for word in idf.keys() & self.vocabulary[i]) / total
            for i, full_value in full.items()
        }
        return {"metadata": metadata, "full_text": full, "pt": mixed}

    def rank(self, scores: dict[int, float], limit: int) -> list[int]:
        return sorted(scores, key=lambda i: (-scores[i], self.documents[i].key))[:limit]


@dataclass(frozen=True)
class Requirement:
    id: str
    label: str
    query: str
    evidence: re.Pattern
    explicit_exclusion: re.Pattern | None


def requirement(key: str, label: str, query: str, pattern: str, exclusion: str = "") -> Requirement:
    return Requirement(key, label, query, re.compile(pattern, re.IGNORECASE),
                       re.compile(exclusion, re.IGNORECASE) if exclusion else None)


PDF = requirement("pdf", "PDF 处理", "pdf", r"\bpdfs?\b")
PDF_TABLE = requirement(
    "pdf_table", "提取 PDF 表格", "pdf table extraction",
    r"\bextract[_ ]tables?\b|\btable extraction\b|\bpdfplumber\b.{0,40}\btables?\b|--tables\b|提取.{0,8}表格|表格提取",
    r"\b(?:does not support|no) table extraction\b|\bcannot extract (?:pdf )?tables\b|表格提取.{0,3}不支持|不支持.{0,3}表格提取",
)
PDF_OCR = requirement(
    "ocr", "扫描件文字识别", "pdf ocr",
    r"\b(?:tesseract|pytesseract|ocrmypdf|easyocr|paddleocr)\b|\bocr\b.{0,42}(?:scann?ed|images?|text)|(?:扫描|光学字符).{0,14}(?:识别|提取文字)",
    r"\b(?:does not support|cannot perform|no (?:built[- ]in )?)\s*ocr\b|\bocr (?:is )?not supported\b|不支持.{0,5}(?:OCR|扫描件识别)",
)
PDF_TEXT = requirement(
    "pdf_text", "提取 PDF 文字", "pdf text extraction",
    r"\bextract[_ ]text\b|\bpdftotext\b|\btext extraction\b|提取.{0,10}(?:文字|文本)",
)
EXCEL = requirement(
    "xlsx", "处理 Excel 工作簿", "xlsx spreadsheet",
    r"\b(?:xlsx|openpyxl|xlsxwriter|excel workbook|excel spreadsheet)\b|Excel\s*(?:工作簿|表格|文件)|\bExcel.{0,10}(?:导入|导出)",
    r"\b(?:does not support|cannot (?:write|read|edit)) (?:[.]?xlsx|Excel workbooks?)\b|不支持.{0,4}(?:XLSX|Excel工作簿)",
)
EXCEL_OUTPUT = requirement(
    "xlsx", "输出 Excel 工作簿", "xlsx workbook",
    r"\b(?:create|generate|produce|write|save|output)(?:s|d|ing)?\s+"
    r"(?:(?:a|an|the|new|local|formatted|editable|valid|fresh|existing|resulting|structured)\s+){0,7}"
    r"(?:[.]?xlsx(?:\s+(?:file|workbook))?|excel\s+workbook|workbook|spreadsheet)s?\b"
    r"|\b(?:convert|export|write|save)(?:s|ed|ing)?\b.{1,55}\b(?:to|as)\s+(?:(?:an?|the)\s+)?[.]?(?:xlsx|excel workbook)"
    r"|\b(?:wb|workbook)\.save\([^\n)]{0,70}\.xlsx|\b(?:deliverable|output) (?:is|must be) (?:an? )?spreadsheet file\b"
    r"|(?:生成|创建|输出|保存|导出)(?:为)?(?:本地|一个|新的)?\s*(?:Excel|XLSX|[.]xlsx|工作簿)",
)
SHEET = requirement("spreadsheet", "处理电子表格", "spreadsheet workbook",
                    r"\b(?:xlsx|spreadsheet|openpyxl|workbook|excel)\b|电子表格|工作簿")
FORMULAS = requirement(
    "formulas", "处理 Excel 公式", "xlsx formulas",
    r"\b(?:formulas?|spreadsheet computation|recalculat\w+)\b|公式",
)
PPT = requirement(
    "pptx", "制作演示文稿", "pptx slides",
    r"\b(?:create|build|generate|produce|edit|design)\b.{0,85}\b(?:presentations?|powerpoint|pptx|slides?|slide deck)\b"
    r"|\b(?:pptxgenjs|python-pptx)\b|(?:生成|创建|制作|编辑).{0,12}(?:演示文稿|幻灯片|PPTX)",
)
WORD = requirement(
    "docx", "编辑 Word 文档", "docx edit word",
    r"\b(?:create|edit|update|rewrite|modify|format|manipulate)\b.{0,85}\b(?:docx|word documents?)\b"
    r"|\bpython-docx\b.{0,60}\b(?:edit|update|write|save|create)\b|(?:创建|编辑|更新|排版).{0,12}(?:DOCX|Word\s*文档)",
)
WEB_TEST = requirement(
    "browser_test", "测试浏览器应用", "playwright browser testing",
    r"\b(?:end-to-end tests?|e2e tests?|browser test(?:s|ing)?|webapp testing)\b"
    r"|\b(?:test|verify|inspect|interact with)\b.{0,60}\b(?:web apps?|web pages?|local (?:sites?|apps?)|forms?|browser)\b"
    r"|\bplaywright(?:-cli)?\b.{0,60}\b(?:test\w*|browser automation|local)\b|浏览器测试",
)
FRONTEND = requirement(
    "frontend", "实现前端界面", "frontend design",
    r"\b(?:build|create|implement|design)\b.{0,35}\b(?:frontend|front-end|interface|landing page|web UI)\b|(?:实现|构建|制作|生成).{0,12}前端界面",
)
FRONTEND_REVIEW = requirement(
    "frontend_review", "审查前端界面", "ui accessibility review",
    r"\b(?:review|audit|check)\b.{0,35}\b(?:ui|frontend|front-end|accessibility|UX|user interface)\b|(?:检查|审查).{0,12}(?:前端|可访问性)",
)
MERMAID = requirement("mermaid", "生成 Mermaid 图", "mermaid diagrams", r"\bmermaid\b")
CUDF = requirement("cudf", "使用 cuDF 处理表格数据", "cudf pandas", r"\b(?:cudf|rapids)\b")
CUDF_GROUP = requirement("cudf_groupby", "用 cuDF 做分组聚合", "cudf groupby", r"\b(?:groupby|group-by|group by|grouped aggregation)\b|分组聚合")
MARKDOWN = requirement("markdown", "处理 Markdown 文档", "markdown document", r"\bmarkdown\b")


@dataclass(frozen=True)
class Task:
    text: str
    requirements: tuple[Requirement, ...]
    queries: tuple[str, ...]
    ranking_text: str


def plan(task: str) -> Task:
    normalized = " ".join(task.split())
    normalized = re.sub(
        r"(?<=[\u3400-\u9fff])(?=[a-zA-Z0-9])|(?<=[a-zA-Z0-9])(?=[\u3400-\u9fff])", " ", normalized,
    )
    found = []
    pdf = re.search(r"\bpdfs?\b|扫描件|扫描版", normalized, re.IGNORECASE)
    if pdf:
        if re.search(r"表格|表\s*\d|\b(?:tabular|tables?)\b", normalized, re.IGNORECASE):
            found.append(PDF_TABLE)
        scan = re.search(r"扫描|图像版|无文字层|\b(?:ocr|scanned|image-only)\b", normalized, re.IGNORECASE)
        if scan:
            found.append(PDF_OCR)
        if not scan and re.search(r"文字|文本|\btext\b", normalized, re.IGNORECASE) and not found:
            found.append(PDF_TEXT)
        if not found:
            found.append(PDF)
    if re.search(r"\b(?:xlsx|excel|spreadsheet|workbook)s?\b|电子表格|工作簿", normalized, re.IGNORECASE):
        fmt = SHEET
        if re.search(r"\b(?:xlsx|excel)\b", normalized, re.IGNORECASE):
            wants_file = re.search(r"生成|创建|保存|导出|整理成|写入|\b(?:write|create|export|save|convert)\b", normalized, re.IGNORECASE)
            fmt = EXCEL_OUTPUT if wants_file else EXCEL
        found.append(fmt)
        if re.search(r"公式|\bformulas?\b", normalized, re.IGNORECASE):
            found.append(FORMULAS)
    for cue, item in (
        (r"\b(?:pptx|powerpoint|slide deck|presentations?)\b|演示文稿|幻灯片|制作\s*ppt", PPT),
        (r"\b(?:docx|word document)\b|Word\s*文档", WORD),
        (r"\b(?:playwright|browser tests?|webapp testing|end-to-end tests?)\b|浏览器测试|测试.{0,12}(?:网页|前端|网站)", WEB_TEST),
        (r"\bmermaid\b", MERMAID),
        (r"\bcudf\b", CUDF),
        (r"\bmarkdown\b", MARKDOWN),
    ):
        if re.search(cue, normalized, re.IGNORECASE):
            found.append(item)
    if CUDF in found and re.search(r"分组|聚合|\bgroup ?by\b", normalized, re.IGNORECASE):
        found.append(CUDF_GROUP)
    if re.search(r"\b(?:frontend|front-end|landing page|web UI|accessibility)\b|前端界面|落地页|可访问性", normalized, re.IGNORECASE):
        wants_review = re.search(r"\b(?:audit|review|check)\b|审查|检查|评估", normalized, re.IGNORECASE)
        found.append(FRONTEND_REVIEW if wants_review else FRONTEND)
    queries = list(dict.fromkeys(item.query for item in found))
    if not queries:
        words = re.findall(r"[A-Za-z][A-Za-z0-9.+-]{1,23}", normalized)
        keywords = list(dict.fromkeys(ALIAS.get(x.casefold(), x.casefold()) for x in words if x.casefold() not in STOP))
        queries.extend(keywords[:4])
        if not queries:
            keywords = re.findall(r"[\u3400-\u9fff]{2,24}", normalized)
            queries.extend(keywords[:2])
    ranking_text = " ".join([normalized, *[item.query for item in found]])
    return Task(normalized, tuple(found), tuple(dict.fromkeys(queries))[:3], ranking_text)
