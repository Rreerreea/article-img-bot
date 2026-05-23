"""Whitelist: безопасный дефолт — пусто пускает НИКОГО (TZ 7.2)."""

from __future__ import annotations

from src.config import Config, Mode
from src.telegram_bot import build_handlers
from src.whitelist import Whitelist


def test_allows_listed_blocks_others():
    wl = Whitelist(frozenset({111, 222}))
    assert wl.is_allowed(111) is True
    assert wl.is_allowed(999) is False
    assert wl.is_allowed(None) is False


def test_empty_blocks_everyone():
    wl = Whitelist(frozenset())
    assert wl.is_empty is True
    assert wl.is_allowed(111) is False


def test_star_allows_all_test_mode():
    wl = Whitelist(frozenset(), allow_all=True)
    assert wl.is_allowed(12345) is True
    assert wl.is_allowed(None) is True
    assert wl.is_empty is False  # осознанно открыто -> бот стартует


def test_from_env_star_is_allow_all(monkeypatch):
    monkeypatch.setenv("HF_ALLOWED_USER_IDS", "*")
    wl = Whitelist.from_env()
    assert wl.allow_all is True
    assert wl.is_allowed(999) is True


def test_from_env_parses_list(monkeypatch):
    monkeypatch.setenv("HF_ALLOWED_USER_IDS", "10, 20 , junk, -5")
    wl = Whitelist.from_env()
    assert wl.allowed == frozenset({10, 20, -5})
    assert wl.allow_all is False


def test_from_env_empty(monkeypatch):
    monkeypatch.delenv("HF_ALLOWED_USER_IDS", raising=False)
    assert Whitelist.from_env().is_empty


def test_build_handlers_returns_callables(tmp_path):
    cfg = Config(
        mode=Mode.MOCK,
        credentials="",
        model="m",
        quality="economy",
        concurrency=1,
        max_retries=0,
        price_per_image=0.05,
        base_dir=tmp_path,
    )
    handlers = build_handlers(cfg, Whitelist(frozenset({1})))
    assert isinstance(handlers, dict)
    for key in ("start", "help", "go", "article", "style", "refs",
                "ref_photo", "edit", "callback", "error"):
        assert callable(handlers[key]), key
