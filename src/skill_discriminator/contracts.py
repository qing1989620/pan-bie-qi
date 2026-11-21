"""Narrow task-to-implementation contract checks with inspectable witnesses.

These are explicit counterexamples or review leads, never a generic correctness
proof. Domain formulas and arbitrary code control flow remain outside this gate.
"""

from __future__ import annotations

import ast
import re


def code_blocks(document: str) -> list[dict]:
    blocks = []
    for match in re.finditer(r"^```(?:python|py)\s*\n(.*?)^```", document, re.MULTILINE | re.DOTALL | re.IGNORECASE):
        try:
            tree = ast.parse(match[1])
        except (SyntaxError, ValueError, RecursionError):
            continue
        blocks.append({"tree": tree, "text": match[1], "start_line": document.count("\n", 0, match.start(1)) + 1})
    return blocks


def check(query: str, document: str, files: dict[str, str] | None = None) -> list[dict]:
    findings = []
    document_lines = document.splitlines()
    expected_paths = []
    path_re = r"(?:/[\w.\-]+){2,}\.[A-Za-z0-9]{1,8}"
    for line in query.splitlines():
        if re.search(r"\b(?:write|save|output)\b|写入|保存|输出", line, re.IGNORECASE):
            expected_paths.extend(re.findall(path_re, line))
    expected_paths = list(dict.fromkeys(expected_paths))
    # Only a direct final operating instruction is compared; filenames inside
    # earlier examples or alternative modes cannot establish a contradiction.
    for i, line in enumerate(document_lines, 1):
        if i < len(document_lines) - 12:
            continue
        if re.match(r"^\s*(?:Write|Save|Output|将.{0,30}(?:写入|保存)|输出)", line, re.IGNORECASE) and not re.search(r"example|e\.g\.|示例|例如", line, re.IGNORECASE):
            actual = re.findall(path_re, line)
            if len(expected_paths) == 1 and len(actual) == 1 and expected_paths[0] != actual[0]:
                findings.append({"kind": "explicit_output_path_conflict", "explanation": "任务要求的输出路径与技能最后的直接操作指令不同",
                                 "required": expected_paths[0], "declared": actual[0], "path": "SKILL.md", "line": i, "quote": line,
                                 "scope": "直接声明冲突；其他可配置执行模式未验证"})
    requires_absolute = bool(re.search(r"absolute (?:file )?path|绝对路径", query, re.IGNORECASE))
    requires_full_rows = bool(re.search(r"完整行.*去重|按整行.*去重|去重.*完整行|duplicate rows.*all columns|all columns.*duplicate rows", query, re.IGNORECASE))
    requires_none = bool(re.search(r"(?:should (?:output|return)|returns?|返回)\s*(?:with:)?\s*\n?\s*None\b", query, re.IGNORECASE))
    expected_functions = set(re.findall(r"\bdef\s+([A-Za-z_]\w*)\s*\(", query))
    blocks = [("SKILL.md", row) for row in code_blocks(document)]
    for path, text in (files or {}).items():
        if path.endswith(".py"):
            try:
                blocks.append((path, {"tree": ast.parse(text), "text": text, "start_line": 1}))
            except (SyntaxError, ValueError, RecursionError):
                pass
    for path, block in blocks:
        for function in ast.walk(block["tree"]):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not expected_functions or function.name not in expected_functions:
                continue
            arguments = {a.arg for a in function.args.args}
            assigned = {n.id for n in ast.walk(function) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            for statement in function.body:
                if requires_absolute and isinstance(statement, ast.Return) and isinstance(statement.value, ast.Name) and statement.value.id in arguments - assigned:
                    variable = statement.value.id
                    if re.search(r"file|path|name", variable, re.IGNORECASE):
                        line = block["start_line"] + statement.lineno - 1
                        findings.append({"kind": "absolute_path_not_constructed", "explanation": "任务要求返回绝对路径，但这个函数直接返回未改变的路径参数；相对路径输入会违约",
                                         "required": "absolute_path", "declared": variable, "path": path, "line": line,
                                         "quote": block["text"].splitlines()[statement.lineno - 1], "scope": "这个函数的直接返回路径；未验证调用方只传绝对路径的特殊约束"})
                if requires_none and isinstance(statement, ast.Return) and isinstance(statement.value, (ast.List, ast.Dict, ast.Tuple, ast.Set)):
                    findings.append({"kind": "explicit_return_type_conflict", "explanation": "任务要求返回None，但函数直接返回一个容器值",
                                     "path": path, "line": block["start_line"] + statement.lineno - 1,
                                     "quote": block["text"].splitlines()[statement.lineno - 1], "scope": "这个明确的返回路径"})
            if requires_full_rows:
                for call in ast.walk(function):
                    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or call.func.attr != "drop_duplicates":
                        continue
                    for keyword in call.keywords:
                        if keyword.arg == "subset" and isinstance(keyword.value, (ast.List, ast.Tuple)) and keyword.value.elts:
                            findings.append({"kind": "partial_columns_for_full_row_dedup", "explanation": "任务要求完整行去重，但这个调用明确只按部分列去重",
                                             "path": path, "line": block["start_line"] + call.lineno - 1,
                                             "quote": block["text"].splitlines()[call.lineno - 1], "scope": "这个显式subset调用；动态分支与其他模式未验证"})
    unique = {}
    for row in findings:
        unique[(row["kind"], row["path"], row["line"])] = row
    return list(unique.values())
