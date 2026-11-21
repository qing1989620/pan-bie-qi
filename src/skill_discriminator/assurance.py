"""Declared capabilities, conservative static leads, and observed policy drift."""

from __future__ import annotations

import ast

from .inspection import citation, frontmatter


def declarations(files: dict[str, str]) -> dict:
    metadata, issue = frontmatter(files["SKILL.md"])
    caps = metadata.get("capabilities")
    if not isinstance(caps, dict):
        return {"state": "not_declared", "values": {}, "scope": "No manifest capability declarations; absence is not permission or prohibition"}
    supported = {key: value for key, value in caps.items() if key in {"filesystem_write", "network"} and type(value) is bool}
    return {"state": "declared" if supported and not issue else "incomplete", "values": supported,
            "provenance": "author_self_declaration_not_independently_proven", "unsupported_fields": sorted(set(caps) - set(supported)),
            "scope": "Two broad capability categories; not complete static capability containment"}


def static_capabilities(files: dict[str, str]) -> dict:
    declared = declarations(files)
    leads = []
    for path, text in files.items():
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            continue
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    aliases[alias.asname or alias.name.split(".")[0]] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    aliases[alias.asname or alias.name] = node.module + "." + alias.name
        def resolve(node, bindings=aliases):
            if isinstance(node, ast.Name):
                return bindings.get(node.id, node.id)
            if isinstance(node, ast.Attribute):
                return resolve(node.value) + "." + node.attr
            if isinstance(node, ast.Call):
                return resolve(node.func)
            return "?"
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = resolve(node.func)
            cap = None
            if name in {"open", "io.open", "pathlib.Path.open"} or name.endswith(".open"):
                mode = next((k.value for k in node.keywords if k.arg == "mode"), node.args[1] if len(node.args) > 1 else None)
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and any(x in mode.value for x in "wax+"):
                    cap = "filesystem_write"
            if name.endswith((".write_text", ".write_bytes", ".unlink", ".rmdir", ".mkdir", ".rename", ".replace")) or name in {"os.remove", "os.unlink", "os.mkdir", "os.rmdir", "os.rename", "shutil.rmtree", "shutil.move", "shutil.copyfile"}:
                cap = "filesystem_write"
            if name.startswith(("requests.", "httpx.", "urllib.request.", "socket.")) and not name.endswith((".Client", ".Session")):
                cap = "network"
            if cap:
                leads.append({"capability": cap, "call": name, "declared": declared["values"].get(cap), "evidence": citation(files, path, node.lineno),
                              "state": "possible_call_only", "explanation": "静态调用线索；分支可能不执行，别名和动态调用也可能漏检，不作为已发生违规"})
    return {"declaration": declared, "leads": leads, "containment_proved": False, "absence_of_findings_proves_safety": False}


def runtime_policy(files: dict[str, str], records: list[dict]) -> dict:
    declared = declarations(files)
    audits = [r["runtime"].get("host_audit") for r in records]
    complete = bool(audits) and all(a and a["origin"] == "trusted_host_wasm_proxy" for a in audits)
    attempted, succeeded = {}, {}
    for audit in audits:
        if not audit:
            continue
        for item in audit["counts"]:
            cap = item["capability"]
            attempted[cap] = attempted.get(cap, 0) + item["count"]
            if item["outcome"] == "succeeded":
                succeeded[cap] = succeeded.get(cap, 0) + item["count"]
    violations = [{"capability": cap, "attempted_calls": n, "successful_calls": succeeded.get(cap, 0), "declared": False,
                   "meaning": "执行中观察到与明确声明不符的能力调用；失败/被阻止不等于副作用已成功"}
                  for cap, n in attempted.items() if n and declared["values"].get(cap) is False]
    return {"state": "observed_declaration_violation" if violations else "not_measured" if not complete else "declaration_unknown" if not declared["values"] else "no_observed_violation_in_monitored_scope",
            "violations": violations, "attempted_calls": attempted, "successful_calls": succeeded, "host_trace_complete": complete,
            "declarations": declared, "hard_block": bool(violations), "scope": "WASIp1 calls in these executions; no all-environment safety certificate; network imports may be unavailable before host boundary"}
