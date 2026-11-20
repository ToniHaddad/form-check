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
    ("clean-easy.jpg", "Clean note", "easy", 30, "none", {}, "Every line adds up. Nothing should be flagged."),
    ("line-total-easy.jpg", "Wrong line total", "easy", 14, "line_total", {}, "One line total does not match quantity times unit price."),
    ("digit-medium.jpg", "Changed digit", "medium", 50, "digit_changed", {}, "One digit on the note was changed."),
    ("skipped-line-medium.jpg", "Line left out of total", "medium", 70, "grand_total", {}, "The grand total leaves one line out."),
    ("comma-medium.jpg", "Decimal commas", "medium", 94, "line_total", {}, "Numbers written as 1.234,50, with one wrong line total."),
    ("hard.jpg", "Hard photo", "hard", 113, "digit_changed", {}, "Strong angle, shadow, blur and heavy JPEG."),
    ("glare-fails.jpg", "Where it fails: glare", "medium", 200, "line_total", {"glare": True},
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
                      "seed": seed, "planted_error": form.error, "style": form.style})
        print(f"{file}: {level}, style {form.style}, error {form.error}")
    (OUT / "samples.json").write_text(json.dumps(index, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
