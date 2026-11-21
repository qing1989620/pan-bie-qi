"""Reconstruct a bounded argparse grammar from literals, without executing source.

The trusted standard library consumes argv. Unsupported source control flow,
custom converters, parser mutation and argument-file expansion cause abstention.
Acceptance proves only grammar compatibility under the stated assumptions.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
import shlex

METHOD = "literal-argparse-reconstruction-v2"
MAX_NODES = 12000
MAX_ARGUMENTS = 128


class Unsupported(ValueError):
    pass


class ParseRejected(ValueError):
    pass


class HelpRequested(Exception):
    pass


class SilentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ParseRejected(message)

    def exit(self, status=0, message=None):
        if status:
            raise ParseRejected(message or "argument parser rejected the command")
        raise HelpRequested()

    def _print_message(self, message, file=None):
        pass


def _name(node, aliases):
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return _name(node.value, aliases) + "." + node.attr
    return "?"


def _main_guard(node):
    return isinstance(node, ast.If) and not node.orelse and isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__" and len(node.test.ops) == 1 and isinstance(node.test.ops[0], ast.Eq) and len(node.test.comparators) == 1 and isinstance(node.test.comparators[0], ast.Constant) and node.test.comparators[0].value == "__main__"


def _scope(node, parents):
    owner = None
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match, ast.Lambda, ast.comprehension)) and not _main_guard(node):
            raise Unsupported("conditional_or_dynamic_parser_control_flow")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if owner is not None or isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef)):
                raise Unsupported("nested_or_class_parser_scope")
            owner = node
    return owner


def _literal(node, aliases, assigned):
    if isinstance(node, ast.Name) and node.id in {"str", "int", "float"} and node.id not in assigned:
        return {"__trusted_type__": node.id}
    qualified = _name(node, aliases)
    if qualified == "argparse.BooleanOptionalAction":
        return {"__trusted_action__": "BooleanOptionalAction"}
    if qualified == "argparse.SUPPRESS":
        return {"__trusted_constant__": "SUPPRESS"}
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, RecursionError):
        raise Unsupported("non_literal_or_custom_argument_value") from None
    if isinstance(value, (list, tuple)):
        if len(value) > 256 or any(type(v) not in {str, int, float, bool, type(None)} for v in value):
            raise Unsupported("unsupported_nested_literal")
        return list(value)
    if type(value) not in {str, int, float, bool, type(None)}:
        raise Unsupported("unsupported_literal_type")
    return value


def _materialize(value):
    if isinstance(value, dict):
        if "__trusted_type__" in value:
            return {"str": str, "int": int, "float": float}[value["__trusted_type__"]]
        if "__trusted_action__" in value:
            return argparse.BooleanOptionalAction
        if "__trusted_constant__" in value:
            return argparse.SUPPRESS
        raise Unsupported("invalid_trusted_marker")
    return value


def grammar(source):
    """Return a JSON-safe grammar. Never eval/compile/import package source."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        raise Unsupported("source_cannot_be_parsed") from None
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise Unsupported("source_ast_budget_exceeded")
    parents = {child: node for node in nodes for child in ast.iter_child_nodes(node)}
    aliases, assigned = {}, {n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in {"exec", "eval", "compile", "globals", "locals", "setattr", "delattr", "__import__"} for n in nodes):
        raise Unsupported("runtime_reflection_or_code_generation")
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "argparse":
                    aliases[alias.asname or alias.name] = alias.name
        if isinstance(node, ast.ImportFrom) and node.module == "argparse" and not node.level:
            for alias in node.names:
                aliases[alias.asname or alias.name] = "argparse." + alias.name
    if any(name in assigned for name in aliases):
        raise Unsupported("argparse_import_shadowed")
    if any(isinstance(n, ast.Attribute) and isinstance(n.ctx, (ast.Store, ast.Del)) and _name(n, aliases).startswith("argparse.") for n in nodes):
        raise Unsupported("argparse_module_mutated")
    constructors = [n for n in nodes if isinstance(n, ast.Call) and _name(n.func, aliases) == "argparse.ArgumentParser"]
    if len(constructors) != 1:
        raise Unsupported("single_known_argparse_constructor_not_found")
    ctor = constructors[0]
    parent = parents.get(ctor)
    if not isinstance(parent, ast.Assign) or len(parent.targets) != 1 or not isinstance(parent.targets[0], ast.Name):
        raise Unsupported("parser_binding_not_simple")
    binding = parent.targets[0].id
    owner = _scope(ctor, parents)
    if sum(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id == binding for n in nodes) != 1:
        raise Unsupported("parser_binding_reassigned")
    accepted_ctor = {"prog", "description", "epilog", "usage", "allow_abbrev", "add_help", "exit_on_error", "prefix_chars", "argument_default", "conflict_handler", "fromfile_prefix_chars"}
    if ctor.args:
        raise Unsupported("positional_constructor_parameters")
    config = {}
    for kw in ctor.keywords:
        if kw.arg not in accepted_ctor:
            raise Unsupported("unsupported_parser_constructor_keyword")
        if kw.arg in {"prog", "description", "epilog", "usage"}:
            continue  # Display-only fields do not define argv acceptance.
        config[kw.arg] = _literal(kw.value, aliases, assigned)
    if config.get("fromfile_prefix_chars") is not None:
        raise Unsupported("argument_file_expansion_not_permitted")
    for key in ["add_help", "allow_abbrev", "exit_on_error"]:
        if key in config and type(config[key]) is not bool:
            raise Unsupported("parser_boolean_not_literal_bool")
    if "prefix_chars" in config and (not isinstance(config["prefix_chars"], str) or not 1 <= len(config["prefix_chars"]) <= 8):
        raise Unsupported("unsupported_prefix_chars")
    receivers = {binding}
    operations, parse_calls, declarations = [], [], []
    # Group names are discovered in source order, rather than merged across AST.
    for node in sorted((n for n in nodes if isinstance(n, ast.Call)), key=lambda n: (n.lineno, n.col_offset)):
        if not isinstance(node.func, ast.Attribute) or not isinstance(node.func.value, ast.Name) or node.func.value.id not in receivers:
            continue
        receiver, method = node.func.value.id, node.func.attr
        if _scope(node, parents) is not owner:
            raise Unsupported("parser_used_across_different_scopes")
        if method == "parse_args":
            if receiver != binding or node.args or node.keywords:
                raise Unsupported("non_default_argv_or_namespace")
            parse_calls.append(node.lineno)
            continue
        if parse_calls:
            raise Unsupported("parser_mutated_after_parse")
        if method not in {"add_argument", "add_argument_group", "add_mutually_exclusive_group", "set_defaults"}:
            raise Unsupported("unsupported_parser_method:" + method)
        args = [_literal(n, aliases, assigned) for n in node.args]
        kwargs = {}
        for kw in node.keywords:
            if kw.arg is None:
                raise Unsupported("dynamic_keyword_expansion")
            kwargs[kw.arg] = _literal(kw.value, aliases, assigned)
        operation = {"receiver": receiver, "method": method, "args": args, "kwargs": kwargs, "line": node.lineno}
        if method in {"add_argument_group", "add_mutually_exclusive_group"}:
            group_parent = parents.get(node)
            if not isinstance(group_parent, ast.Assign) or len(group_parent.targets) != 1 or not isinstance(group_parent.targets[0], ast.Name):
                raise Unsupported("group_binding_not_simple")
            group = group_parent.targets[0].id
            if group in receivers or sum(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id == group for n in nodes) != 1:
                raise Unsupported("group_binding_reassigned")
            receivers.add(group)
            operation["bind"] = group
        elif method == "add_argument":
            if not args or any(not isinstance(a, str) or not a or len(a) > 120 for a in args):
                raise Unsupported("argument_names_not_literal_strings")
            if not set(kwargs) <= {"action", "nargs", "const", "default", "type", "choices", "required", "help", "metavar", "dest"}:
                raise Unsupported("unsupported_argument_keyword")
            action = kwargs.get("action", "store")
            if not isinstance(action, dict) and action not in {"store", "store_const", "store_true", "store_false", "append", "append_const", "count", "help", "version"}:
                raise Unsupported("custom_argument_action")
            if "type" in kwargs and not (isinstance(kwargs["type"], dict) and "__trusted_type__" in kwargs["type"]):
                raise Unsupported("custom_type_converter")
            nargs = kwargs.get("nargs")
            if nargs is not None and not (type(nargs) is int and 0 <= nargs <= 64 or nargs in {"?", "*", "+"}):
                raise Unsupported("unsupported_nargs")
            if "required" in kwargs and type(kwargs["required"]) is not bool:
                raise Unsupported("required_not_literal_bool")
            declarations.append(node.lineno)
        operations.append(operation)
    if len(parse_calls) != 1 or len(declarations) > MAX_ARGUMENTS:
        raise Unsupported("one_parse_call_and_bounded_arguments_required")
    # Passing a parser elsewhere, rebinding methods, and aliases escape the DSL.
    for node in nodes:
        if not isinstance(node, ast.Name) or node.id not in receivers or not isinstance(node.ctx, ast.Load):
            continue
        attribute = parents.get(node)
        call = parents.get(attribute)
        if not (isinstance(attribute, ast.Attribute) and isinstance(call, ast.Call) and call.func is attribute):
            raise Unsupported("parser_escapes_literal_grammar")
    plan = {"constructor": config, "binding": binding, "operations": operations, "declaration_lines": declarations, "parse_line": parse_calls[0]}
    plan["sha256"] = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return plan


