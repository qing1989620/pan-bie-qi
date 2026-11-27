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
    context: re.Pattern | None = None
    output: re.Pattern | None = None


def requirement(key: str, label: str, query: str, pattern: str, exclusion: str = "", *,
                context: str = "", output: str = "") -> Requirement:
    return Requirement(key, label, query, re.compile(pattern, re.IGNORECASE),
                       re.compile(exclusion, re.IGNORECASE) if exclusion else None,
                       re.compile(context, re.IGNORECASE) if context else None,
                       re.compile(output, re.IGNORECASE) if output else None)


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
    r"|(?:生成|创建|制作|编辑).{0,12}(?:演示文稿|幻灯片|PPTX)",
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
PLAYWRIGHT_TEST = Requirement(
    WEB_TEST.id, "用 Playwright 测试浏览器应用", WEB_TEST.query, WEB_TEST.evidence,
    re.compile(r"\b(?:no|without)\b.{0,24}\bplaywright\b|\bplaywright\b.{0,18}\bnot supported\b|不支持\s*playwright",
               re.IGNORECASE),
    re.compile(r"\bplaywright\b", re.IGNORECASE),
)
FRONTEND = requirement(
    "frontend", "实现前端界面", "frontend design",
    r"\b(?:build|create|implement|design)\b.{0,35}\b(?:frontend|front-end|interface|landing page|web UI)\b|(?:实现|构建|制作|生成).{0,12}前端界面",
)
FRONTEND_REVIEW = requirement(
    "frontend_review", "审查前端界面", "ui accessibility review",
    r"\b(?:review|audit|check)\b.{0,35}\b(?:ui|frontend|front-end|accessibility|UX|user interface)\b|(?:检查|审查).{0,12}(?:前端|可访问性)",
)
DIAGRAM_ACTION = (
    r"\b(?:creat\w*|generat\w*|draw(?:s|ing|n)?(?![.]io\b)|writ(?:e[sn]?|ten|ing)"
    r"|produc\w*|author\w*|build\w*|design(?:s|ed|ing)?)\b"
)
FLOW = r"\b(?:flow[- ]?charts?|process flows?|decision flows?)\b|流程图"
CUDF = requirement("cudf", "使用 cuDF 处理表格数据", "cudf pandas", r"\b(?:cudf|rapids)\b")
CUDF_GROUP = requirement("cudf_groupby", "用 cuDF 做分组聚合", "cudf groupby", r"\b(?:groupby|group-by|group by|grouped aggregation)\b|分组聚合")
MARKDOWN = requirement("markdown", "处理 Markdown 文档", "markdown document", r"\bmarkdown\b")
GIT_CONFLICTS = requirement(
    "git_conflict", "解决 Git 合并冲突", "git merge conflicts",
    r"\b(?:resolv\w+|fix\w*|handl\w+)\b.{0,48}\b(?:merge )?conflicts?\b|处理.{0,12}合并冲突|解决.{0,12}冲突",
    context=r"\bgit\b|\bmerge conflicts?\b",
)
POSTGRES_TUNING = requirement(
    "postgres_tuning", "检查 PostgreSQL 查询性能", "postgresql query optimization",
    r"\b(?:explain(?: analyze)?|index(?:es|ing)?|query (?:performance|optimization|tuning))\b|查询性能|索引",
    context=r"\bpostgres(?:ql)?\b",
)
CI_DIAGNOSIS = requirement(
    "ci_diagnosis", "诊断 CI 工作流失败", "diagnose ci failures",
    r"\b(?:diagnos\w+|debug\w*|triag\w*|investigat\w+|fix\w*)\b.{0,55}\b(?:fail\w*|errors?|ci)\b|排查.{0,16}(?:CI|故障|失败)",
    context=r"\b(?:github actions|continuous integration|ci (?:jobs?|checks?|workflows?|builds?|pipelines?|failures?))\b"
            r"|\b(?:jobs?|checks?|workflows?|builds?) in ci\b|持续集成",
)
GITHUB_ACTIONS_DIAGNOSIS = Requirement(
    CI_DIAGNOSIS.id, "诊断 GitHub Actions 工作流失败", CI_DIAGNOSIS.query, CI_DIAGNOSIS.evidence, None,
    re.compile(r"\b(?:github actions|github cli|gh (?:run|pr checks))\b|[.]github/workflows/", re.IGNORECASE),
)
POSTGRES_COMPOSE = requirement(
    "postgres_compose", "用 Docker Compose 配置 PostgreSQL 服务", "docker compose postgres",
    r"\b(?:docker compose|docker-compose|compose[.]ya?ml)\b",
    context=r"\b(?:docker compose|docker-compose|compose[.]ya?ml)\b",
    output=r"\b(?:postgresql|postgres)\b.{0,30}\b(?:image|service|services|container)\b"
           r"|\b(?:image|service|services|container)\b.{0,25}\b(?:postgresql|postgres)\b"
           r"|(?:配置|启动).{0,15}\bpostgres(?:ql)?\b",
)
JSON_SCHEMA = requirement(
    "json_schema", "用 JSON Schema 校验数据", "json schema validation",
    r"\b(?:validat|check)\w*\b.{0,65}\b(?:json|schema|records?|payloads?|documents?|data)\b"
    r"|\b(?:json schema validation|schema validators?)\b|校验.{0,15}(?:JSON|记录|数据)",
    context=r"\b(?:json schema|jsonschema|ajv)\b",
)
REACT_FRONTEND = Requirement(
    FRONTEND.id, "用 React 实现前端界面", FRONTEND.query, FRONTEND.evidence, None,
    re.compile(r"\breact\b|reactjs", re.IGNORECASE),
)
KEYBOARD_UI = r"\bkeyboard(?:[- ]accessible| support| navigation)\b|\b(?:visible|keyboard) focus\b|键盘(?:操作|访问|导航)|可见焦点"
PLOT_TYPES = (
    (r"\bscatter(?:plots?)?\b|散点图", "scatter plot", "散点图"),
    (r"\bheat[- ]?maps?\b|热力图", "heatmap", "热力图"),
    (r"\bbar (?:charts?|plots?)\b|柱状图", "bar chart", "柱状图"),
    (r"\bline (?:charts?|plots?)\b|折线图", "line chart", "折线图"),
)
PLOT_TOOLS = ("Matplotlib", "Plotly", "Seaborn", "Altair", "Vega-Lite")
PLOT_FORMAT = re.compile(
    r"\b(?:export|save|output|deliver|write)\b.{0,35}\b(?:as|to)\s+[.]?(svg|png|html|jpe?g)\b"
    r"|(?:导出|保存|输出)(?:为|成)?[\s.]*?(svg|png|html|jpe?g)\b", re.IGNORECASE,
)
EXPLICIT_TOOL = re.compile(
    r"(?:\b(?:must use|using|using only|use)\s+|(?:必须使用|必须用|使用|用)\s*)"
    r"`([A-Za-z][A-Za-z0-9_.+-]{1,35})`", re.IGNORECASE,
)


