"""Rebuild the sample photos bundled with the review screen.

Every sample is synthetic and reproducible from its seed.
Run: python scripts/make_samples.py
"""

from __future__ import annotations

import json
from pathlib import Path

from formcheck.generator import make_form, photograph, render

OUT = Path(__file__).resolve().parents[1] / "src" / "formcheck" / "samples"

SAMPLES = [
    # file, title, difficulty, seed, planted error, photo overrides, description
    ("clean-waybill.jpg", "Clean waybill", "easy", 100, "none", {}, "Every line adds up. Nothing should be flagged."),
    ("wrong-total.jpg", "Wrong line total", "easy", 207, "line_total", {},
     "One line's total pieces does not match boxes x pieces per box + loose."),
    ("loose-box.jpg", "Loose pieces make a box", "medium", 300, "loose_overflow", {},
     "Loose pieces add up to a full box or more."),
    ("expired.jpg", "Expired item", "medium", 403, "expired", {}, "An expiry date already past on the document date."),
    ("bad-code.jpg", "Malformed item code", "easy", 500, "bad_code", {}, "An item code that does not fit FAM-XXX-000."),
    ("line-left-out.jpg", "Line left out of total", "medium", 611, "pieces_total", {},
     "The total pieces leaves one line out."),
    ("hard.jpg", "Hard photo", "hard", 700, "line_total", {}, "Strong angle, shadow, blur and heavy JPEG."),
    ("glare-fails.jpg", "Where it fails: glare", "medium", 800, "line_total", {"glare": True},
     "Flash glare washes out part of the table. See the README."),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for file, title, level, seed, err, over, desc in SAMPLES:
        form = make_form(seed, level, force_error=err)
        page, _ = render(form)
        photo, corners = photograph(page, level, seed, **over)
        (OUT / file).write_bytes(photo)
        index.append({"file": file, "title": title, "difficulty": level, "description": desc,
                      "seed": seed, "planted_error": form.error, "doc_type": form.doc_type})
        print(f"{file}: {level}, {form.doc_type}, error {form.error}")
    (OUT / "samples.json").write_text(json.dumps(index, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
