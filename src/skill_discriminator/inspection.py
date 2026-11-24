"""Inspect user-provided package text; never import or execute package code."""

from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import PurePosixPath

import yaml

from .inputs import commands, digest
from .resource_links import bundled_links

MAX_FILES = 128
MAX_BYTES = 4_000_000
MAX_FILE_BYTES = 600_000
TEXT_SUFFIXES = {".md", ".py", ".json", ".txt", ".yaml", ".yml", ".toml", ".sh", ".ps1"}
DECLARED_EXCLUSION = r"\b(?:not for|do not use (?:for|when)|not applicable (?:to|when))\b|不触发(?:于|在)|不适用|不支持"
SECRET = re.compile(r"sk-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{30,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{16,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.DOTALL | re.IGNORECASE)


def redact(text: str) -> str:
    return SECRET.sub("[凭据已隐藏]", text)


def clean(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [clean(x) for x in value]
    if isinstance(value, tuple):
        return [clean(x) for x in value]
    if isinstance(value, dict):
        return {redact(str(k)): clean(v) for k, v in value.items()}
    return value


def validate_path(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 240 or "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("文件名必须是技能包内的相对路径")
    path = PurePosixPath(name)
    if path.is_absolute() or any(x in {"..", ".", ""} for x in name.split("/")):
        raise ValueError("文件路径不能越过技能包目录")
    if any(ord(x) < 32 or x in '<>"|?*' for x in name) or any(
        part.endswith((".", " ")) or re.match(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.IGNORECASE)
        for part in name.split("/")
    ):
        raise ValueError("文件名与本机Windows文件规则不兼容，请修改后重新导入")
    return path.as_posix()


def validate_files(files: dict[str, str], inventory: list[str]) -> None:
    if not files or len(files) > MAX_FILES or len(inventory) > 2000:
        raise ValueError("请提供1至128个文本文件；完整清单最多2000项")
    size = 0
    for name, text in files.items():
        validate_path(name)
        if not isinstance(text, str) or "\x00" in text:
            raise ValueError("只接受UTF-8文本文件")
        length = len(text.encode("utf-8"))
        if length > MAX_FILE_BYTES:
            raise ValueError("单个文本文件不能超过600KB")
        size += length
    if size > MAX_BYTES:
        raise ValueError("本次文本材料总量不能超过4MB")
    if "SKILL.md" not in files or len(files["SKILL.md"].strip()) < 10:
        raise ValueError("请在技能包根目录提供至少10个字符的SKILL.md")
    for name in inventory:
        validate_path(name)
    if inventory and not set(files).issubset(set(inventory)):
        raise ValueError("完整清单必须包含所有上传文件")
    all_paths = set(files) | set(inventory)
    if len({name.casefold() for name in all_paths}) != len(all_paths):
        raise ValueError("存在仅大小写不同的路径，本机无法保证两份独立源码版本")


def citation(files: dict[str, str], path: str, line: int, text: str | None = None) -> dict:
    source = files[path]
    lines = source.splitlines()
    return {"path": path, "start_line": line, "sha256": hashlib.sha256(source.encode()).hexdigest(),
            "quote": redact(text if text is not None else lines[line - 1] if 0 < line <= len(lines) else "")[:500]}


def frontmatter(document: str) -> tuple[dict, str | None]:
    if not document.startswith("---"):
        return {}, None
    parts = document.split("---", 2)
    if len(parts) < 3 or len(parts[1]) > 12_000:
        return {}, "文档头部元数据不完整或超出解析范围"
    try:
        parsed = yaml.safe_load(parts[1])
        return (parsed if isinstance(parsed, dict) else {}), None
    except (yaml.YAMLError, RecursionError, ValueError):
        return {}, "文档头部元数据无法解析"


def description_lines(document: str) -> list[tuple[int, str]]:
    lines = document.removeprefix("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    observed = []
    for number, line in enumerate(lines[1:], 2):
        if line.strip() == "---":
            break
        if observed:
            if line and not line[0].isspace():
                break
            if line.strip():
                observed.append((number, line.strip()))
        elif re.match(r"^description[ \t]*:", line):
            observed.append((number, line))
    return observed


def section_lines(files: dict[str, str], pattern: str, exclude: str = "") -> list[dict]:
    rows, active, level = [], False, 0
    for i, line in enumerate(files["SKILL.md"].splitlines(), 1):
        heading = re.match(r"^(#{1,6})\s+(.+)", line)
        if heading:
            if active and len(heading[1]) <= level:
                active = False
            if re.search(pattern, heading[2], re.IGNORECASE) and not (exclude and re.search(exclude, heading[2], re.IGNORECASE)):
                active, level = True, len(heading[1])
            continue
        if active and line.strip() and not line.startswith("```"):
            rows.append(citation(files, "SKILL.md", i))
        if len(rows) >= 8:
            break
    return rows


def inspect(identity: str, files: dict[str, str], inventory: list[str], complete: bool, environment: dict) -> dict:
    validate_files(files, inventory)
    document = files["SKILL.md"]
    examples = [{**item, "document": "SKILL.md"} for item in commands(document)]
    hashes = {path: hashlib.sha256(text.encode("utf-8")).hexdigest() for path, text in files.items()}
    fingerprint = digest({"identity": identity, "files": files, "expected_hashes": hashes, "examples": examples})
    findings, review = [], []
    metadata, metadata_issue = frontmatter(document)
    if metadata_issue:
        review.append({"kind": "metadata_unparsed", "explanation": metadata_issue, "evidence": citation(files, "SKILL.md", 1)})
    fallback_title = next((re.sub(r"^#+\s*", "", line).strip() for line in document.splitlines() if line.startswith("#")), identity)
    title = metadata.get("name") if isinstance(metadata.get("name"), str) else fallback_title
    description = metadata.get("description") if isinstance(metadata.get("description"), str) else ""
    if not description:
        body = document.split("---", 2)[-1] if document.startswith("---") and len(document.split("---", 2)) == 3 else document
        description = next((line.strip() for line in body.splitlines() if line.strip() and not line.startswith(("#", "---", "```"))), "未提供单独说明")[:400]
    required_keys, dependency_imports = [], set()
    declared = metadata.get("requires", {})
    if not isinstance(declared, dict):
        declared = {}
    explicit_env = declared.get("env", [])
    if not isinstance(explicit_env, list):
        explicit_env = []
    explicit_env = {x for x in explicit_env if isinstance(x, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", x)}
    conditions = []
    for key in sorted(explicit_env):
        state = environment.get("available_keys", {}).get(key)
        line = next((i for i, text in enumerate(document.splitlines(), 1) if re.search(r"\b" + re.escape(key) + r"\b", text)), 1)
        conditions.append({"kind": "api_configuration", "requirement": key,
                           "state": "met" if state is True else "unmet" if state is False else "unknown",
                           "explanation": "技能元数据明确要求此环境变量；只核对是否具备配置，不接收密钥值",
                           "evidence": citation(files, "SKILL.md", line)})
    if declared.get("network") is True:
        state = environment.get("network_allowed")
        line = next((i for i, text in enumerate(document.splitlines(), 1) if re.match(r"\s*network\s*:", text)), 1)
        conditions.append({"kind": "network", "requirement": "允许访问网络", "state": "met" if state is True else "unmet" if state is False else "unknown",
                           "explanation": "元数据明确要求网络访问", "evidence": citation(files, "SKILL.md", line)})
    present = set(inventory or files)
    resource_links = []
    for line, path in bundled_links(document):
        resolved = path in present or any(entry.startswith(path + "/") for entry in present)
        state = "present" if resolved else "missing" if complete else "unknown"
        reference = {"resource": path, "state": state, "evidence": citation(files, "SKILL.md", line)}
        resource_links.append(reference)
        if state == "missing":
            findings.append({**reference, "kind": "bundled_link_missing", "layer": "resource", "severity": "confirmed",
                             "explanation": f"说明链接指向包内资源 {path}，完整清单中未找到该文件或目录。"})
    for path, text in files.items():
        for i, line in enumerate(text.splitlines(), 1):
            if SECRET.search(line):
                review.append({"kind": "embedded_credential", "severity": "review", "layer": "configuration",
                               "explanation": "发现形似凭据的字面值，已隐藏；可能是示例或真实配置，未鉴定有效性，请确认后从分发包移除",
                               "evidence": citation(files, path, i), "credential_validity": "not_verified"})
            for key in set(re.findall(r"\b[A-Z][A-Z0-9_]{2,70}(?:API_KEY|ACCESS_TOKEN|SECRET_KEY|AUTH_TOKEN)\b|\bAPI_KEY\b", line)):
                if key not in explicit_env and not any(x["name"] == key for x in required_keys):
                    required_keys.append({"name": key, "status": "mentioned_not_proven_required", "evidence": citation(files, path, i)})
        if path.endswith(".py"):
            try:
                tree = ast.parse(text, filename=path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        dependency_imports.update(x.name.split(".")[0] for x in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                        dependency_imports.add(node.module.split(".")[0])
            except SyntaxError as exc:
                if not any(x["kind"] == "entrypoint_syntax_error" and x["evidence"]["path"] == path for x in findings):
                    findings.append({"kind": "python_syntax_error", "severity": "confirmed", "layer": "syntax", "explanation": "这个Python文件存在语法错误",
                                     "evidence": citation(files, path, exc.lineno or 1)})
            except (ValueError, RecursionError):
                review.append({"kind": "python_parse_out_of_scope", "explanation": "Python解析未完成，保留待验证", "evidence": citation(files, path, 1)})
    from .cli_evidence import check as check_cli
    command_observations, unknown_checks = [], []
    for example in examples:
        script = example.get("script")
        normalized = script.removeprefix("./") if isinstance(script, str) else script
        context = "\n".join(document.splitlines()[max(0, example.get("start_line", 1)-5):example.get("start_line", 1)-1])
        uncertain_context = bool(re.search(r"do not run|don't run|invalid example|incorrect example|wrong example|不要运行|错误示例|反例|^\s*cd\s", context, re.IGNORECASE | re.MULTILINE))
        if uncertain_context:
            checked = {"status": "unknown", "reasons": ["negative_example_or_working_directory_context"], "findings": [], "runtime_verified": False}
        elif normalized in files:
            checked = check_cli(files[normalized], example["command"])
        else:
            checked = {"status": "unknown", "reasons": ["entrypoint_unread_or_unresolved"], "findings": [], "runtime_verified": False}
        observation = {**example, "script": normalized, "result": checked}
        command_observations.append(observation)
        if checked["status"] == "inconsistent_static":
            grammar = checked["grammar"]
            positions = grammar["declaration_lines"] or [grammar["parse_line"]]
            for item in checked["findings"]:
                findings.append({"kind": item["kind"], "severity": "confirmed", "layer": "cli",
                                 "explanation": "文档命令被源码字面参数声明对应的标准库解析器拒绝：" + item["parser_message"],
                                 "command": example["command"], "detail": item,
                                 "evidence": citation(files, "SKILL.md", example.get("start_line", 1)),
                                 "contract_evidence": citation(files, normalized, min(positions), "\n".join(files[normalized].splitlines()[min(positions)-1:max(positions)])),
                                 "grammar_sha256": grammar["sha256"], "scope": checked["scope"],
                                 "method": checked["method"]})
        elif checked["status"] != "consistent_static":
            unknown_checks.append({"layer": "cli", "reason": checked["status"], "details": checked.get("reasons", []), "command": example["command"]})
    if not command_observations:
        unknown_checks.append({"layer": "cli", "reason": "no_literal_python_task_example_not_a_defect"})
    for observation in command_observations:
        script = observation.get("script")
        if script and script not in present and "negative_example_or_working_directory_context" not in observation["result"].get("reasons", []):
            anchored = script.startswith("scripts/")
            row = {"kind": "documented_entry_missing", "layer": "resource", "explanation": "文档中的Python入口没有出现在完整包清单中" if complete else "文档提到的Python入口尚未上传，暂不能检查",
                   "evidence": citation(files, "SKILL.md", observation.get("start_line", 1)), "resource": script}
            if not anchored:
                row["explanation"] = "文档提及的相对入口未定位；可能由工作流生成或使用其他工作目录，需确认"
            (findings if complete and anchored else review).append({"severity": "confirmed" if complete and anchored else "review", **row})
    # A dependency import may be optional; it is a configuration lead, not a failure.
    local_modules = {PurePosixPath(p).stem for p in files if p.endswith(".py")}
    imports = sorted(dependency_imports - sys.stdlib_module_names - local_modules)
    supported = section_lines(files, r"when to use|use cases|适用|触发|能力|用途", r"not (?:to )?use|不适用|不支持|排除|限制")
    excluded = section_lines(files, r"limitations|not to use|not supported|不适用|限制|边界|排除")
    if isinstance(metadata.get("description"), str):
        excluded.extend(citation(files, "SKILL.md", line) for line, text in description_lines(document)
                        if re.search(DECLARED_EXCLUSION, text, re.IGNORECASE))
    if not excluded:
        review.append({"kind": "boundary_not_explicit", "explanation": "未发现明确的不适用范围；建议补充输入、输出和资源限制"})
    suggestions = []
    if any(x["layer"] == "cli" for x in findings if "layer" in x):
        suggestions.append("按照脚本实际参数声明修正文档命令，并验证这些命令的真实输出。")
    if any(x["kind"] == "documented_entry_missing" for x in findings):
        suggestions.append("补齐文档承诺的入口文件，或删除无法执行的示例。")
    if any(x["kind"] == "bundled_link_missing" for x in findings):
        suggestions.append("补齐说明链接指向的包内资源，或修正相对链接。")
    if any(x["kind"] in {"python_syntax_error", "entrypoint_syntax_error"} for x in findings):
        suggestions.append("先修复指出的语法位置，再运行对应入口的任务测试。")
    if any(x["kind"] == "embedded_credential" for x in review):
        suggestions.append("将凭据移到本地运行配置，并从可分发技能包中移除字面值。")
    if any(x["state"] != "met" for x in conditions):
        suggestions.append("确认列出的资源与配置条件；这里只填写是否具备配置，不填写密钥。")
    if not excluded:
        suggestions.append("补充不适用场景、输入格式、输出接口及失败时的行为。")
    suggestions.append("用真实输入和独立产物判据验证；文档或模型匹配分数不能代替执行结果。")
    return clean({
        "identity": identity, "bundle_fingerprint": fingerprint,
        "introduction": {"name": title[:200], "description": description[:600], "declared_supported": supported, "declared_exclusions": excluded},
        "package": {"status": "confirmed_issue" if findings else "no_confirmed_issue_in_checked_scope", "findings": findings,
                    "review_items": review, "command_observations": command_observations, "unknown_checks": unknown_checks,
                    "resource_links": resource_links,
                    "files_analyzed": len(files), "inventory_files": len(present), "inventory_complete": complete,
                    "file_sha256": hashes,
                    "origin": "当前上传文本快照，未独立认证上游来源"},
        "conditions": conditions, "configuration_mentions": required_keys,
        "third_party_imports": imports, "suggestions": suggestions,
        "execution_verified": False, "package_code_executed": False,
    })
