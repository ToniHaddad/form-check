# form-check

Reads a photo of a delivery note with a local OCR model (RapidOCR, ONNX, CPU),
rebuilds the table, checks quantity x unit value = line total and both column
totals, and shows each failing spot outlined on the photo. All data is
synthetic (fictional NGO "Northbridge Relief").

## Run and test

```
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # bin/ on Linux
formcheck serve                 # review screen on http://127.0.0.1:8103
formcheck check photo.jpg       # exit 1 when a check fails or a number is unreadable
formcheck bench --per-level 30  # writes docs/benchmark.json and .txt (about 5 min)
python scripts/make_samples.py  # rebuilds the bundled sample photos
pytest -q
```

## Layout

- `src/formcheck/generator.py` synthetic notes, planted errors, simulated photo
- `src/formcheck/ocr.py` find the sheet, flatten it, OCR to word boxes (`OcrPage`, JSON-able)
- `src/formcheck/table.py` word boxes to header, rows, columns, totals (`Note`)
- `src/formcheck/numbers.py` number parsing, decimal comma vs dot per page
- `src/formcheck/checks.py` arithmetic checks and flags with photo outlines
- `src/formcheck/server.py` + `static/index.html` the review screen (no build step, no CDN)
- `tests/fixtures/*.ocr.json` recorded OCR output so table and check tests run without the model

## Quirks

- Only one test runs the OCR model (`test_end_to_end.py`); keep it that way so CI stays fast.
- Fonts: Pillow's bundled default font (`ImageFont.load_default(size=...)`, Pillow >= 10.1) so
  rendering is identical on Windows and Linux.
- Port 8103 only.
