"""Helpers for stable evidence identifiers and documented Python commands."""

import hashlib
import json
import re
import shlex
from typing import Any


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def commands(text: str) -> list[dict[str, Any]]:
    lines, output, index = text.splitlines(), [], 0
    while index < len(lines):
        line = lines[index]
        start = index + 1
        if not re.match(r"^\s*python(?:3)?\s+\S+\.py(?:\s|$)", line):
            index += 1
            continue
        command = line.strip()
        while command.endswith("\\") and index + 1 < len(lines):
            index += 1
            command = command[:-1] + " " + lines[index].strip()
        try:
            script = shlex.split(command)[1]
        except (ValueError, IndexError):
            script = None
        output.append({"command": command, "script": script, "start_line": start, "end_line": index + 1})
        index += 1
    return output
