"""Local web and JSON interfaces for the standalone Skill discriminator."""

from __future__ import annotations

import base64
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from . import __version__, adoption, published_benchmarks, source_import
from .pdf_table import MAX_CSV_BYTES, MAX_PDF_BYTES, probe
from .profile import attach as attach_profile
from .service import assess
from .verify import replay_registered

WEB = Path(__file__).resolve().parent / "web"


class BoundedRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            if request.method == "POST":
                body = bytearray()
                async for block in request.stream():
                    if len(body) + len(block) > 6_000_000:
                        return JSONResponse(status_code=413, content={"detail": "请求超过 6 MB。"})
                    body.extend(block)
                request._body = bytes(body)
            try:
                return await original(request)
            except RequestValidationError as exc:
                # Do not echo submitted package text in framework errors.
                details = [{"loc": list(x["loc"]), "msg": x["msg"]} for x in exc.errors()]
                return JSONResponse(status_code=422, content={"detail": details})

        return handler


class Environment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    network_allowed: StrictBool | None = None
    available_keys: dict[str, StrictBool] = Field(default_factory=dict, max_length=64)

    @field_validator("available_keys")
    @classmethod
    def key_names(cls, value: dict[str, bool]) -> dict[str, bool]:
        if any(not re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", key) for key in value):
            raise ValueError("只填写环境变量名称及是否配置。")
        return value


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = Field(default="", max_length=4000)
    environment: Environment = Field(default_factory=Environment)
    use_model: StrictBool = False  # Retain the field for existing request payloads.


class LinkRequest(TaskRequest):
    url: str = Field(min_length=10, max_length=2048)
    skill_path: str | None = Field(default=None, max_length=240)
    revision: str | None = Field(default=None, min_length=40, max_length=40)


class InspectRequest(TaskRequest):
    identity: str = Field(default="my-skill", min_length=1, max_length=120)
    files: dict[str, str] = Field(min_length=1, max_length=128)
    inventory: list[str] = Field(default_factory=list, max_length=2000)
    inventory_complete: StrictBool = False


class PdfTableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pdf_base64: str = Field(min_length=8, max_length=4 * ((MAX_PDF_BYTES + 2) // 3))
    page: int = Field(ge=1, strict=True)
    reference_csv: str = Field(default="", max_length=MAX_CSV_BYTES)
    headers: str = Field(default="", max_length=2_000)
    expected_rows: int | None = Field(default=None, ge=1, le=500, strict=True)


def create_app() -> FastAPI:
    app = FastAPI(title="开源 Skill 判别器", version=__version__)
    router = APIRouter(route_class=BoundedRoute, prefix="/api/discriminator")
    link_slots = threading.BoundedSemaphore(2)
    pdf_slots = threading.BoundedSemaphore(1)
    token = os.environ.get("PAN_BIE_QI_TOKEN", "")

    def access(x_discriminator_token: Annotated[str | None, Header()] = None) -> None:
        if token and not secrets.compare_digest((x_discriminator_token or "").encode(), token.encode()):
            raise HTTPException(401, "需要有效的 X-Discriminator-Token。")

    @router.get("/health")
    def health() -> dict:
        return {"status": "ready", "version": __version__, "link_import_supported": True,
                "pdf_table_probe_supported": True, "model_api_calls": 0, "skill_code_executed": False}

    @router.get("/adoption-studies")
    def studies() -> dict:
        saved = adoption.catalog()
        if not saved:
            return {"state": "not_ready"}
        return {"state": "ready", "results": saved["experiments"], "sources": saved["sources"],
                "utility_study": adoption.utility_study(), "example_url": saved["profiles"][0]["url"]}

    @router.get("/evidence/recheck")
    def recheck() -> dict:
        try:
            return replay_registered()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(503, "随包的实测材料校验失败。") from exc

    @router.post("/inspect", dependencies=[Depends(access)])
    def inspect_request(request: InspectRequest) -> dict:
        try:
            report = assess(request.identity, request.files, request.inventory, request.inventory_complete,
                            request.task, request.environment.model_dump())
            attach_profile(report["skill_profile"], report)
            return report
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/task/pdf-table", dependencies=[Depends(access)])
    def pdf_table(request: PdfTableRequest) -> dict:
        if not pdf_slots.acquire(blocking=False):
            raise HTTPException(429, "已有 PDF 正在试跑，请稍后重试。")
        try:
            try:
                data = base64.b64decode(request.pdf_base64, validate=True)
            except ValueError as exc:
                raise HTTPException(422, "PDF 编码无法读取。") from exc
            return probe(data, request.page, request.reference_csv, request.headers, request.expected_rows)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        finally:
            pdf_slots.release()

    @router.post("/import-url", dependencies=[Depends(access)])
    def import_url(request: LinkRequest) -> dict:
        if not link_slots.acquire(blocking=False):
            raise HTTPException(429, "已有链接正在读取，请稍后重试。")
        try:
            data = source_import.import_link(request.url, request.skill_path, request.revision)
            if data["state"] == "selection_required":
                return data
            package = data["package"]
            report = assess(package["identity"], package["files"], package["inventory"],
                            package["inventory_complete"], request.task, request.environment.model_dump())
            visible, redacted = source_import.public_package(package)
            source = {**data["source"], "displayed_material_redacted": redacted,
                      "original_bundle_fingerprint": report["bundle_fingerprint"]}
            report["source_import"] = source
            report["package"]["origin"] = {
                "direct_document_only": "只读取得 SKILL.md。完整清单待核对。",
                "verified_local_snapshot": "随包提供的固定提交和 Git blob 已核对。",
            }.get(source.get("source_mode"), "从 GitHub 取得固定提交的文本快照。")
            report["link_assessment"] = {
                "overall_quality_certificate": False, "functional_execution": "not_performed_by_link_import",
            }
            adoption.attach(report, source)
            attach_profile(report["skill_profile"], report, source)
            published_benchmarks.attach(report, source)
            return {"state": "assessed", "source": source, "package": visible, "report": report,
                    "model_api_calls": 0, "skill_code_executed": False}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        finally:
            link_slots.release()

    app.include_router(router)
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})

    return app


app = create_app()
