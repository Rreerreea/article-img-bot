"""Текст-слой гибрида 10.A: правильный текст ТЗ поверх визуала."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from src.config import Config, Mode
from src.higgsfield_worker import HiggsfieldWorker
from src.models import ImageSlot, SlotType
from src.text_overlay import _font, _wrap, render


def _bg(size=(1280, 720)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (120, 130, 140)).save(buf, format="PNG")
    return buf.getvalue()


def _slot() -> ImageSlot:
    return ImageSlot(
        "tokeny", "ТОКЕНЫ",
        ("BNB — собственный токен экосистемы Binance.",
         "HTR — родной токен платформы Hathor."),
        SlotType.INFOGRAPHIC,
    )


def test_render_keeps_size_and_changes_pixels():
    src = _bg()
    out = render(src, _slot(), (1280, 720))
    img = Image.open(io.BytesIO(out))
    assert img.size == (1280, 720)
    assert img.format == "PNG"
    assert out != src  # текст реально наложен


def test_font_loads_some_font():
    f = _font(40)
    assert f is not None


def test_wrap_splits_long_line():
    img = Image.new("RGB", (400, 100))
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    long = "очень длинная строка которая точно не влезет в узкую ширину" * 2
    lines = _wrap(d, long, _font(30), 300)
    assert len(lines) > 1


def test_empty_title_does_not_crash():
    slot = ImageSlot("x", "", ("единственный пункт",), SlotType.INFOGRAPHIC)
    out = render(_bg(), slot, (1280, 720))
    assert Image.open(io.BytesIO(out)).size == (1280, 720)


def _cfg(tmp_path, **kw) -> Config:
    base = dict(
        mode=Mode.MOCK, credentials="", model="m", quality="economy",
        concurrency=1, max_retries=0, price_per_image=0.0, base_dir=tmp_path,
    )
    base.update(kw)
    return Config(**base)


async def test_overlay_flag_changes_infographic_output(tmp_path):
    slot = _slot()

    on = HiggsfieldWorker(_cfg(tmp_path / "a", text_overlay=True))
    off = HiggsfieldWorker(_cfg(tmp_path / "b", text_overlay=False))

    r_on = await on.generate_one(slot)
    r_off = await off.generate_one(slot)

    a = r_on.file_path.read_bytes()
    b = r_off.file_path.read_bytes()
    assert a != b  # флаг реально влияет: с текстом vs чистый визуал
    assert Image.open(io.BytesIO(a)).size == (1280, 720)
    assert Image.open(io.BytesIO(b)).size == (1280, 720)


async def test_story_slot_skips_overlay(tmp_path):
    # У сюжетных текста нет — overlay не применяется, мок-размер сохранён.
    w = HiggsfieldWorker(_cfg(tmp_path, text_overlay=True))
    slot = ImageSlot("s1", "", ("кот у графиков",), SlotType.STORY)
    res = await w.generate_one(slot)
    assert Image.open(io.BytesIO(res.file_path.read_bytes())).size == (1024, 1024)
