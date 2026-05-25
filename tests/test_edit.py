"""Фича 15: правки картинок (edit_image / pipeline.edit) — MOCK, без API."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from src.config import Config, Mode, Provider
from src.higgsfield_worker import HiggsfieldWorker
from src.models import ImageSlot, SlotType
from src.pipeline import PipelineService


def _png(size=(1536, 864)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, (100, 110, 120)).save(b, format="PNG")
    return b.getvalue()


def _cfg(tmp_path, **kw) -> Config:
    base = dict(
        mode=Mode.MOCK, credentials="", model="m", quality="economy",
        concurrency=2, max_retries=0, price_per_image=0.0, base_dir=tmp_path,
    )
    base.update(kw)
    return Config(**base)


async def test_edit_image_mock_changes_keeps_size(tmp_path):
    w = HiggsfieldWorker(_cfg(tmp_path))
    src = _png()
    out = await w.edit_image(src, "сделай темнее")
    assert out != src
    assert Image.open(io.BytesIO(out)).size == (1536, 864)


async def test_edit_image_non_gemini_real_raises(tmp_path):
    w = HiggsfieldWorker(
        _cfg(tmp_path, mode=Mode.REAL, provider=Provider.OPENAI,
             openai_api_key="sk-x")
    )
    with pytest.raises(RuntimeError, match="только на провайдере Gemini"):
        await w.edit_image(_png(), "что-нибудь")


async def test_pipeline_edit_roundtrip(tmp_path):
    svc = PipelineService(_cfg(tmp_path))
    slot = ImageSlot("tokeny", "ТОКЕНЫ", ("BNB",), SlotType.INFOGRAPHIC)
    await svc.run([slot])  # сгенерили в output/tokeny.png

    assert "tokeny" in svc.available_slot_ids()
    before = (svc.cfg.output_dir / "tokeny.png").read_bytes()

    path = await svc.edit("tokeny", "сделай ярче")
    assert path is not None and path.exists()
    after = path.read_bytes()
    assert after != before                       # правка применена
    assert Image.open(io.BytesIO(after)).size == (1536, 864)  # размер сохранён


async def test_pipeline_edit_missing_slot_returns_none(tmp_path):
    svc = PipelineService(_cfg(tmp_path))
    assert await svc.edit("nope", "x") is None
    assert svc.available_slot_ids() == []