def _typed_requirements(text: str) -> tuple[list[Requirement], list[str]]:
    """Keep named tools and the requested operation together in one capability."""
    found, broader = [], []
    for language in ("Mermaid", "PlantUML"):
        anchor = rf"\b{language}\b"
        if not re.search(anchor, text, re.IGNORECASE):
            continue
        # Match authoring with the named language. A converter that only reads that
        # language should not satisfy a contract to generate it.
        input_phrase = rf"\b(?:from|import\w*|read\w*)\s+(?:(?:an?|the|existing|source)\s+){{0,3}}{anchor}"
        output = (rf"{DIAGRAM_ACTION}(?!.{{0,85}}{input_phrase}).{{0,85}}{anchor}"
                  rf"|{anchor}.{{0,30}}\b(?:source|code) (?:generation|output)\b"
                  rf"|(?:生成|绘制|编写|构建|输出).{{0,20}}{anchor}")
        flowchart = re.search(FLOW, text, re.IGNORECASE) is not None
        kind, label = ("flowchart", "流程图") if flowchart else ("diagrams", "图")
        found.append(requirement(language.casefold(), f"生成 {language} {label}", f"{language.casefold()} {kind}",
                                 FLOW if flowchart else output, context=anchor, output=output))
        broader.append(f"{language.casefold()} diagrams")
    for tool in PLOT_TOOLS:
        anchor = rf"\b{re.escape(tool)}\b"
        if not re.search(anchor, text, re.IGNORECASE):
            continue
        fmt = PLOT_FORMAT.search(text)
        artifact = next((item.casefold() for item in fmt.groups() if item), "") if fmt else ""
        export = (rf"\b(?:export\w*|sav\w*|output\w*|writ\w*|render\w*)\b.{{0,60}}\b{artifact}\b"
                  rf"|\b(?:output|export|supported) formats?\b.{{0,64}}\b{artifact}\b"
                  rf"|\bsavefig\s*\(.{{0,64}}[.]{artifact}\b|(?:导出|保存|输出).{{0,16}}\b{artifact}\b") if artifact else ""
        suffix = f"并导出 {artifact.upper()}" if artifact else ""
        for pattern, query, label in PLOT_TYPES:
            if re.search(pattern, text, re.IGNORECASE):
                found.append(requirement(f"{tool.casefold()}_{query.split()[0]}", f"用 {tool} 生成{label}{suffix}",
                                         f"{tool.casefold()} {query}", pattern, context=anchor, output=export))
                broader.append(f"{tool.casefold()} plotting")
    normalized = text.casefold()
    if re.search(r"\bgit\b", normalized) and re.search(r"conflicts?|冲突", normalized):
        found.append(GIT_CONFLICTS)
        broader.append("resolve merge conflicts")
    if re.search(r"\bpostgres(?:ql)?\b", normalized) and re.search(r"slow|optimiz|explain|index|性能|索引", normalized):
        found.append(POSTGRES_TUNING)
        broader.append("postgresql")
    if re.search(r"\b(?:github actions|ci)\b|持续集成", normalized) and re.search(r"fail|diagnos|故障|失败|排查", normalized):
        found.append(GITHUB_ACTIONS_DIAGNOSIS if "github actions" in normalized else CI_DIAGNOSIS)
        broader.append("diagnose")
    if re.search(r"\bjson schema\b", normalized) and re.search(r"validat|check|校验|验证", normalized):
        found.append(JSON_SCHEMA)
        broader.append("jsonschema")
    if re.search(r"\bdocker[ -]compose\b", normalized) and re.search(r"\bpostgres(?:ql)?\b", normalized):
        found.append(POSTGRES_COMPOSE)
        broader.append("docker compose")
    if not found:
        for name in dict.fromkeys(EXPLICIT_TOOL.findall(text)):
            exact = rf"(?<![\w.-]){re.escape(name)}(?![\w.-])"
            found.append(requirement(f"tool_{name.casefold()}", f"使用 {name}", name.casefold(), exact, context=exact))
    return found, broader


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
        (r"\b(?:playwright|browser tests?|webapp testing|end-to-end tests?)\b|浏览器测试|测试.{0,12}(?:网页|前端|网站)",
         PLAYWRIGHT_TEST if re.search(r"\bplaywright\b", normalized, re.IGNORECASE) else WEB_TEST),
        (r"\bcudf\b", CUDF),
        (r"\bmarkdown\b", MARKDOWN),
    ):
        if re.search(cue, normalized, re.IGNORECASE):
            found.append(item)
    if CUDF in found and re.search(r"分组|聚合|\bgroup ?by\b", normalized, re.IGNORECASE):
        found.append(CUDF_GROUP)
    if re.search(r"\b(?:frontend|front-end|landing page|web UI|accessibility)\b|前端界面|落地页|可访问性", normalized, re.IGNORECASE):
        wants_review = re.search(r"\b(?:audit|review|check)\b|审查|检查|评估", normalized, re.IGNORECASE)
        if wants_review:
            found.append(FRONTEND_REVIEW)
        else:
            implementation = REACT_FRONTEND if re.search(r"\breact\b", normalized, re.IGNORECASE) else FRONTEND
            if re.search(r"\b(?:accessib\w*|keyboard)\b|可访问|键盘", normalized, re.IGNORECASE):
                implementation = Requirement(implementation.id, implementation.label + "及键盘可访问性",
                                             implementation.query, implementation.evidence, None,
                                             implementation.context, re.compile(KEYBOARD_UI, re.IGNORECASE))
            found.append(implementation)
    typed, wider_queries = _typed_requirements(normalized)
    found.extend(typed)
    queries = list(dict.fromkeys([*(item.query for item in found), *wider_queries]))
    if not queries:
        words = re.findall(r"[A-Za-z][A-Za-z0-9.+-]{1,23}", normalized)
        keywords = list(dict.fromkeys(ALIAS.get(x.casefold(), x.casefold()) for x in words if x.casefold() not in STOP))
        queries.extend(keywords[:4])
        if not queries:
            keywords = re.findall(r"[\u3400-\u9fff]{2,24}", normalized)
            queries.extend(keywords[:2])
    ranking_text = " ".join([normalized, *[item.query for item in found]])
    return Task(normalized, tuple(found), tuple(dict.fromkeys(queries))[:3], ranking_text)
