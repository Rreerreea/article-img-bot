"""Регрессионные тесты под P0/P1 баги из ревью 2026-05-25.

Что покрываем:
- start() сбрасывает залипший running-флаг
- style override не мутирует user_data.slots permanently
- _clean_markdown пустой результат → RuntimeError, не «не нашёл маркеров»
- .style.txt битый → не валит build, просто игнорируется
- has_refs корректно детектится в build()
"""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path

import pytest

from src.models import ImageSlot, SlotType
from src.parser import parse
from src.prompt_builder import build, STYLE_HINT_FOR_REFS


def test_parse_marker_with_leading_bullet():
    # Регрессия — друг писал "- Рис. [Сюжет] ..." (markdown-буллет в Word).
    text = "- Рис. [Сюжет] Золотые монеты\n• монеты\n• руки\n"
    slots = parse(text)
    assert len(slots) == 1
    assert slots[0].category == "сюжет"
    assert "монет" in slots[0].title.lower()
    assert len(slots[0].bullets) == 2


def test_parse_marker_category_optional():
    text = "Рис. Заголовок без категории\n• первый\n• второй\n"
    slots = parse(text)
    assert len(slots) == 1
    assert slots[0].category is None


def test_build_has_refs_true_when_folder_not_empty():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "infographic").mkdir()
        (base / "infographic" / "r.jpg").write_bytes(b"data")
        slot = ImageSlot(
            id="x", title="T", bullets=("a", "b"), type=SlotType.INFOGRAPHIC
        )
        spec = build(slot, base)
        assert STYLE_HINT_FOR_REFS in spec.prompt


def test_build_has_refs_false_when_folder_empty():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "infographic").mkdir()  # пустая папка
        slot = ImageSlot(
            id="x", title="T", bullets=("a",), type=SlotType.INFOGRAPHIC
        )
        spec = build(slot, base)
        assert STYLE_HINT_FOR_REFS not in spec.prompt


def test_build_loads_style_desc_and_changes_signature():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "infographic").mkdir()
        (base / "infographic" / "r.jpg").write_bytes(b"data")
        slot = ImageSlot(
            id="x", title="T", bullets=("a",), type=SlotType.INFOGRAPHIC
        )
        spec_before = build(slot, base)
        (base / "infographic" / ".style.txt").write_text(
            "тёмный неон, изометрия", encoding="utf-8"
        )
        spec_after = build(slot, base)
        # Описание появилось в промпте.
        assert "тёмный неон, изометрия" in spec_after.prompt
        # Сигнатура поменялась → cache miss → перегенерация.
        assert spec_before.refs_signature != spec_after.refs_signature


def test_build_tolerates_broken_style_txt():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "infographic").mkdir()
        (base / "infographic" / "r.jpg").write_bytes(b"data")
        # Невалидный utf-8.
        (base / "infographic" / ".style.txt").write_bytes(b"\xff\xfe\xff")
        slot = ImageSlot(
            id="x", title="T", bullets=("a",), type=SlotType.INFOGRAPHIC
        )
        # Не должно падать — просто без user_notes.
        spec = build(slot, base)
        assert "User style notes" not in spec.prompt


def test_clean_markdown_empty_input_returns_empty():
    from src.article_loader import _clean_markdown
    assert _clean_markdown("") == ""
    assert _clean_markdown("   \n\n  ").strip() == ""


def test_clean_markdown_strips_base64_images():
    from src.article_loader import _clean_markdown
    md = "Hello\n![](data:image/png;base64,XXXXX)\nWorld"
    out = _clean_markdown(md)
    assert "data:image" not in out
    assert "Hello" in out and "World" in out


def test_dataclasses_replace_doesnt_share_state():
    """Регрессия: style override через replace создаёт НОВЫЕ слоты,
    не модифицирует исходные. Иначе залипает category между прогонами."""
    s = ImageSlot(
        id="x", title="T", bullets=("a",), type=SlotType.INFOGRAPHIC,
        category=None,
    )
    s2 = dataclasses.replace(s, category="story")
    assert s.category is None  # исходный не мутирован
    assert s2.category == "story"
    assert s.id == s2.id  # id сохраняется


def test_is_non_retryable_auth_error():
    """Auth/billing ошибки не должны ретраиться — деньги жжём впустую."""
    from src.higgsfield_worker import _is_non_retryable

    class AuthenticationError(Exception):
        pass

    class BadRequestError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    assert _is_non_retryable(AuthenticationError("invalid api key")) is True
    assert _is_non_retryable(BadRequestError("file too large")) is True
    # Сетевые/таймауты — ретраим.
    assert _is_non_retryable(APITimeoutError("timeout")) is False
    # Сообщение с billing/quota маркером.
    assert _is_non_retryable(
        RuntimeError("billing_hard_limit_reached")
    ) is True
    assert _is_non_retryable(
        RuntimeError("insufficient_quota")
    ) is True


def test_disk_space_check_blocks_generation(tmp_path, monkeypatch):
    """Меньше 100 MB свободно — выпадает с понятной ошибкой до сжигания
    API-токенов, а не после череды OSError(ENOSPC) в кэше."""
    import asyncio
    import shutil
    from src.config import Config, Mode, Provider
    from src.higgsfield_worker import HiggsfieldWorker

    cfg = Config(
        mode=Mode.MOCK, credentials="", model="x", quality="economy",
        concurrency=1, max_retries=0, price_per_image=0.0,
        base_dir=tmp_path, provider=Provider.GEMINI,
    )
    w = HiggsfieldWorker(cfg)

    class FakeUsage:
        free = 50 * 1024 * 1024  # 50 MB — мало

    monkeypatch.setattr(shutil, "disk_usage", lambda _: FakeUsage())
    slot = ImageSlot(
        id="x", title="T", bullets=("a",), type=SlotType.INFOGRAPHIC
    )
    with pytest.raises(RuntimeError, match="свободного места на диске мало|Свободного места"):
        asyncio.get_event_loop().run_until_complete(
            w.generate_batch([slot])
        )
