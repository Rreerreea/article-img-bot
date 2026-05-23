"""Тесты воркера в MOCK-режиме — гоняют весь пайплайн без Higgsfield и без затрат."""

from __future__ import annotations

import pytest

from src.config import Config, Mode
from src.higgsfield_worker import HiggsfieldWorker
from src.models import GenStatus, ImageSlot, SlotType


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


@pytest.fixture
def slot() -> ImageSlot:
    return ImageSlot(
        id="tokens",
        title="ТОКЕНЫ",
        bullets=("BNB — токен Binance", "HTR — токен Hathor"),
        type=SlotType.INFOGRAPHIC,
    )


async def test_generate_one_creates_file(cfg, slot):
    w = HiggsfieldWorker(cfg)
    res = await w.generate_one(slot)

    assert res.status is GenStatus.OK
    assert res.ok
    assert res.file_path.exists()
    assert res.file_path.name == "tokens.png"


async def test_second_run_hits_cache(cfg, slot):
    w = HiggsfieldWorker(cfg)
    first = await w.generate_one(slot)
    second = await w.generate_one(slot)

    assert first.status is GenStatus.OK
    assert second.status is GenStatus.FROM_CACHE  # API не дёргали повторно
    assert second.file_path.exists()


async def test_cache_key_changes_with_refs(slot):
    assert slot.cache_key("refs-v1") != slot.cache_key("refs-v2")


async def test_batch_all_ok(cfg):
    slots = [
        ImageSlot(f"img{i}", f"Заголовок {i}", (f"пункт {i}",), SlotType.STORY)
        for i in range(5)
    ]
    w = HiggsfieldWorker(cfg)
    results = await w.generate_batch(slots)

    assert len(results) == 5
    assert all(r.ok for r in results)


async def test_estimate_counts_cache(cfg, slot):
    w = HiggsfieldWorker(cfg)

    before = w.estimate([slot])
    assert before.total == 1
    assert before.cached == 0
    assert before.to_generate == 1
    assert before.approx_cost_usd == pytest.approx(0.05)

    await w.generate_one(slot)  # теперь слот в кэше

    after = w.estimate([slot])
    assert after.cached == 1
    assert after.to_generate == 0
    assert after.approx_cost_usd == pytest.approx(0.0)
