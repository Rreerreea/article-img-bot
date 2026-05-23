"""Фича 13: пресеты стиля + per-chat состояние."""

from __future__ import annotations

import pytest

from src import presets
from src.config import Config, Mode
from src.higgsfield_worker import HiggsfieldWorker
from src.models import ImageSlot, SlotType
from src.prompt_builder import build
from src.state import ChatState


def test_canon_known_and_fallback():
    assert presets.canon("flat") == "flat"
    assert presets.canon("BOGUS") == presets.DEFAULT
    assert presets.canon(None) == presets.DEFAULT


def test_presets_differ():
    assert presets.get("flat").infographic != presets.get("dark").infographic


def test_chatstate_persists(tmp_path):
    p = tmp_path / ".state" / "chat.json"
    s1 = ChatState(p)
    s1.set(111, "preset", "dark")
    s1.set(222, "preset", "flat")
    # Новый инстанс читает с диска (переживает рестарт).
    s2 = ChatState(p)
    assert s2.get(111, "preset") == "dark"
    assert s2.get(222, "preset") == "flat"
    assert s2.get(999, "preset", "premium") == "premium"


def test_prompt_changes_with_preset_but_keeps_text(tmp_path):
    slot = ImageSlot("t", "ТОКЕНЫ", ("BNB — Binance",), SlotType.INFOGRAPHIC)
    flat = build(slot, tmp_path, "flat").prompt
    dark = build(slot, tmp_path, "dark").prompt
    assert flat != dark                       # стиль разный
    assert "ТОКЕНЫ" in flat and "ТОКЕНЫ" in dark   # контент сохранён
    assert "BNB — Binance" in flat and "BNB — Binance" in dark


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        mode=Mode.MOCK, credentials="", model="m", quality="economy",
        concurrency=2, max_retries=0, price_per_image=0.05, base_dir=tmp_path,
    )


async def test_preset_invalidates_cache(cfg):
    slot = ImageSlot("t", "ТОКЕНЫ", ("BNB",), SlotType.INFOGRAPHIC)
    w = HiggsfieldWorker(cfg)

    await w.generate_one(slot, preset="flat")
    # Тот же слот, другой пресет — кэш не подходит, нужна перегенерация.
    assert w.estimate([slot], preset="flat").cached == 1
    assert w.estimate([slot], preset="dark").cached == 0


async def test_batch_with_preset_ok(cfg):
    slots = [
        ImageSlot(f"i{i}", f"Заг {i}", (f"п{i}",), SlotType.STORY)
        for i in range(3)
    ]
    res = await HiggsfieldWorker(cfg).generate_batch(slots, preset="corporate")
    assert all(r.ok for r in res)


async def test_progress_cb_called_per_slot(cfg):
    from src.models import ImageSlot, SlotType
    slots = [ImageSlot(f"p{i}", f"З{i}", (f"б{i}",), SlotType.STORY)
             for i in range(4)]
    seen = []
    await HiggsfieldWorker(cfg).generate_batch(
        slots, progress_cb=lambda d, t: seen.append((d, t))
    )
    assert len(seen) == 4
    assert seen[-1] == (4, 4)
    assert sorted(d for d, _ in seen) == [1, 2, 3, 4]
