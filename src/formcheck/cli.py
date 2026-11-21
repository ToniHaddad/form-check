"""Command line: formcheck check | generate | bench | serve."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _print_note(res: dict, out=sys.stdout) -> None:
    note = res["note"]
    h = {k: (v or {}).get("text", "") for k, v in note["header"].items()}
    w = out.write
    kind = {"waybill": "Waybill (dispatch)", "grn": "Goods received note"}.get(note.get("doc_type"), "Document")
    party = "Destination" if note.get("doc_type") == "waybill" else "From"
    w(f"{kind:<22}{h.get('doc_no', '?')}    Date {h.get('date', '?')}\n")
    w(f"Warehouse             {h.get('warehouse', '?')}    Project {h.get('project', '?')}\n")
    w(f"{party:<22}{h.get('party', '?')}\n")
    w(f"Keeper                {h.get('keeper', '?')}\n\n")
    cols = [("no", "No", 3, ">"), ("code", "Item code", 12, "<"), ("item", "Item", 22, "<"),
            ("ppb", "Per box", 7, ">"), ("boxes", "Boxes", 6, ">"), ("loose", "Loose", 6, ">"),
            ("total", "Total", 7, ">"), ("expiry", "Expiry", 10, ">")]
    w("  ".join(f"{t[:width]:{al}{width}}" for _, t, width, al in cols) + "\n")
    w("-" * (sum(c[2] for c in cols) + 2 * (len(cols) - 1)) + "\n")
    for row in note["rows"]:
        cells = []
        for key, _, width, al in cols:
            c = row.get(key)
            t = c["text"] if c else ("-" if key == "expiry" else "?")
            cells.append(f"{t[:width]:{al}{width}}")
        w("  ".join(cells) + "\n")
    t = {k: (note["totals"].get(k) or {}).get("text", "?") for k in ("lines", "boxes", "pieces")}
    w(f"\nNumber of lines {t['lines']}    Total boxes {t['boxes']}    Total pieces {t['pieces']}\n\n")
    flags = res["flags"]
    if not flags:
        w("All checks pass: every line adds up, no loose pieces make a full box, nothing has expired,\n"
          "every item code has the right shape, and the totals match the lines.\n")
        return
    w(f"{len(flags)} flag{'s' if len(flags) != 1 else ''}:\n")
    for f in flags:
        w(f"  [{f['n']}] {f['message']}\n")
        if f.get("hint"):
            w(f"      {f['hint']}\n")


def cmd_check(args) -> int:
    from .ocr import ocr_photo
    from .pipeline import analyse

    data = Path(args.image).read_bytes()
    page = ocr_photo(data)
    if args.save_ocr:
        Path(args.save_ocr).write_text(page.to_json(), encoding="utf-8")
    res = analyse(page)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        _print_note(res)
    return 0 if res["summary"]["ok"] else 1


def cmd_generate(args) -> int:
    from .generator import DIFFICULTIES, generate

    levels = DIFFICULTIES if args.difficulty == "all" else (args.difficulty,)
    for i, level in enumerate(levels):
        paths = generate(Path(args.out), args.count, level, start_seed=args.seed + 1000 * i,
                         error_rate=args.error_rate)
        print(f"{level}: wrote {len(paths)} photos to {args.out}")
    return 0


def cmd_bench(args) -> int:
    from .bench import main as bench_main

    return bench_main(per_level=args.per_level, seed=args.seed, out=args.out, keep=args.keep)


def cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("formcheck.server:app", host=args.host, port=args.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    p = argparse.ArgumentParser(prog="formcheck", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="read a photo of a waybill or goods received note and check it")
    c.add_argument("image")
    c.add_argument("--json", action="store_true", help="print the full result as JSON")
    c.add_argument("--save-ocr", metavar="PATH", help="save the raw OCR boxes (used as test fixtures)")
    c.set_defaults(func=cmd_check)

    g = sub.add_parser("generate", help="write synthetic document photos with ground truth")
    g.add_argument("--out", default="data/generated")
    g.add_argument("--count", type=int, default=10)
    g.add_argument("--difficulty", choices=["easy", "medium", "hard", "all"], default="all")
    g.add_argument("--seed", type=int, default=1000)
    g.add_argument("--error-rate", type=float, default=0.5)
    g.set_defaults(func=cmd_generate)

    b = sub.add_parser("bench", help="measure extraction accuracy and error catching")
    b.add_argument("--per-level", type=int, default=25)
    b.add_argument("--seed", type=int, default=5000)
    b.add_argument("--out", default="docs/benchmark.json")
    b.add_argument("--keep", metavar="DIR", help="also save the generated photos here")
    b.set_defaults(func=cmd_bench)

    s = sub.add_parser("serve", help="start the review screen")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8103)
    s.set_defaults(func=cmd_serve)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
