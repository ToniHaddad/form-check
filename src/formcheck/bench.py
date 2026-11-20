"""Benchmark: generate photos, run the full pipeline, compare with ground truth.

Reports per difficulty level:

* field-level extraction accuracy (header fields, table cells, totals)
* planted errors caught at the right place, flagged elsewhere, or missed
* false alarms on clean notes
* silent misreads: a number read wrongly whose line and totals still pass the
  checks, so nothing points the reviewer at it
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .checks import run_checks
from .generator import DIFFICULTIES, ERROR_TYPES, make_form, photograph, render
from .numbers import parse_number
from .ocr import ocr_photo
from .table import rebuild


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def plan(per_level: int) -> list[str]:
    """Exactly 40% of forms carry a planted error, cycling through the types."""
    out = []
    for k in range(per_level):
        out.append(ERROR_TYPES[(k // 5 * 2 + k % 5) % 3] if k % 5 < 2 else "none")
    return out


def score_form(truth, note, flags) -> dict:
    style = note.style
    r = defaultdict(lambda: [0, 0])  # field group -> [correct, total]
    misreads = []  # (location, row) of numeric cells read wrongly

    for key, val in truth.header.items():
        cell = note.header.get(key)
        r["header"][1] += 1
        if cell and _squash(cell.text) == _squash(val):
            r["header"][0] += 1

    # align rows by their printed number, falling back to order
    by_no = {}
    for i, row in enumerate(note.rows):
        c = row.get("no")
        if c and c.text.strip().isdigit():
            by_no.setdefault(int(c.text.strip()), i)
    for idx, trow in enumerate(truth.rows):
        i = by_no.get(trow.no, idx if idx < len(note.rows) else None)
        row = note.rows[i] if i is not None and i < len(note.rows) else {}
        for key in ("item", "unit"):
            r["text cells"][1] += 1
            c = row.get(key)
            if c and _squash(c.text) == _squash(getattr(trow, key)):
                r["text cells"][0] += 1
        for key in ("qty", "unit_value", "line_total"):
            r["number cells"][1] += 1
            c = row.get(key)
            v = parse_number(c.text, style) if c else None
            if v is not None and v == getattr(trow, key):
                r["number cells"][0] += 1
            elif v is not None:
                misreads.append((f"row{(i or 0) + 1}.{key}", (i or 0) + 1, c.text, str(getattr(trow, key))))
    for key, truth_val in (("total_qty", truth.total_qty), ("grand_total", truth.grand_total)):
        r["totals"][1] += 1
        c = getattr(note, key)
        v = parse_number(c.text, style) if c else None
        if v is not None and v == truth_val:
            r["totals"][0] += 1
        elif v is not None:
            misreads.append((key, None, c.text, str(truth_val)))
    r["rows found"] = [int(len(note.rows) == len(truth.rows)), 1]

    error_flags = [f for f in flags if f.severity == "error"]
    flagged_cells = {c for f in flags for c in f.cells}
    flagged_rows = {f.row for f in flags if f.row}
    silent = []
    for loc, row, read, true in misreads:
        if loc in flagged_cells or (row and row in flagged_rows):
            continue  # a flag on that line or total points the reviewer at it
        silent.append({"cell": loc, "read": read, "printed": true})

    planted = truth.error
    outcome = None
    if planted:
        if planted["field"] == "grand_total":
            right = any(f.kind == "total_sum" for f in error_flags)
            unread = any(f.kind == "unreadable" and "grand_total" in f.cells for f in flags)
        else:
            want = planted["row"]
            row = by_no.get(want, want - 1) + 1
            right = any(f.kind == "line" and f.row == row for f in flags) or (
                planted["field"] == "qty" and any(f.kind == "qty_sum" for f in error_flags))
            unread = any(f.kind == "unreadable" and f.row == row for f in flags)
        if right:
            outcome = "caught"
        elif unread:
            outcome = "unreadable there"
        elif flags:
            outcome = "flagged elsewhere"
        else:
            outcome = "missed"
    return {"fields": dict(r), "silent_misreads": len(silent), "silent": silent, "misreads": len(misreads),
            "planted": planted["type"] if planted else None, "outcome": outcome,
            "any_flag": bool(flags), "error_flag": bool(error_flags)}


def main(per_level: int = 30, seed: int = 5000, out: str | None = "docs/benchmark.json", keep: str | None = None) -> int:
    report = {}
    lines = []
    details = []
    p = plan(per_level)
    for li, level in enumerate(DIFFICULTIES):
        agg = defaultdict(lambda: [0, 0])
        stats = defaultdict(int)
        secs = 0.0
        for k in range(per_level):
            s = seed + 1000 * li + k
            form = make_form(s, level, force_error=p[k])
            page_img, _ = render(form)
            photo, _ = photograph(page_img, level, s)
            if keep:
                Path(keep).mkdir(parents=True, exist_ok=True)
                (Path(keep) / f"{form.form_id}.jpg").write_bytes(photo)
            t0 = time.perf_counter()
            page = ocr_photo(photo)
            note = rebuild(page)
            flags = run_checks(note)
            secs += time.perf_counter() - t0
            sc = score_form(form, note, flags)
            if keep:
                (Path(keep) / f"{form.form_id}.ocr.json").write_text(page.to_json(), encoding="utf-8")
            details.append({"form": form.form_id, "planted": form.error, "outcome": sc["outcome"],
                            "flags": [f.message for f in flags], "silent_misreads": sc["silent"]})
            for g, (a, b) in sc["fields"].items():
                agg[g][0] += a
                agg[g][1] += b
            stats["silent_misreads"] += sc["silent_misreads"]
            stats["misreads"] += sc["misreads"]
            if sc["planted"]:
                stats["planted"] += 1
                stats[sc["outcome"]] += 1
            else:
                stats["clean"] += 1
                stats["clean_flagged"] += sc["any_flag"]
                stats["clean_error_flag"] += sc["error_flag"]
        all_c = sum(agg[g][0] for g in ("header", "text cells", "number cells", "totals"))
        all_t = sum(agg[g][1] for g in ("header", "text cells", "number cells", "totals"))
        report[level] = {
            "forms": per_level,
            "seconds_per_form": round(secs / per_level, 2),
            "field_accuracy": {g: {"correct": a, "total": b, "pct": round(100 * a / b, 1)} for g, (a, b) in agg.items()},
            "all_fields_pct": round(100 * all_c / all_t, 1),
            **dict(stats),
        }
        lines.append(_format_level(level, report[level]))
        print(lines[-1], flush=True)
    totals = _overall(report)
    report["overall"] = totals
    report["forms"] = details
    lines.append(_format_overall(totals))
    print(lines[-1])
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        Path(out).with_suffix(".txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


def _pct(a, b) -> str:
    return f"{100 * a / b:5.1f}%" if b else "  n/a"


def _format_level(level: str, r: dict) -> str:
    fa = r["field_accuracy"]
    out = [f"== {level} ({r['forms']} forms, {r['seconds_per_form']} s/form) =="]
    for g in ("header", "text cells", "number cells", "totals", "rows found"):
        a = fa[g]
        out.append(f"  {g:<14} {a['correct']:>4}/{a['total']:<4} {_pct(a['correct'], a['total'])}")
    out.append(f"  all fields     {r['all_fields_pct']:5.1f}%")
    pl = r.get("planted", 0)
    out.append(f"  planted errors {pl}: caught by a check at the right spot {r.get('caught', 0)}, "
               f"marked unreadable at that spot {r.get('unreadable there', 0)}, "
               f"only other flags {r.get('flagged elsewhere', 0)}, nothing flagged {r.get('missed', 0)}")
    out.append(f"  clean notes {r.get('clean', 0)}: any flag raised on {r.get('clean_flagged', 0)} "
               f"(arithmetic error flag on {r.get('clean_error_flag', 0)})")
    out.append(f"  numbers misread {r.get('misreads', 0)}, of which slipped through every check "
               f"{r.get('silent_misreads', 0)}")
    return "\n".join(out)


def _overall(report: dict) -> dict:
    keys = ["planted", "caught", "unreadable there", "flagged elsewhere", "missed", "clean", "clean_flagged",
            "clean_error_flag", "misreads", "silent_misreads", "forms"]
    return {k: sum(report[lv].get(k, 0) for lv in DIFFICULTIES) for k in keys}


def _format_overall(t: dict) -> str:
    return (f"== overall ({t['forms']} forms) ==\n"
            f"  planted errors {t['planted']}: caught {t['caught']}, unreadable there {t['unreadable there']}, "
            f"only other flags {t['flagged elsewhere']}, nothing flagged {t['missed']}\n"
            f"  clean notes {t['clean']}: any flag {t['clean_flagged']}, "
            f"arithmetic error flag {t['clean_error_flag']}\n"
            f"  misread numbers that passed every check: {t['silent_misreads']} of {t['misreads']} misreads")


def _squash(s: str) -> str:
    """Compare text ignoring case and spaces: the OCR model often drops spaces."""
    return re.sub(r"\s+", "", s).lower()
