"""Промпт-билдер: размеры/aspect по типу + привязка рефов к кэшу."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from src.config import Config, Mode
from src.higgsfield_worker import HiggsfieldWorker
from src.models import ImageSlot, SlotType
from src.prompt_builder import build, refs_signature


def _infographic() -> ImageSlot:
    return ImageSlot("tokens", "ТОКЕНЫ", ("BNB — Binance", "HTR — Hathor"), SlotType.INFOGRAPHIC)


def _story() -> ImageSlot:
    return ImageSlot("img1", "", ("кот на фоне графиков",), SlotType.STORY)


def test_infographic_spec(tmp_path):
    spec = build(_infographic(), tmp_path / "refs")
    assert spec.aspect_ratio == "16:9"
    assert spec.target_size == (1536, 864)
    assert spec.refs_dir.name == "infographic"
    # Решение Гоши: нейросеть рисует ВЕСЬ текст — даём точный
    # контент дословно + требование аккуратной кириллицы.
    assert "ТОКЕНЫ" in spec.prompt
    assert "BNB — Binance" in spec.prompt
    assert "HTR — Hathor" in spec.prompt
    assert "EXACTLY" in spec.prompt


def test_story_spec(tmp_path):
    spec = build(_story(), tmp_path / "refs")
    assert spec.aspect_ratio == "16:9"
    assert spec.target_size == (1536, 864)
    assert spec.refs_dir.name == "story"


def test_refs_signature_empty_then_set(tmp_path):
    folder = tmp_path / "refs" / "infographic"
    assert refs_signature(folder) == ""  # папки ещё нет
    folder.mkdir(parents=True)
    assert refs_signature(folder) == ""  # пустая
    (folder / "style1.png").write_bytes(b"x")
    sig1 = refs_signature(folder)
    assert sig1 != ""
    (folder / "style2.png").write_bytes(b"yy")
    assert refs_signature(folder) != sig1  # добавили реф — сигнатура сменилась


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        mode=Mode.MOCK,
        credentials="",
        model="flux-pro/kontext/max/text-to-image",
        quality="economy",
        concurrency=3,
        max_retries=2,
        price_per_image=0.05,
        base_dir=tmp_path,
    )


async def test_estimate_always_full_no_cache(cfg):
    """Кэш вырезан 2026-05-25 — смета всегда показывает полную генерацию,
    независимо от прошлых прогонов и изменения рефов."""
    w = HiggsfieldWorker(cfg)
    slot = _infographic()

    res = await w.generate_one(slot)
    assert res.status.value == "ok"
    assert w.estimate([slot]).cached == 0
    assert w.estimate([slot]).to_generate == 1

    refs = cfg.refs_dir / "infographic"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "brand.png").write_bytes(b"ref-bytes")

    est = w.estimate([slot])
    assert est.cached == 0
    assert est.to_generate == 1


async def test_generated_file_matches_target_size(cfg):
    """Сквозная проверка: воркер+билдер+постобработка в MOCK."""
    w = HiggsfieldWorker(cfg)
    res = await w.generate_one(_infographic())
    assert Image.open(io.BytesIO(res.file_path.read_bytes())).size == (1536, 864)
