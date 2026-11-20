"""The review screen's API, without running OCR: edits re-run the checks."""

from pathlib import Path

from fastapi.testclient import TestClient

from formcheck.ocr import OcrPage
from formcheck.pipeline import analyse
from formcheck.server import app

FIX = Path(__file__).parent / "fixtures"
client = TestClient(app)


def test_page_and_samples_are_served():
    r = client.get("/")
    assert r.status_code == 200 and "form-check" in r.text
    samples = client.get("/api/samples").json()
    assert len(samples) >= 5
    assert client.get(f"/samples/{samples[0]['file']}").status_code == 200
    assert client.get("/samples/../server.py").status_code == 404


def test_correcting_a_cell_clears_the_flag():
    res = analyse(OcrPage.load(FIX / "easy-5000.ocr.json"))
    assert [f["kind"] for f in res["flags"]] == ["line"]
    note = res["note"]
    # The reviewer looks at the photo: line 2 really says 27,80, so the error is
    # on the paper. Typing the right total (28,80) makes the line add up and
    # moves the problem to the grand total, which was added up from the wrong line.
    note["rows"][1]["line_total"]["text"] = "28,80"
    r = client.post("/api/recheck", json={"note": note}).json()
    assert [f["kind"] for f in r["flags"]] == ["total_sum"]
    assert not r["summary"]["ok"]


def test_typing_an_unreadable_value_runs_the_check():
    res = analyse(OcrPage.load(FIX / "easy-5003.ocr.json"))
    note = res["note"]
    note["rows"][0]["qty"]["text"] = ""
    r = client.post("/api/recheck", json={"note": note}).json()
    assert [f["kind"] for f in r["flags"]] == ["unreadable"]
    note["rows"][0]["qty"]["text"] = "19"
    r = client.post("/api/recheck", json={"note": note}).json()
    assert r["flags"] == [] and r["summary"]["ok"]
