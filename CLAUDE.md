# form-check

Reads a photo of a warehouse waybill (`DSP-`) or goods received note (`RCV-`)
with a local OCR model (RapidOCR, ONNX, CPU), rebuilds the table, and checks
boxes x pieces per box + loose = total, loose under a box, expiry against the
document date, item code shape (`FOO-RIC-001`) and the three totals. Each
failing spot is outlined on the photo. All data is synthetic; the organization
is unnamed ("the Organization"). The repo is public.

## Run and test

```
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # bin/ on Linux
formcheck serve                 # review screen on http://127.0.0.1:8103
formcheck check photo.jpg       # exit 1 when a check fails or a figure is unreadable
formcheck bench --per-level 25 --keep data/bench   # docs/benchmark.json and .txt (20-30 min on CPU);
                                # --keep saves the OCR so a rerun only rescores
python scripts/make_samples.py  # rebuilds the bundled sample photos
pytest -q
```

## Layout

- `src/formcheck/generator.py` synthetic documents, planted errors, simulated photo
- `src/formcheck/ocr.py` find the sheet, flatten it, OCR to word boxes (`OcrPage`, JSON-able)
- `src/formcheck/table.py` word boxes to header, rows, columns, totals (`Note`)
- `src/formcheck/numbers.py` number and count parsing
- `src/formcheck/checks.py` the checks and flags with photo outlines
- `src/formcheck/server.py` + `static/index.html` the review screen (no build step, no CDN)
- `tests/fixtures/*.ocr.json` recorded OCR output so table and check tests run without the model

## Quirks

- Only one test runs the OCR model (`test_end_to_end.py`); keep it that way so CI stays fast.
- Tune rules on the fixtures and samples (seeds below 1000), never on benchmark seeds (5000+).
- Fonts: Pillow's bundled default font so rendering is identical on Windows and Linux.
- Port 8103 only.
