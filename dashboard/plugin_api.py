"""hermes-templates backend: what the Templates page calls.

GET  /profiles              agents in this home that can be shared
GET  /library               templates already built here (bundle still on disk)
GET  /file/<id>             the bundle itself, as a download
POST /create                pack a profile into a bundle
POST /preview               inspect a link, a path or an uploaded bundle
POST /install               install after a preview

Nothing here reads outside the Hermes home and the user's home directory, and
no route returns a credential: the packer never stores one in the first place.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from agent_templates import api, store  # noqa: E402
from agent_templates.errors import TemplateError  # noqa: E402
from agent_templates.profiles import hermes_home  # noqa: E402

router = APIRouter()

_BODY = Body(default={})


def _home() -> Path:
    return hermes_home()


@router.get("/profiles")
async def profiles():
    return {"profiles": api.profiles(_home())}


@router.get("/library")
async def library():
    return {"templates": api.library(_home())}


@router.get("/file/{template_id}")
async def file(template_id: str):
    try:
        name, payload = api.bundle_bytes(_home(), template_id)
    except TemplateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Content-Length": str(len(payload)),
            "Cache-Control": "no-store",
        },
    )


@router.post("/download/{template_id}")
async def download(template_id: str):
    """The bundle as base64 — the page turns it into a Blob, so no origin juggling."""
    try:
        name, payload = api.bundle_bytes(_home(), template_id)
    except TemplateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    import base64

    return {"filename": name, "bytes": len(payload), "data_base64": base64.b64encode(payload).decode()}


@router.post("/create")
async def create(body: dict = _BODY):
    try:
        return api.create(
            _home(),
            str(body.get("profile") or ""),
            name=str(body.get("name") or ""),
            title=str(body.get("title") or ""),
            description=str(body.get("description") or ""),
            author=str(body.get("author") or ""),
            license=str(body.get("license") or ""),
            version=str(body.get("version") or "1.0.0"),
            skills=bool(body.get("skills", True)),
            skill_names=body.get("skill_names") or None,
            cron=bool(body.get("cron", True)),
            memories=bool(body.get("memories", False)),
            mcp=bool(body.get("mcp", True)),
            plugins=bool(body.get("plugins", True)),
        )
    except TemplateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/preview")
async def preview(body: dict = _BODY):
    try:
        return api.inspect(
            _home(),
            str(body.get("source") or ""),
            str(body.get("data_base64") or ""),
            str(body.get("filename") or ""),
            name=str(body.get("name") or ""),
            restricted=True,
        )
    except TemplateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/install")
async def install(body: dict = _BODY):
    try:
        return api.apply(
            _home(),
            str(body.get("source") or ""),
            str(body.get("data_base64") or ""),
            str(body.get("filename") or ""),
            name=str(body.get("name") or ""),
            force=bool(body.get("force", False)),
            enable_plugins=bool(body.get("enable_plugins", True)),
            restricted=True,
        )
    except TemplateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc