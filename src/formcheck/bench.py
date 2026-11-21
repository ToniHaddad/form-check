"""Benchmark: generate photos, run the full pipeline, compare with ground truth.

Reports per difficulty level:

* field-level extraction accuracy (header fields, item codes, names, counts,
  expiry dates, totals)
* item codes read right, raw and after the pattern-aware clean-up
* planted errors caught at the right place, marked unreadable there, flagged
  only elsewhere, or missed
* flags raised on clean documents
* silent misreads: a count read wrongly whose line and totals still pass every
  check, and an item code misread into another valid-looking code
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from .checks import CODE_RE, normalise_code, parse_date, run_checks
from .generator import DIFFICULTIES, ERROR_TYPES, make_form, photograph, render
from .numbers import parse_count
from .ocr import OcrPage, ocr_photo
from .table import rebuild

GROUPS = ("header", "item codes", "text cells", "count cells", "expiry dates", "totals")
ROW_KIND = {"line_total": ("line",), "digit_changed": ("line", "loose"), "loose_overflow": ("loose",),
            "expired": ("expired",), "bad_code": ("code",)}
TOTAL_KIND = {"pieces_total": ("pieces_sum", "pieces"), "boxes_total": ("boxes_sum", "boxes"),
              "line_count": ("lines_count", "lines")}


def _squash(s: str) -> str:
    """Compare text ignoring case and spaces: the OCR model often drops spaces."""
    return re.sub(r"\s+", "", s).lower()


def plan(per_level: int) -> list[str]:
    """Half the documents carry a planted error, cycling through the eight kinds."""
    out, j = [], 0
    for k in range(per_level):
        if k % 2 == 0:
            out.append(ERROR_TYPES[j % len(ERROR_TYPES)])
            j += 1
        else:
            out.append("none")
    return out


def score_form(truth, note, flags) -> dict:
    r = defaultdict(lambda: [0, 0])  # group -> [correct, total]
    misreads = []  # (cell, row, read, printed)
    code_raw = [0, 0]

    for key, val in truth.header.items():
        cell = note.header.get(key)
        r["header"][1] += 1
        r["header"][0] += bool(cell and _squash(cell.text) == _squash(val))

    by_no = {}
    if len(note.rows) == len(truth.rows):
        by_no = {t.no: i for i, t in enumerate(truth.rows)}
    else:
        for i, row in enumerate(note.rows):
            c = row.get("no")
            if c and c.text.strip().isdigit():
                by_no.setdefault(int(c.text.strip()), i)
    silent_codes = []
    for idx, trow in enumerate(truth.rows):
        i = by_no.get(trow.no, idx if idx < len(note.rows) else None)
        row = note.rows[i] if i is not None and i < len(note.rows) else {}
        n = (i or 0) + 1
        c = row.get("code")
        r["item codes"][1] += 1
        code_raw[1] += 1
        if c:
            code_raw[0] += c.text.strip() == trow.code
            norm = normalise_code(c.text)
            if norm == trow.code:
                r["item codes"][0] += 1
            elif CODE_RE.match(norm):
                silent_codes.append({"cell": f"row{n}.code", "read": c.text, "printed": trow.code})
        for key in ("item", "unit"):
            r["text cells"][1] += 1
            c = row.get(key)
            r["text cells"][0] += bool(c and _squash(c.text) == _squash(getattr(trow, key)))
        for key in ("ppb", "boxes", "loose", "total"):
            r["count cells"][1] += 1
            c = row.get(key)
            v = parse_count(c.text) if c else None
            if v is not None and v == getattr(trow, key):
                r["count cells"][0] += 1
            elif v is not None:
                misreads.append((f"row{n}.{key}", n, c.text, getattr(trow, key)))
        if trow.expiry:
            r["expiry dates"][1] += 1
            c = row.get("expiry")
            r["expiry dates"][0] += bool(c and parse_date(c.text) == trow.expiry)
    for key, val in (("lines", truth.lines), ("boxes", truth.total_boxes), ("pieces", truth.total_pieces)):
        r["totals"][1] += 1
        c = note.totals.get(key)
        v = parse_count(c.text) if c else None
        if v is not None and v == val:
            r["totals"][0] += 1
        elif v is not None:
            misreads.append((f"totals.{key}", None, c.text, val))
    r["rows found"] = [int(len(note.rows) == len(truth.rows)), 1]

    flagged_cells = {c for f in flags for c in f.cells}
    flagged_rows = {f.row for f in flags if f.row}
    silent = [{"cell": loc, "read": read, "printed": true} for loc, row, read, true in misreads
              if not (loc in flagged_cells or (row and row in flagged_rows))]
    silent_codes = [s for s in silent_codes if s["cell"] not in flagged_cells]

    planted = truth.error
    outcome = None
    if planted:
        t = planted["type"]
        if t in TOTAL_KIND:
            kind, key = TOTAL_KIND[t]
            right = any(f.kind == kind for f in flags)
            unread = any(f.kind == "unreadable" and f"totals.{key}" in f.cells for f in flags)
        else:
            row = by_no.get(planted["row"], planted["row"] - 1) + 1
            right = any(f.kind in ROW_KIND[t] and f.row == row for f in flags)
            unread = any(f.kind == "unreadable" and f.row == row for f in flags)
        outcome = ("caught" if right else "unreadable there" if unread
                   else "flagged elsewhere" if flags else "missed")
    return {"fields": dict(r), "code_raw": code_raw, "silent": silent, "silent_codes": silent_codes,
            "misreads": len(misreads), "planted": planted["type"] if planted else None, "outcome": outcome,
            "any_flag": bool(flags), "error_flag": any(f.severity == "error" for f in flags)}


def main(per_level: int = 25, seed: int = 5000, out: str | None = "docs/benchmark.json", keep: str | None = None) -> int:
    report, lines, details = {}, [], []
    p = plan(per_level)
    for li, level in enumerate(DIFFICULTIES):
        agg = defaultdict(lambda: [0, 0])
        stats = defaultdict(int)
        by_type = defaultdict(lambda: defaultdict(int))
        secs = 0.0
        for k in range(per_level):
            s = seed + 1000 * li + k
            form = make_form(s, level, force_error=p[k])
            cached = Path(keep) / f"{form.form_id}.ocr.json" if keep else None
            if cached and cached.exists():
                page = OcrPage.load(cached)  # a rerun after a scoring change reuses the saved OCR
            else:
                page_img, _ = render(form)
                photo, _ = photograph(page_img, level, s)
                page = ocr_photo(photo)
                if keep:
                    Path(keep).mkdir(parents=True, exist_ok=True)
                    (Path(keep) / f"{form.form_id}.jpg").write_bytes(photo)
                    cached.write_text(page.to_json(), encoding="utf-8")
            note = rebuild(page)
            flags = run_checks(note)
            # time spent finding the sheet and reading it, as measured when the OCR ran
            secs += page.timings.get("page_s", 0) + page.timings.get("ocr_s", 0)
            sc = score_form(form, note, flags)
            for g, (a, b) in sc["fields"].items():
                agg[g][0] += a
                agg[g][1] += b
            agg["item codes, raw OCR"][0] += sc["code_raw"][0]
            agg["item codes, raw OCR"][1] += sc["code_raw"][1]
            stats["silent_misreads"] += len(sc["silent"])
            stats["silent_codes"] += len(sc["silent_codes"])
            stats["misreads"] += sc["misreads"]
            if sc["planted"]:
                stats["planted"] += 1
                stats[sc["outcome"]] += 1
                by_type[sc["planted"]][sc["outcome"]] += 1
            else:
                stats["clean"] += 1
                stats["clean_flagged"] += sc["any_flag"]
                stats["clean_error_flag"] += sc["error_flag"]
            details.append({"form": form.form_id, "planted": form.error, "outcome": sc["outcome"],
                            "flags": [f.message for f in flags], "silent_misreads": sc["silent"],
                            "silent_codes": sc["silent_codes"]})
        all_c = sum(agg[g][0] for g in GROUPS)
        all_t = sum(agg[g][1] for g in GROUPS)
        report[level] = {
            "forms": per_level,
            "seconds_per_form": round(secs / per_level, 2),
            "field_accuracy": {g: {"correct": a, "total": b, "pct": round(100 * a / b, 1) if b else None}
                               for g, (a, b) in agg.items()},
            "all_fields_pct": round(100 * all_c / all_t, 1),
            "planted_by_type": {t: dict(v) for t, v in by_type.items()},
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
        Path(out).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        Path(out).with_suffix(".txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


def _pct(a, b) -> str:
    return f"{100 * a / b:5.1f}%" if b else "  n/a"


def _format_level(level: str, r: dict) -> str:
    fa = r["field_accuracy"]
    out = [f"== {level} ({r['forms']} documents, {r['seconds_per_form']} s each) =="]
    for g in ("header", "item codes, raw OCR", "item codes", "text cells", "count cells", "expiry dates",
              "totals", "rows found"):
        a = fa[g]
        name = "item codes, cleaned" if g == "item codes" else g
        out.append(f"  {name:<20} {a['correct']:>4}/{a['total']:<4} {_pct(a['correct'], a['total'])}")
    out.append(f"  all fields           {r['all_fields_pct']:5.1f}%")
    out.append(f"  planted errors {r.get('planted', 0)}: caught by a check at the right spot {r.get('caught', 0)}, "
               f"marked unreadable there {r.get('unreadable there', 0)}, "
               f"only other flags {r.get('flagged elsewhere', 0)}, nothing flagged {r.get('missed', 0)}")
    out.append(f"  clean documents {r.get('clean', 0)}: any flag on {r.get('clean_flagged', 0)} "
               f"(error flag on {r.get('clean_error_flag', 0)})")
    out.append(f"  counts misread {r.get('misreads', 0)}, of which slipped through every check "
               f"{r.get('silent_misreads', 0)}; item codes misread into another valid code "
               f"{r.get('silent_codes', 0)}")
    return "\n".join(out)


def _overall(report: dict) -> dict:
    keys = ["planted", "caught", "unreadable there", "flagged elsewhere", "missed", "clean", "clean_flagged",
            "clean_error_flag", "misreads", "silent_misreads", "silent_codes", "forms"]
    return {k: sum(report[lv].get(k, 0) for lv in DIFFICULTIES) for k in keys}


def _format_overall(t: dict) -> str:
    return (f"== overall ({t['forms']} documents) ==\n"
            f"  planted errors {t['planted']}: caught {t['caught']}, unreadable there {t['unreadable there']}, "
            f"only other flags {t['flagged elsewhere']}, nothing flagged {t['missed']}\n"
            f"  clean documents {t['clean']}: any flag {t['clean_flagged']}, error flag {t['clean_error_flag']}\n"
            f"  counts misread that passed every check: {t['silent_misreads']} of {t['misreads']}; "
            f"item codes misread into another valid code: {t['silent_codes']}")
