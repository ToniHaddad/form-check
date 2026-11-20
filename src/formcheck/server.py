"""The review screen: one FastAPI app serving one page and a small JSON API."""

from __future__ import annotations

import json
import threading
import uuid
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from .checks import run_checks, summary
from .pipeline import check_bytes
from .table import Note

PKG = Path(__file__).resolve().parent
STATIC = PKG / "static"
SAMPLES = PKG / "samples"
MAX_UPLOAD = 15 * 1024 * 1024

app = FastAPI(title="form-check", docs_url=None, redoc_url=None)

_lock = threading.Lock()
_sample_cache: dict[str, dict] = {}
_uploads: OrderedDict[str, tuple[bytes, str]] = OrderedDict()


def _samples() -> list[dict]:
    index = SAMPLES / "samples.json"
    return json.loads(index.read_text(encoding="utf-8")) if index.exists() else []


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/samples")
def samples() -> list[dict]:
    return _samples()


@app.get("/samples/{name}")
def sample_file(name: str):
    names = {s["file"] for s in _samples()}
    if name not in names:
        raise HTTPException(404)
    return FileResponse(SAMPLES / name, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/image/{key}")
def uploaded_image(key: str):
    item = _uploads.get(key)
    if not item:
        raise HTTPException(404)
    return Response(item[0], media_type=item[1])


@app.post("/api/check")
async def check(file: UploadFile | None = File(None), sample: str | None = Form(None)) -> dict:
    if sample:
        names = {s["file"] for s in _samples()}
        if sample not in names:
            raise HTTPException(404, "unknown sample")
        with _lock:
            cached = _sample_cache.get(sample)
        if cached is None:
            cached = check_bytes((SAMPLES / sample).read_bytes())
            with _lock:
                _sample_cache[sample] = cached
        return {**cached, "image_url": f"/samples/{sample}"}
    if file is None:
        raise HTTPException(400, "send a photo or pick a sample")
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "photo is larger than 15 MB")
    try:
        res = check_bytes(data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    key = uuid.uuid4().hex
    with _lock:
        _uploads[key] = (data, file.content_type or "image/jpeg")
        while len(_uploads) > 20:
            _uploads.popitem(last=False)
    return {**res, "image_url": f"/api/image/{key}"}


class RecheckIn(BaseModel):
    note: dict


@app.post("/api/recheck")
def recheck(body: RecheckIn) -> dict:
    note = Note.from_dict(body.note)
    flags = run_checks(note)
    return {"flags": [f.to_dict() for f in flags], "summary": summary(flags)}
