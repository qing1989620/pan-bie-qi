"""Classify license evidence in the pinned Skill folder or its nearest licensed ancestor."""

from __future__ import annotations

import re
from urllib.parse import quote

from .inspection import frontmatter
from .source_import import is_package_license

OPEN_LICENSES = {
    item.casefold(): item for item in (
        "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0", "Unlicense", "CC0-1.0",
        "CC-BY-4.0", "CC-BY-SA-4.0",
        "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later", "LGPL-2.1-only",
        "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later", "AGPL-3.0-only", "AGPL-3.0-or-later",
    )
}


def _from_text(text: str) -> tuple[str, str | None]:
    head = text.strip().casefold()
    if re.search(r"\bproprietary\b|\bnon[- ]commercial\b|\bsource available\b|reproduction and distribution (?:is|are) prohibited", head[:6000]):
        return "restricted", None
    match = re.match(r"(?:#\s*)?(?:spdx-license-identifier:\s*)?([a-z0-9.-]+)(?:\s+license)?\s*$", head)
    if match and match[1] in OPEN_LICENSES:
        return "recognized", OPEN_LICENSES[match[1]]
    parts = re.split(r"\s+and\s+", head)
    if len(parts) > 1 and all(part in OPEN_LICENSES for part in parts):
        return "recognized", " AND ".join(OPEN_LICENSES[part] for part in parts)
    if ("permission is hereby granted, free of charge" in head and "without restriction" in head
            and "the software is provided" in head):
        return "recognized", "MIT"
    if re.search(r"apache license\s+version 2\.0", head) and "terms and conditions" in head:
        return "recognized", "Apache-2.0"
    if "redistribution and use in source and binary forms" in head and "this software is provided" in head:
        return "recognized", "BSD-3-Clause" if "endorse or promote" in head else "BSD-2-Clause"
    if "permission to use, copy, modify, and/or distribute this software for any purpose" in head:
        return "recognized", "ISC"
    return "unconfirmed", None


def license_evidence(data: dict) -> dict:
    source, package = data["source"], data["package"]
    unknown = {"state": "unconfirmed", "id": None, "reason": "固定版本未找到可识别的开源许可条款。", "materials": []}
    if not source.get("commit") or not package["inventory_complete"]:
        return {**unknown, "reason": "版本或包清单尚未核全，许可证需要复查。"}
    files = package["files"]
    metadata, _ = frontmatter(files["SKILL.md"].removeprefix("\ufeff"))
    declared = metadata.get("license")
    own_paths = [path for path in package["inventory"] if is_package_license(path)]
    folder = source.get("package_root", "")
    interested = {}
    for path in own_paths:
        full = f"{folder}/{path}" if folder else path
        interested[full] = (files.get(path), source["file_hashes"].get(path, {}).get("raw_bytes_sha256"))
    if not own_paths and isinstance(declared, str) and declared.strip():
        full = source.get("skill_path", "SKILL.md")
        interested[full] = (declared, source["file_hashes"].get("SKILL.md", {}).get("raw_bytes_sha256"))
    if not interested:
        for path, material in package.get("ancestor_licenses", {}).items():
            interested[path] = (material["text"], material["sha256"])
    materials = []
    for path, (text, sha) in sorted(interested.items()):
        state, identity = _from_text(text) if isinstance(text, str) and text.strip() else ("unconfirmed", None)
        url = f"https://github.com/{source['repository']}/blob/{source['commit']}/{quote(path, safe='/')}"
        materials.append({"path": path, "sha256": sha, "state": state, "id": identity, "url": url})
    if isinstance(declared, str) and _from_text(declared)[0] == "restricted" or any(
        row["state"] == "restricted" for row in materials
    ):
        return {"state": "restricted", "id": None, "reason": "许可证含限制性条款。", "materials": materials}
    identities = {row["id"] for row in materials}
    if len(identities) == 1 and None not in identities:
        identity = next(iter(identities))
        if isinstance(declared, str) and (metadata_id := OPEN_LICENSES.get(declared.strip().casefold())) and metadata_id != identity:
            return {**unknown, "reason": "说明声明与许可文件的标识不一致。", "materials": materials}
        return {"state": "recognized", "id": identity,
                "reason": "已在固定版本识别许可证。", "materials": materials}
    return {**unknown, "materials": materials}
