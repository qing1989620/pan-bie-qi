"""Resolve public Skill links to bounded, commit-pinned text; never execute them."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from .inspection import (
    MAX_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    clean,
    redact,
    validate_files,
    validate_path,
)

from .web_fetch import fetch_url

HOSTS = {"github.com", "www.github.com", "raw.githubusercontent.com", "skills.sh"}
TEXT = {".md", ".py", ".json", ".yaml", ".yml", ".toml", ".txt", ".sh", ".ps1", ".js", ".ts", ".mjs", ".cjs", ".tsx", ".jsx", ".go", ".rs", ".rb", ".java", ".sql", ".r", ".ini", ".cfg", ".c", ".h"}
_CACHE: OrderedDict[str, bytes] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_BYTES = 12_000_000
_RAW_BACKOFF_UNTIL = 0.0
PUBLISHED_SNAPSHOT = Path(__file__).resolve().parent / "data/verified-link-snapshot.json"


class SourceError(ValueError):
    """A safe product-facing acquisition error, never a raw provider body."""


class SourceUnavailable(SourceError):
    """Catalog discovery can be unavailable even when a direct document is public."""


@dataclass(frozen=True)
class Link:
    owner: str
    repo: str
    tail: tuple[str, ...]
    kind: str
    requested_url: str


def parse_link(url: str) -> Link:
    if not isinstance(url, str) or len(url) > 2048 or re.search(r"[\x00-\x20\x7f\\]", url):
        raise SourceError("请粘贴完整的HTTPS开源技能链接，不含空格或凭据")
    try:
        value = urlsplit(url)
        if value.scheme != "https" or value.hostname not in HOSTS or value.port not in {None, 443} or value.username is not None or value.password is not None:
            raise SourceError("目前支持GitHub、raw.githubusercontent.com和skills.sh的公开HTTPS链接")
    except ValueError:
        raise SourceError("链接地址无效；请使用公开HTTPS链接，不附带密钥") from None
    parts = [unquote(part) for part in value.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise SourceError("请提供包含仓库或单个技能路径的链接")
    owner, repo = parts[:2]
    repo = repo.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,99}", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repo) or repo in {".", ".."}:
        raise SourceError("GitHub仓库名称无法识别")
    kind, tail = "repository", parts[2:]
    if value.hostname == "skills.sh":
        if len(tail) != 1:
            raise SourceError("skills.sh链接应包含作者、仓库和技能名称")
        kind = "catalog"
    elif value.hostname == "raw.githubusercontent.com":
        if tail[:2] in [["refs", "heads"], ["refs", "tags"]]:
            tail = tail[2:]
        kind = "file"
    elif tail:
        kind = {"tree": "directory", "blob": "file", "raw": "file"}.get(tail[0], "unsupported")
        tail = tail[1:]
        if tail[:2] in [["refs", "heads"], ["refs", "tags"]]:
            tail = tail[2:]
    if kind == "unsupported" or (kind == "file" and len(tail) < 2) or (kind == "directory" and not tail):
        raise SourceError("请使用仓库主页、tree技能目录或blob/SKILL.md链接")
    if kind == "file" and (not tail or tail[-1] != "SKILL.md"):
        raise SourceError("文件链接需要指向SKILL.md；也可以提供技能目录链接")
    for part in tail:
        for segment in part.split("/"):
            validate_path(segment)
    # Query strings and fragments never enter outbound requests or exported logs.
    canonical = "https://" + value.hostname + value.path
    return Link(owner, repo, tuple(tail), kind, canonical)


class Reader:
    def __init__(self):
        self.deadline = time.monotonic() + 60
        self.requests = 0
        self.cache_hits = 0
        self.lock = threading.Lock()

    def raw(self, url: str, limit: int, *, immutable=False, missing_ok=False, timeout_limit=15) -> bytes | None:
        if immutable:
            with _CACHE_LOCK:
                if url in _CACHE:
                    data = _CACHE.pop(url)
                    _CACHE[url] = data
                    if len(data) > limit:
                        raise SourceError("来源文件超出读取范围")
                    with self.lock:
                        self.cache_hits += 1
                    return data
        remaining = self.deadline - time.monotonic()
        with self.lock:
            if self.requests >= 150 or remaining <= .1:
                raise SourceError("链接读取达到本轮时间或请求上限，请选择更小的技能目录")
            self.requests += 1
        # Only URLs we construct on these two hosts can reach the public fetcher.
        if urlsplit(url).hostname not in {"api.github.com", "raw.githubusercontent.com"}:
            raise SourceError("来源地址不受支持")
        result = fetch_url(url, timeout=min(remaining, timeout_limit), max_bytes=limit, max_redirects=0)
        status = result.get("status_code")
        if missing_ok and status == 404:
            return None
        if result["state"] != "fetched":
            if status in {403, 429}:
                raise SourceUnavailable("GitHub公开接口暂时限制读取。可使用单个 Skill 目录或 SKILL.md 的直接链接。")
            if status == 404:
                raise SourceError("未找到公开仓库、版本或文件；请核对链接是否公开且有效")
            if status in {301, 302, 307, 308}:
                raise SourceError("来源发生跳转，请使用仓库当前的直接链接")
            if result["state"] == "too_large":
                raise SourceError("来源目录过大，请改用具体技能目录的链接")
            raise SourceUnavailable("公开来源读取未完成，可能是网络、超时或响应格式问题；未据此判定技能质量")
        data = result["raw_body"]
        if immutable:
            with _CACHE_LOCK:
                _CACHE[url] = data
                while sum(map(len, _CACHE.values())) > _CACHE_BYTES:
                    _CACHE.popitem(last=False)
        return data

    def document(self, url, *, limit=5_000_000, **kwargs):
        data = self.raw(url, limit, **kwargs)
        if data is None:
            return None
        try:
            return json.loads(data)
        except (ValueError, UnicodeDecodeError):
            raise SourceError("GitHub返回了无法解析的目录信息") from None


def _tree(reader: Reader, base: str, sha: str, *, recursive=False) -> dict:
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise SourceError("无法固定来源树版本")
    document = reader.document(base + "/git/trees/" + sha + ("?recursive=1" if recursive else ""), immutable=True)
    if not isinstance(document, dict) or not isinstance(document.get("tree"), list) or document.get("truncated"):
        raise SourceError("GitHub目录信息不完整，请改用更具体的技能目录链接")
    return document


def _scope_tree(reader, base, tree_sha, scope):
    for part in scope.split("/") if scope else []:
        entries = _tree(reader, base, tree_sha)["tree"]
        node = next((item for item in entries if item["path"] == part and item["type"] == "tree"), None)
        if node is None:
            raise SourceError("该固定版本中未找到所选技能目录")
        tree_sha = node["sha"]
    return _tree(reader, base, tree_sha, recursive=True)["tree"]


def _direct_document(link):
    if link.kind not in {"file", "directory"} or not link.tail:
        return None
    ref = link.tail[0]
    path = "/".join(link.tail[1:])
    path = (path + "/" if path else "") + "SKILL.md" if link.kind == "directory" else path
    try:
        return ref, validate_path(path)
    except ValueError:
        return None


def _single_document_result(link, data, source, *, complete):
    try:
        text = data.decode("utf-8-sig")
        files = {"SKILL.md": text}
        inventory = ["SKILL.md"]
        validate_files(files, inventory)
    except (ValueError, UnicodeDecodeError):
        raise SourceError("SKILL.md不是可检查的 UTF-8 文档") from None
    root = source["package_root"]
    identity = f"{link.owner}/{link.repo}:{root or 'root'}"[:120]
    return {"state": "imported", "source": source,
            "package": {"identity": identity, "files": files, "inventory": inventory, "inventory_complete": complete},
            "model_api_calls": 0, "skill_code_executed": False}


def _frozen_snapshot(link, target, revision):
    """Re-use bytes previously checked against a public tree, only at the pinned ref."""
    if target is None or not re.fullmatch(r"[a-f0-9]{40}", target[0]):
        return None
    ref, path = target
    try:
        snapshot = json.loads(PUBLISHED_SNAPSHOT.read_text(encoding="utf-8"))
        if snapshot.get("schema") != "verified-public-document-snapshot-1":
            return None
        source = snapshot["source"]
        if ((link.owner+"/"+link.repo).casefold() != source["repository"].casefold()
                or ref != source["commit"] or path != source["skill_path"]
                or revision not in {None,ref}):
            return None
        raw = (PUBLISHED_SNAPSHOT.parent / "manufacturing-codebook-document.md").read_bytes()
        hashes = source["file_hashes"]["SKILL.md"]
        if (len(raw) != hashes["bytes"] or hashlib.sha256(raw).hexdigest() != hashes["raw_bytes_sha256"]
                or hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest() != hashes["git_blob_sha1"]
                or source["inventory_files"] != 1 or not source["inventory_complete"]):
            return None
        hashes["transport"] = "verified_local_snapshot"
        source.update(requested_url=link.requested_url, source_mode="verified_local_snapshot",
                      network_requests=0, immutable_cache_hits=1)
        return _single_document_result(link, raw, source, complete=True)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _direct_fallback(link, target, revision, reader):
    ref, path = target
    ref = revision or ref
    repository = f"{link.owner}/{link.repo}"
    target_url = f"https://raw.githubusercontent.com/{repository}/"+quote(ref, safe="")+"/"+quote(path, safe="/")
    fixed = ref if re.fullmatch(r"[a-f0-9]{40}", ref) else None
    raw = reader.raw(target_url, MAX_FILE_BYTES, immutable=bool(fixed), timeout_limit=8)
    try:
        analyzed = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SourceError("SKILL.md不是可检查的 UTF-8 文档") from None
    source = {
        "requested_url": link.requested_url, "repository": repository, "commit": fixed, "requested_ref": ref,
        "retrieved_at_utc": datetime.now(UTC).isoformat(), "repository_license": None,
        "source_mode": "direct_document_only",
        "canonical_url": f"https://github.com/{repository}/blob/"+quote(ref,safe="")+"/"+quote(path,safe="/"),
        "trust": "Public raw document read by HTTPS; file hash recorded; package tree not verified.",
        "skill_path": path, "package_root": path.removesuffix("SKILL.md").rstrip("/"),
        "file_hashes": {"SKILL.md":{"raw_bytes_sha256":hashlib.sha256(raw).hexdigest(),
            "analyzed_text_sha256":hashlib.sha256(analyzed.encode()).hexdigest(), "bytes":len(raw), "transport":"github_raw_document_only"}},
        "files_read":1, "inventory_files":1, "inventory_complete":False, "omitted":[], "omitted_count":0,
        "omissions_truncated":False, "network_requests":reader.requests, "immutable_cache_hits":reader.cache_hits,
    }
    return _single_document_result(link, raw, source, complete=False)


def import_link(url: str, skill_path: str | None = None, revision: str | None = None) -> dict:
    link = parse_link(url)
    if skill_path:
        skill_path = validate_path(skill_path)
        if not skill_path.endswith("SKILL.md"):
            raise SourceError("所选候选需要指向SKILL.md")
    if revision and not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise SourceError("固定提交版本需要完整的40位十六进制标识")
    target = _direct_document(link)
    if skill_path and target and skill_path != target[1]:
        target = None
    known = _frozen_snapshot(link, target, revision)
    if known:
        return known
    reader = Reader()
    try:
        return _import_tree(link, skill_path, revision, reader)
    except SourceUnavailable:
        if target is None:
            raise
        return _direct_fallback(link, target, revision, reader)


def _import_tree(link, skill_path, revision, reader):
    base = f"https://api.github.com/repos/{link.owner}/{link.repo}"
    repository = reader.document(base)
    if not isinstance(repository, dict) or repository.get("private") is not False:
        raise SourceError("仅支持可公开读取的GitHub仓库")
    scope = ""
    commit = None
    requested_ref = repository.get("default_branch", "main")
    if link.kind in {"directory", "file"}:
        tail = list(link.tail)
        if len(tail) > 18:
            raise SourceError("链接路径层级过深，请使用更短的固定提交链接")
        default_parts = requested_ref.split("/")
        # Common default-branch URLs need one lookup. Otherwise use longest
        # resolvable ref (up to eight segments), including branches with slashes.
        if tail and re.fullmatch(r"[a-f0-9]{40}", tail[0]):
            spans = [1]
        elif tail[:len(default_parts)] == default_parts:
            spans = [len(default_parts)]
        else:
            spans = list(range(min(8, len(tail) - (link.kind == "file")), 0, -1))
        for span in spans:
            ref = "/".join(tail[:span])
            found = reader.document(base + "/commits/" + quote(ref, safe=""), missing_ok=True)
            if found:
                commit, requested_ref = found, ref
                scope = "/".join(tail[span:])
                if link.kind == "file":
                    scope = scope.removesuffix("/SKILL.md") if scope != "SKILL.md" else ""
                break
        if commit is None:
            raise SourceError("链接中的分支、标签或提交无法识别")
    if revision or commit is None:
        commit = reader.document(base + "/commits/" + quote(revision or requested_ref, safe=""))
    try:
        sha, tree_sha = commit["sha"], commit["commit"]["tree"]["sha"]
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise SourceError("来源提交信息无法核对") from None
    if skill_path:
        if scope and skill_path != scope + "/SKILL.md" and not skill_path.startswith(scope + "/"):
            raise SourceError("所选技能不在原链接指向的目录内")
        scope = skill_path.rsplit("/", 1)[0] if "/" in skill_path else ""
    entries = _scope_tree(reader, base, tree_sha, scope)
    candidates = []
    for item in entries:
        path = item.get("path", "")
        if path.split("/")[-1] != "SKILL.md" or item.get("type") != "blob" or item.get("mode") not in {"100644", "100755"}:
            continue
        try:
            validate_path(path)
        except ValueError:
            continue
        full = (scope + "/" if scope else "") + path
        candidates.append({"path": full, "name": PurePosixPath(full).parent.name or link.repo,
                           "url": f"https://github.com/{link.owner}/{link.repo}/blob/{sha}/" + quote(full, safe="/")})
    candidates.sort(key=lambda item: item["path"])
    if link.kind == "catalog" and not skill_path:
        candidates = [c for c in candidates if c["name"] == link.tail[0]]
    primary = next((c for c in candidates if c["path"] == (scope + "/" if scope else "") + "SKILL.md"), None)
    source = {"requested_url": link.requested_url, "repository": f"{link.owner}/{link.repo}", "commit": sha,
              "requested_ref": requested_ref, "retrieved_at_utc": datetime.now(UTC).isoformat(),
              "repository_license": (repository.get("license") or {}).get("spdx_id"),
              "trust": "GitHub HTTPS snapshot, commit and blob hashes; not author identity or third-party quality certification"}
    if primary is None and len(candidates) == 1:
        primary = candidates[0]
    if primary is None:
        if not candidates:
            raise SourceError("该链接范围内没有可读取的SKILL.md；请提供单个Skill或技能库链接")
        return clean({"state": "selection_required", "source": source, "candidates": candidates[:200], "candidate_count": len(candidates),
                      "candidates_truncated": len(candidates) > 200, "model_api_calls": 0, "skill_code_executed": False})
    selected = primary["path"]
    folder = selected.rsplit("/", 1)[0] if "/" in selected else ""
    relative_prefix = folder[len(scope):].strip("/")
    if relative_prefix:
        entries = _scope_tree(reader, base, tree_sha, folder)
    inventory, readable, omitted = [], [], []
    inventory_complete = True
    for item in entries:
        if item.get("type") == "tree":
            continue
        path = item.get("path", "")
        try:
            path = validate_path(path)
        except ValueError:
            inventory_complete = False
            omitted.append({"path": path[:240], "reason": "unsupported_path"})
            continue
        if len(inventory) == 2000:
            inventory_complete = False
            omitted.append({"path": path, "reason": "inventory_limit"})
            break
        inventory.append(path)
        if item.get("mode") not in {"100644", "100755"} or item.get("type") != "blob":
            omitted.append({"path": path, "reason": "symlink_or_submodule_not_followed"})
        elif PurePosixPath(path).suffix.lower() in TEXT or PurePosixPath(path).name.lower().startswith(("license", "notice", "dockerfile")):
            readable.append(item)
        else:
            omitted.append({"path": path, "reason": "non_text_resource"})
    readable.sort(key=lambda item: (item["path"] != "SKILL.md", PurePosixPath(item["path"]).suffix not in {".py", ".sh", ".js", ".ts"}, item["path"]))
    planned, total = [], 0
    for item in readable:
        size = item.get("size")
        if not isinstance(size, int) or not 0 <= size <= MAX_FILE_BYTES or total + size > MAX_BYTES or len(planned) >= MAX_FILES:
            omitted.append({"path": item["path"], "reason": "text_read_budget"})
        else:
            total += size
            planned.append(item)
    if not any(item["path"] == "SKILL.md" for item in planned):
        raise SourceError("SKILL.md缺失或超出600KB读取限制")
    def read_file(item):
        global _RAW_BACKOFF_UNTIL
        full = (folder + "/" if folder else "") + item["path"]
        raw_url = f"https://raw.githubusercontent.com/{link.owner}/{link.repo}/{sha}/" + quote(full, safe="/")
        data, transport = None, "github_raw"
        with _CACHE_LOCK:
            try_raw = item["path"] != "SKILL.md" and time.monotonic() >= _RAW_BACKOFF_UNTIL
        if try_raw:
            try:
                data = reader.raw(raw_url, MAX_FILE_BYTES, immutable=True, timeout_limit=3)
            except SourceError:
                with _CACHE_LOCK:
                    _RAW_BACKOFF_UNTIL = time.monotonic() + 120
        if data is None:
            try:
                blob = reader.document(base + "/git/blobs/" + item["sha"], limit=900_000, immutable=True)
                if not isinstance(blob, dict) or blob.get("encoding") != "base64" or not isinstance(blob.get("content"), str):
                    raise SourceError("固定版本的文件内容无法解码")
                data = base64.b64decode(re.sub(r"\s", "", blob["content"]), validate=True)
                if len(data) > MAX_FILE_BYTES:
                    raise SourceError("固定版本的文件超过读取限制")
                transport = "github_blob_api"
            except ValueError:
                if item["path"] == "SKILL.md":
                    raise SourceError("SKILL.md读取未完成，可能是GitHub限额或网络问题；本次未形成评估") from None
                return item["path"], None, {"path": item["path"], "reason": "source_read_incomplete"}
        if len(data) != item["size"] or hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() != item["sha"]:
            raise SourceError("下载内容与固定提交中的Git文件哈希不一致，已停止评估")
        try:
            text = data.decode("utf-8-sig")
            if "\0" in text:
                raise UnicodeError
        except UnicodeError:
            return item["path"], None, {"path": item["path"], "reason": "not_utf8_text"}
        return item["path"], text, {"git_blob_sha1": item["sha"], "raw_bytes_sha256": hashlib.sha256(data).hexdigest(),
                                    "analyzed_text_sha256": hashlib.sha256(text.encode()).hexdigest(), "bytes": len(data), "transport": transport}
    files, hashes = {}, {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="skill-source") as pool:
        for path, text, provenance in pool.map(read_file, planned):
            if text is None:
                omitted.append(provenance)
            else:
                files[path], hashes[path] = text, provenance
    if "SKILL.md" not in files:
        raise SourceError("SKILL.md不是可解析的UTF-8文本")
    identity = f"{link.owner}/{link.repo}:{folder or 'root'}"[:120]
    validate_files(files, inventory)
    source.update(skill_path=selected, package_root=folder, canonical_url=primary["url"], file_hashes=hashes,
                  files_read=len(files), inventory_files=len(inventory), inventory_complete=inventory_complete,
                  omitted=omitted[:200], omitted_count=len(omitted), omissions_truncated=len(omitted) > 200,
                  network_requests=reader.requests, immutable_cache_hits=reader.cache_hits,
                  package_instructions_treated_as="untrusted assessment data; not executed as assistant or host instructions")
    return {"state": "imported", "source": source, "package": {"identity": identity, "files": files, "inventory": inventory, "inventory_complete": inventory_complete},
            "model_api_calls": 0, "skill_code_executed": False}


def public_package(package: dict) -> tuple[dict, bool]:
    files = {path: redact(text) for path, text in package["files"].items()}
    changed = files != package["files"]
    return {**package, "files": files}, changed
