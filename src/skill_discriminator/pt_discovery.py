"""Discover public Skill candidates without trusting directory rankings as evidence."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlencode

from .pt_retrieval import Task, terms
from .source_import import SourceError, parse_link
from .web_fetch import fetch_url

CATALOG = "https://skills.sh/api/search"
MAX_QUERIES = 4
MAX_CANDIDATES = 10
VALID_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


@dataclass(frozen=True)
class Candidate:
    url: str
    source: str
    slug: str
    discovery: str
    query: str | None
    installs: int | None = None

    @property
    def key(self) -> str:
        return f"{self.source}/{self.slug}".casefold()


# Checked entry points complement live search when a relevant provider is absent from the first search page.
SEEDS = (
    (("pdf", "pdf_table", "pdf_text", "ocr"),
     "https://github.com/NousResearch/hermes-agent/tree/a15b9710395aa1c4858fb6dd9e4c5014c5ced4e9/skills/productivity/pdf"),
    (("pdf", "pdf_table", "pdf_text", "ocr"),
     "https://github.com/anthropics/skills/tree/e4bb5d960d87126ff3b3a8af1a104b7db67560ba/skills/pdf"),
    (("xlsx", "spreadsheet", "formulas"),
     "https://github.com/anthropics/skills/tree/e4bb5d960d87126ff3b3a8af1a104b7db67560ba/skills/xlsx"),
    (("docx",),
     "https://github.com/anthropics/skills/tree/e4bb5d960d87126ff3b3a8af1a104b7db67560ba/skills/docx"),
    (("pptx",),
     "https://github.com/anthropics/skills/tree/e4bb5d960d87126ff3b3a8af1a104b7db67560ba/skills/pptx"),
    (("browser_test",), "https://github.com/microsoft/playwright-cli/tree/main/skills/playwright-cli"),
    (("cudf", "cudf_groupby"),
     "https://github.com/NVIDIA/skills/tree/252ba53487b73eaafbfcccf3e750c7b350b93db9/skills/accelerated-computing-cudf"),
)


def _seeds(task: Task) -> list[Candidate]:
    relevant = {item.id for item in task.requirements}
    found = []
    for capabilities, url in SEEDS:
        if not relevant.intersection(capabilities):
            continue
        link = parse_link(url)
        found.append(Candidate(url, f"{link.owner}/{link.repo}", link.tail[-1], "reference_catalog", None))
    return found


def _catalog_search(query: str, deadline: float) -> tuple[dict, list[Candidate]]:
    remaining = deadline - time.monotonic()
    if remaining <= .1:
        return {"query": query, "state": "timed_out", "hits": 0}, []
    result = fetch_url(f"{CATALOG}?{urlencode({'q': query, 'limit': 8})}", timeout=min(5.5, remaining),
                       max_bytes=100_000, max_redirects=0)
    status = result.get("status_code")
    if result["state"] != "fetched":
        state = "temporarily_limited" if status in {403, 429} else "unavailable"
        return {"query": query, "state": state, "hits": 0}, []
    try:
        response = json.loads(result["raw_body"])
        rows = response.get("skills", []) if isinstance(response, dict) else []
    except (ValueError, UnicodeDecodeError):
        return {"query": query, "state": "unavailable", "hits": 0}, []
    if not isinstance(rows, list):
        return {"query": query, "state": "unavailable", "hits": 0}, []
    hits = []
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        source, slug, installs = row.get("source"), row.get("skillId"), row.get("installs")
        if not isinstance(source, str) or source.count("/") != 1 or not isinstance(slug, str) or not VALID_SLUG.fullmatch(slug):
            continue
        try:
            public = parse_link(f"https://skills.sh/{source}/{slug}")
        except (ValueError, SourceError):
            continue
        hits.append(Candidate(public.requested_url, f"{public.owner}/{public.repo}", slug, "skills.sh", query,
                              installs if type(installs) is int and installs >= 0 else None))
    return {"query": query, "state": "searched", "hits": len(hits), "top_keys": [row.key for row in hits[:3]]}, hits


def discover(task: Task, extra_queries: list[str], deadline: float, *, search_online: bool) -> tuple[list[Candidate], dict]:
    seeds = _seeds(task)
    queries = list(dict.fromkeys(task.queries + tuple(extra_queries)))[:MAX_QUERIES]
    observations = []
    pools = []
    if search_online and queries:
        with ThreadPoolExecutor(max_workers=MAX_QUERIES, thread_name_prefix="pt-discovery") as workers:
            for observation, hits in workers.map(lambda query: _catalog_search(query, deadline), queries):
                observations.append(observation)
                pools.append(hits)
    # Round-robin keeps a second requested operation from being crowded out by one popular query.
    ranked = (pool[position] for position in range(8) for pool in pools if position < len(pool))
    merged = []
    seen = set()
    for row in (*seeds[:4], *ranked):
        if row.key in seen:
            continue
        seen.add(row.key)
        merged.append(row)
        if len(merged) >= MAX_CANDIDATES:
            break
    return merged, {"provider": "skills.sh", "enabled": search_online, "searched": observations,
                    "reference_matches": len(seeds), "candidates": len(merged)}


def model_query(value: str) -> str | None:
    """Only a few plain search terms may leave the optional planning stage."""
    if not isinstance(value, str) or not re.fullmatch(r"[\w +.-]{2,64}", value.strip()):
        return None
    result = " ".join(value.casefold().split())
    if len(result.split()) > 6 or not terms(result):
        return None
    return result
