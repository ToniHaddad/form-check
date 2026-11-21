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


def test_correcting_a_cell_moves_the_flag():
    res = analyse(OcrPage.load(FIX / "easy-3.ocr.json"))
    assert [f["kind"] for f in res["flags"]] == ["line"]
    note = res["note"]
    # The reviewer checks the photo: line 8 really says 604, so the mistake is
    # on the paper. Typing the right total (624) makes the line add up and moves
    # the problem to the total pieces, which was added up from the wrong line.
    note["rows"][7]["total"]["text"] = "624"
    r = client.post("/api/recheck", json={"note": note}).json()
    assert [f["kind"] for f in r["flags"]] == ["pieces_sum"]
    assert not r["summary"]["ok"]


def test_typing_an_unreadable_value_runs_the_check():
    res = analyse(OcrPage.load(FIX / "easy-3.ocr.json"))
    note = res["note"]
    note["rows"][7]["total"]["text"] = "624"
    note["totals"]["pieces"]["text"] = "9,644"
    note["rows"][0]["boxes"]["text"] = ""
    r = client.post("/api/recheck", json={"note": note}).json()
    assert [f["kind"] for f in r["flags"]] == ["unreadable"]
    note["rows"][0]["boxes"]["text"] = "15"
    r = client.post("/api/recheck", json={"note": note}).json()
    assert r["flags"] == [] and r["summary"]["ok"]
