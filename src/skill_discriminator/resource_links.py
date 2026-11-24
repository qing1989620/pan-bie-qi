"""Read literal links to bundled resources from the primary Skill guide."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

DIRECTORIES = ("scripts/", "references/", "assets/", "templates/", "examples/")
LINK = re.compile(
    r"(?<!\\)\[[^\]\n]{1,200}\]\(\s*(?:<([^<>\n]{1,240})>|([^\s()<>]{1,240}))"
    r"\s*(?:\"[^\"\n]*\"\s*|'[^'\n]*'\s*)?\)"
)
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
INLINE_CODE = re.compile(r"(`+)[^`\n]*\1")
MAX_REFERENCES = 100


def bundled_links(document: str) -> list[tuple[int, str]]:
    links, seen, fence = [], set(), ""
    lines = document.splitlines()
    frontmatter = bool(lines and lines[0].strip("\ufeff \t") == "---")
    for number, line in enumerate(lines, 1):
        if frontmatter:
            if number > 1 and line.strip() == "---":
                frontmatter = False
            continue
        if marker := FENCE.match(line):
            token = marker[1]
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end():].strip():
                fence = ""
            continue
        if fence:
            continue
        for link in LINK.finditer(INLINE_CODE.sub("", line)):
            try:
                target = urlsplit(link[1] or link[2])
                path = unquote(target.path, errors="strict").removeprefix("./").rstrip("/")
            except (ValueError, UnicodeError):
                continue
            if target.scheme or target.netloc or not path.startswith(DIRECTORIES) or path in seen:
                continue
            if any(part in {"", ".", ".."} for part in path.split("/")) or "\\" in path or "\0" in path:
                continue
            seen.add(path)
            links.append((number, path))
            if len(links) >= MAX_REFERENCES:
                return links
    return links