def check(source, command):
    result = {"method": METHOD, "runtime_verified": False, "package_code_executed": False,
              "checker_python": platform.python_version(), "findings": [],
              "assumptions": ["目标使用相同版本标准库argparse，未被本地同名模块或外部运行时修改", "源码字面参数声明正常到达；外部控制流与任务功能未核验"],
              "scope": "只核验文档命令的参数语法；不证明脚本能完成任务、依赖齐全或运行安全"}
    try:
        tokens = shlex.split(command, comments=True)
        if len(tokens) < 2 or tokens[0] not in {"python", "python3"} or not tokens[1].endswith(".py"):
            raise Unsupported("not_a_literal_python_script_invocation")
        if len(tokens) > 256 or any(t in {"|", ";", "&&", "||", ">", ">>"} for t in tokens) or any(any(c in t for c in "$`<>\n\r") for t in tokens[2:]):
            raise Unsupported("shell_expansion_compound_or_placeholder")
        plan = grammar(source)
        result["grammar"] = plan
        parser = SilentParser(prog=tokens[1], **{k: _materialize(v) for k, v in plan["constructor"].items()})
        receivers = {plan["binding"]: parser}
        for op in plan["operations"]:
            value = getattr(receivers[op["receiver"]], op["method"])(*op["args"], **{k: _materialize(v) for k, v in op["kwargs"].items()})
            if "bind" in op:
                receivers[op["bind"]] = value
        try:
            parser.parse_args(tokens[2:])
            result.update(status="consistent_static", explanation="可信标准库接受按源码字面声明重建的参数语法；没有运行技能代码")
        except (ParseRejected, argparse.ArgumentError) as exc:
            result.update(status="inconsistent_static", explanation="可信标准库拒绝此文档命令的参数语法")
            result["findings"] = [{"kind": "argparse_command_rejected", "parser_message": str(exc), "rule": "literal_argparse_grammar_rejects_documented_argv"}]
        except HelpRequested:
            result.update(status="help_not_task_execution", explanation="该命令只请求帮助或版本，未验证任务执行")
    except (Unsupported, ValueError, TypeError, argparse.ArgumentError, RecursionError, KeyError) as exc:
        result.update(status="unknown", reasons=[str(exc)[:240]], explanation="超出受支持的静态参数语法，保留待验证")
    return result
