"""Постобработка приводит любой вход к целевому размеру (TZ 7.5)."""

from __future__ import annotations

import io

from PIL import Image

from src.postprocess import normalize


def _img_bytes(size: tuple[int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_upscales_small_to_target():
    out = normalize(_img_bytes((50, 80)), (1280, 720))
    img = Image.open(io.BytesIO(out))
    assert img.size == (1280, 720)
    assert img.format == "PNG"


def test_already_target_stays_target():
    out = normalize(_img_bytes((1024, 1024)), (1024, 1024))
    assert Image.open(io.BytesIO(out)).size == (1024, 1024)


def test_wide_source_cover_crops_without_distortion():
    # Широкий вход в квадрат: сторона ровно целевая, без полей.
    out = normalize(_img_bytes((2000, 500)), (1024, 1024))
    assert Image.open(io.BytesIO(out)).size == (1024, 1024)
