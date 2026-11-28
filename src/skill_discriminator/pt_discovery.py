"""Discover public Skill candidates without trusting directory rankings as evidence."""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import threading
import time
from collections import OrderedDict
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
DEEPSEEK_WEB = "DeepSeek Web"
WEB_PROMPT = (
    "Find live GitHub sources by calling your built-in web search before answering. "
    'Use a targeted query with site:github.com, "SKILL.md", and the requested capability. '
    "When given a focus phrase, use it as the main search terms. Search for the operation the "
    "Skill must perform. For diagrams, focus on the diagram tool or output format. "
    "Prefer links to the actual SKILL.md or its containing directory. "
    'Then return JSON with up to two short English "queries" and "links" to the actual '
    "GitHub sources you just found. Never repeat personal details or credentials."
)
_SEARCH_CACHE = OrderedDict()
_SEARCH_CACHE_LOCK = threading.Lock()


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
    (("mermaid",),
     "https://github.com/softaworks/agent-toolkit/tree/3027f20f3181758385a1bb8c022d4041dfb4de84/skills/mermaid-diagrams"),
    (("mermaid",),
     "https://github.com/shep-ai/shep/tree/b6792e4d9def48f6542d858af4eae095ab5527af/.claude/skills/mermaid-diagrams"),
    (("point_cloud_registration",),
     "https://github.com/a5c-ai/babysitter/tree/feb68abe397acc14f32b34984975c48fedba2b33/library/specializations/robotics-simulation/skills/point-cloud-processing"),
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


def _github_candidate(url: str, query: str | None) -> Candidate | None:
    try:
        link = parse_link(url)
    except ValueError:
        return None
    if link.kind == "catalog":
        return None
    if link.kind == "file" and len(link.tail) > 2:
        slug = link.tail[-2]
    elif link.kind == "directory" and len(link.tail) > 1:
        slug = link.tail[-1]
    else:
        slug = link.repo
    return Candidate(link.requested_url, f"{link.owner}/{link.repo}", slug, DEEPSEEK_WEB, query)


def _response_phrases(content) -> list[str]:
    if not isinstance(content, str):
        return []
    try:
        payload = json.loads(content.strip().removeprefix("```json").removesuffix("```"))
    except ValueError:
        return []
    phrases = payload.get("queries") if isinstance(payload, dict) else None
    if not isinstance(phrases, list):
        return []
    return list(dict.fromkeys(filter(None, (model_query(value) for value in phrases[:2]))))


def _deepseek_web_search(task: Task, deadline: float, api_key: str) -> tuple[dict, list[Candidate]]:
    text = f"Search the web now for open-source GitHub SKILL.md files for this task:\n{task.text[:700]}"
    if task.requirements:
        text = f"Search focus: {task.queries[0]}\nTask: {text}"
    observation = {"provider": DEEPSEEK_WEB, "state": "unavailable", "hits": 0, "model_calls": 0}
    cache_key = hashlib.sha256(f"{api_key}\0{text}".encode()).digest()
    with _SEARCH_CACHE_LOCK:
        cached = _SEARCH_CACHE.get(cache_key)
        if cached and cached[0] > time.monotonic():
            _SEARCH_CACHE.move_to_end(cache_key)
            return {**cached[1], "model_calls": 0, "tokens": None}, cached[2]
    remaining = deadline - time.monotonic()
    if remaining <= .1:
        return {**observation, "state": "timed_out"}, []
    client = http.client.HTTPSConnection("api.deepseek.com", timeout=min(11, remaining))
    observation["model_calls"] = 1
    try:
        client.request(
            "POST", "/chat/completions",
            json.dumps({
                "model": "deepseek-flash", "thinking": {"type": "disabled"},
                "max_tokens": 180, "tools": [{"type": "deepseek_web_search"}],
                "messages": [{"role": "system", "content": WEB_PROMPT},
                             {"role": "user", "content": text}],
            }, ensure_ascii=False).encode(),
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        response = client.getresponse()
        if response.status != 200:
            state = {
                401: "authentication_failed", 402: "balance_unavailable",
                403: "access_denied", 429: "temporarily_limited",
            }.get(response.status, "unavailable")
            return {**observation, "state": state}, []
        payload = json.loads(response.read(256_000))
        choice = payload["choices"][0]
        if choice.get("finish_reason") == "content_filter":
            return {**observation, "state": "content_filtered"}, []
        message = choice["message"]
        phrases = _response_phrases(message.get("content"))
        hits = {}
        completed = 0
        for step in message.get("server_tool_calls", [])[:8]:
            for call in step.get("calls", [])[:8]:
                if call.get("type") != "deepseek_web_search" or call.get("status") != "completed":
                    continue
                completed += 1
                for source in call.get("sources", [])[:20]:
                    if isinstance(source, dict) and (candidate := _github_candidate(
                        source.get("url"), phrases[0] if phrases else None,
                    )):
                        hits.setdefault(candidate.key, candidate)
        usage = payload.get("usage") or {}
        observation.update(
            state="searched" if completed else "not_used", hits=len(hits),
            terms=phrases, tokens=usage.get("total_tokens"),
        )
        candidates = list(hits.values())
        if completed:
            with _SEARCH_CACHE_LOCK:
                _SEARCH_CACHE[cache_key] = (time.monotonic() + 180, observation, candidates)
                _SEARCH_CACHE.move_to_end(cache_key)
                if len(_SEARCH_CACHE) > 32:
                    _SEARCH_CACHE.popitem(last=False)
        return observation, candidates
    except TimeoutError:
        return {**observation, "state": "timed_out"}, []
    except (http.client.HTTPException, OSError, ValueError, AttributeError, TypeError, KeyError, IndexError):
        return observation, []
    finally:
        client.close()


def discover(
    task: Task, extra_queries: list[str], deadline: float, *, search_online: bool,
    deepseek_api_key: str | None = None,
) -> tuple[list[Candidate], dict]:
    seeds = _seeds(task)
    queries = list(dict.fromkeys(task.queries + tuple(extra_queries)))[:MAX_QUERIES]
    observations = []
    pools = []
    if search_online and queries:
        with ThreadPoolExecutor(max_workers=MAX_QUERIES + 1, thread_name_prefix="pt-discovery") as workers:
            web_search = (
                workers.submit(_deepseek_web_search, task, deadline, deepseek_api_key)
                if deepseek_api_key else None
            )
            catalog = workers.map(lambda query: _catalog_search(query, deadline), queries)
            extra_catalog = None
            if web_search is not None:
                observation, hits = web_search.result()
                observations.append(observation)
                pools.append(hits)
                terms = observation.get("terms", [])
                extra_catalog = (
                    workers.submit(_catalog_search, terms[0], deadline)
                    if not task.requirements and terms and terms[0] not in queries else None
                )
            for observation, hits in catalog:
                observations.append({"provider": "skills.sh", **observation})
                pools.append(hits)
            if extra_catalog is not None:
                observation, hits = extra_catalog.result()
                observations.append({"provider": "skills.sh", **observation})
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
