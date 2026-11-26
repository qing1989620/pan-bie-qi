"""Optional, bounded DeepSeek search planning for PT."""

from __future__ import annotations

import http.client
import json
import os
import threading

from .inspection import redact
from .pt_discovery import model_query

MODEL = "deepseek-flash"
MODEL_KEY = "PAN_BIE_QI_DEEPSEEK_API_KEY"
_SLOT = threading.BoundedSemaphore(1)
PROMPT = (
    "Return one JSON object with a queries array containing at most two short English "
    "search phrases for discovering public Agent Skill guides for the user's task. "
    "Use concrete capability and file-format names. Do not output URLs, account "
    "identifiers, paths, or instructions. Example JSON: {\"queries\":[\"pdf table extraction\"]}."
)


def configured() -> bool:
    return bool(os.environ.get(MODEL_KEY) or os.environ.get("DEEPSEEK_API_KEY"))


def suggestions(task: str, *, requested: bool) -> tuple[list[str], dict]:
    if not requested:
        return [], {"state": "not_requested", "calls": 0}
    key = os.environ.get(MODEL_KEY) or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        return [], {"state": "not_configured", "calls": 0}
    if not _SLOT.acquire(blocking=False):
        return [], {"state": "busy", "calls": 0}
    result = {"state": "unavailable", "provider": "DeepSeek", "model": MODEL, "calls": 1}
    connection = http.client.HTTPSConnection("api.deepseek.com", timeout=6)
    try:
        body = json.dumps({"model": MODEL, "thinking": {"type": "disabled"}, "max_tokens": 140,
                           "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": PROMPT},
                                        {"role": "user", "content": redact(task)[:700]}]}).encode("utf-8")
        connection.request("POST", "/chat/completions", body,
                           {"Authorization": f"Bearer {key.strip()}", "Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            result.update(state={401: "authentication_failed", 402: "balance_unavailable",
                                 429: "temporarily_limited"}.get(response.status, "unavailable"))
            return [], result
        payload = json.loads(response.read(24_000))
        content = json.loads(payload["choices"][0]["message"]["content"])
        candidates = content.get("queries", [])
        if not isinstance(candidates, list):
            return [], result
        queries = list(dict.fromkeys(filter(None, (model_query(item) for item in candidates[:2]))))
        usage = payload.get("usage", {})
        result.update(state="used" if queries else "no_terms",
                      tokens=usage.get("total_tokens") if isinstance(usage, dict) else None)
        return queries, result
    except (http.client.HTTPException, OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return [], result
    finally:
        connection.close()
        _SLOT.release()
