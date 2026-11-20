"""Find the sheet in the photo, flatten it, and read it with a local OCR model.

The OCR engine is RapidOCR (PaddleOCR models exported to ONNX), which runs on
CPU and installs with pip. Nothing leaves the machine.

The output of this module is an ``OcrPage``: every word box in flattened
page coordinates plus the homography back to the photo. It is plain data and
can be saved as JSON, which is how the tests replay OCR without the model.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

FLAT_W = 1240


@dataclass
class Word:
    text: str
    conf: float
    poly: list[list[float]]  # 4 points, flattened-page coordinates

    @property
    def x0(self) -> float:
        return min(p[0] for p in self.poly)

    @property
    def x1(self) -> float:
        return max(p[0] for p in self.poly)

    @property
    def y0(self) -> float:
        return min(p[1] for p in self.poly)

    @property
    def y1(self) -> float:
        return max(p[1] for p in self.poly)

    @property
    def cx(self) -> float:
        return sum(p[0] for p in self.poly) / 4

    @property
    def cy(self) -> float:
        return sum(p[1] for p in self.poly) / 4

    @property
    def height(self) -> float:
        (a, b, c, d) = self.poly
        return (np.hypot(d[0] - a[0], d[1] - a[1]) + np.hypot(c[0] - b[0], c[1] - b[1])) / 2


@dataclass
class OcrPage:
    words: list[Word]
    flat_size: tuple[int, int]
    photo_size: tuple[int, int]
    to_photo: list[list[float]]  # 3x3 homography, flat -> photo
    page_corners: list[list[float]] | None = None  # in photo, None if the sheet was not found
    timings: dict = field(default_factory=dict)

    def poly_to_photo(self, poly: list[list[float]]) -> list[list[float]]:
        pts = np.array(poly, dtype=np.float64).reshape(-1, 1, 2)
        out = cv2.perspectiveTransform(pts, np.array(self.to_photo, dtype=np.float64))
        return out.reshape(-1, 2).round(1).tolist()

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d)

    @classmethod
    def from_json(cls, text: str) -> "OcrPage":
        d = json.loads(text)
        d["words"] = [Word(**w) for w in d["words"]]
        d["flat_size"] = tuple(d["flat_size"])
        d["photo_size"] = tuple(d["photo_size"])
        return cls(**d)

    @classmethod
    def load(cls, path: Path) -> "OcrPage":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- page

def _corners_from_mask(mask: np.ndarray) -> np.ndarray | None:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    hull = cv2.convexHull(c).reshape(-1, 2).astype(np.float32)
    approx = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True).reshape(-1, 2)
    pts = approx.astype(np.float64) if len(approx) == 4 else hull.astype(np.float64)
    s = pts.sum(axis=1)
    d = pts[:, 0] - pts[:, 1]
    return np.array([pts[np.argmin(s)], pts[np.argmax(d)], pts[np.argmax(s)], pts[np.argmin(d)]])


def _plausible(q: np.ndarray, shape: tuple[int, int]) -> bool:
    """A sheet: large, convex, corners near right angles, a paper-like aspect."""
    if q is None or len({tuple(p) for p in q.round(1)}) < 4:
        return False
    area = cv2.contourArea(q.astype(np.float32))
    if area < 0.2 * shape[0] * shape[1] or not cv2.isContourConvex(q.astype(np.float32)):
        return False
    for i in range(4):
        a, b, c = q[i - 1], q[i], q[(i + 1) % 4]
        v1, v2 = a - b, c - b
        cos = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9)
        if abs(cos) > 0.42:  # outside roughly 65 to 115 degrees
            return False
    w = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    h = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    return 0.9 < h / max(w, 1) < 2.0


def find_page(img: np.ndarray) -> np.ndarray | None:
    """Return the four corners of the sheet (tl, tr, br, bl) or None.

    Tries a brightness mask first (paper is lighter and less coloured than the
    desk), then an edge map, and keeps the first outline that looks like a sheet.
    """
    h, w = img.shape[:2]
    scale = 800 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    v = cv2.GaussianBlur(hsv[..., 2], (5, 5), 0)
    sat = hsv[..., 1]
    t, _ = cv2.threshold(v, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = np.ones((15, 15), np.uint8)

    def clean(m):
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel)
        return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))

    def candidate(m):
        q = _corners_from_mask(m)
        if q is None or not _plausible(q, small.shape[:2]):
            return None
        border = 0.01 * max(small.shape)
        if (q[:, 0].min() < border or q[:, 1].min() < border or q[:, 0].max() > small.shape[1] - border
                or q[:, 1].max() > small.shape[0] - border):
            return None  # the outline runs along the photo edge: that is the frame, not the sheet
        return q

    # Candidate outlines: 1. paper is brighter than the desk; 2. paper is grey
    # or white while most desks have some colour, which still holds where a
    # shadow falls on the sheet; 3. the strongest closed edge; 4. a looser
    # brightness mask. Each outline is scored by how sharp the image edge is
    # along its weakest side, and the best one wins.
    bright = ((v > t) & (sat < 80)).astype(np.uint8) * 255
    ts, _ = cv2.threshold(cv2.GaussianBlur(sat, (5, 5), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    neutral = ((sat < min(ts, 60)) & (v > 60)).astype(np.uint8) * 255
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(gray, 30, 90), np.ones((3, 3), np.uint8))
    filled = np.zeros_like(edges)
    cs, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if cs:
        cv2.drawContours(filled, [max(cs, key=cv2.contourArea)], -1, 255, thickness=cv2.FILLED)
    local = cv2.adaptiveThreshold(v, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 151, -5)
    loose = (((v > t * 0.85) | (local > 0)) & (sat < 70)).astype(np.uint8) * 255

    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad = cv2.GaussianBlur(np.hypot(gx, gy), (7, 7), 0)

    def support(q):
        worst = np.inf
        for i in range(4):
            a, b = q[i], q[(i + 1) % 4]
            ts_ = np.linspace(0.1, 0.9, 40)[:, None]
            pts = (a + (b - a) * ts_).round().astype(int)
            pts[:, 0] = pts[:, 0].clip(0, grad.shape[1] - 1)
            pts[:, 1] = pts[:, 1].clip(0, grad.shape[0] - 1)
            worst = min(worst, float(np.median(grad[pts[:, 1], pts[:, 0]])))
        return worst

    found = [q for q in (candidate(clean(m)) for m in (bright, neutral, filled, loose)) if q is not None]
    if found:
        return max(found, key=support) / scale
    return None


def flatten(img: np.ndarray, corners: np.ndarray, width: int = FLAT_W) -> tuple[np.ndarray, np.ndarray]:
    """Warp the sheet to a flat rectangle. Returns the image and photo->flat homography."""
    tl, tr, br, bl = corners
    top, bottom = np.linalg.norm(tr - tl), np.linalg.norm(br - bl)
    left, right = np.linalg.norm(bl - tl), np.linalg.norm(br - tr)
    height = int(round(width * (left + right) / (top + bottom)))
    dst = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    H = cv2.getPerspectiveTransform(corners.astype(np.float32), dst)
    flat = cv2.warpPerspective(img, H, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return flat, H


# ---------------------------------------------------------------- ocr

_ENGINE = None


def engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


def read_words(img: np.ndarray) -> list[Word]:
    result, _ = engine()(img)
    words = []
    for box, text, score in result or []:
        words.append(Word(text=str(text), conf=round(float(score), 4),
                          poly=[[round(float(x), 1), round(float(y), 1)] for x, y in box]))
    return words


def decode(data: bytes) -> np.ndarray:
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("not an image this tool can read")
    return cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)


def ocr_photo(data: bytes) -> OcrPage:
    import time
    t0 = time.perf_counter()
    img = decode(data)
    h, w = img.shape[:2]
    corners = find_page(img)
    if corners is not None:
        flat, H = flatten(img, corners)
        to_photo = np.linalg.inv(H)
    else:
        # No sheet edge found (a tight crop, or a sheet on a white table):
        # read the photo as it is, scaled to the usual working width.
        s = FLAT_W / w
        flat = cv2.resize(img, (FLAT_W, int(h * s)), interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
        to_photo = np.diag([1 / s, 1 / s, 1.0])
    t1 = time.perf_counter()
    words = read_words(flat)
    t2 = time.perf_counter()
    return OcrPage(
        words=words,
        flat_size=(flat.shape[1], flat.shape[0]),
        photo_size=(w, h),
        to_photo=np.asarray(to_photo).round(8).tolist(),
        page_corners=None if corners is None else corners.round(1).tolist(),
        timings={"page_s": round(t1 - t0, 3), "ocr_s": round(t2 - t1, 3)},
    )
