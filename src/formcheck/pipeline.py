"""Photo in, checked note out."""

from __future__ import annotations

from pathlib import Path

from .checks import run_checks, summary
from .ocr import OcrPage, ocr_photo
from .table import Note, rebuild

LOW_CONF = 0.90  # cells read with less confidence than this are tinted for review


def analyse(page: OcrPage) -> dict:
    note = rebuild(page)
    return result(note, page)


def result(note: Note, page: OcrPage | None = None) -> dict:
    flags = run_checks(note)
    out = {
        "note": note.to_dict(),
        "flags": [f.to_dict() for f in flags],
        "summary": summary(flags),
        "low_conf": LOW_CONF,
    }
    if page is not None:
        out["photo_size"] = list(page.photo_size)
        out["page_corners"] = page.page_corners
        out["timings"] = page.timings
    return out


def check_bytes(data: bytes) -> dict:
    return analyse(ocr_photo(data))


def check_file(path: str | Path) -> dict:
    return check_bytes(Path(path).read_bytes())
