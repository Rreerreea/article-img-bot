"""OpenAI GPT Image 2: разбор ответа, диспатч, конфиг — без вызовов API."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from src.config import Config, Mode, Provider
from src.higgsfield_worker import HiggsfieldWorker
from src.models import ImageSlot, SlotType
from src.prompt_builder import build

extract = HiggsfieldWorker._extract_openai_bytes


def test_extract_b64_to_bytes():
    resp = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(b"PNGDATA").decode())]
    )
    assert extract(resp) == b"PNGDATA"


def test_extract_empty_raises():
    with pytest.raises(RuntimeError, match="OpenAI не вернул"):
        extract(SimpleNamespace(data=[SimpleNamespace(b64_json=None)]))


def test_extract_bad_shape_raises():
    with pytest.raises(RuntimeError):
        extract(SimpleNamespace(data=[]))


def _cfg(tmp_path, **kw) -> Config:
    base = dict(
        mode=Mode.REAL, credentials="", model="m", quality="economy",
        concurrency=1, max_retries=0, price_per_image=0.0, base_dir=tmp_path,
        provider=Provider.OPENAI, openai_api_key="sk-test", openai_model="gpt-image-2",
    )
    base.update(kw)
    return Config(**base)


async def test_dispatch_openai(tmp_path, monkeypatch):
    w = HiggsfieldWorker(_cfg(tmp_path))
    slot = ImageSlot("x", "T", ("a",), SlotType.INFOGRAPHIC)

    async def fake_openai(spec):
        return b"OAI"

    monkeypatch.setattr(w, "_generate_openai", fake_openai)
    assert await w._generate(slot, build(slot, w.cfg.refs_dir)) == b"OAI"


def test_from_env_real_openai_without_key_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_MODE", "REAL")
    monkeypatch.setenv("HF_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        Config.from_env(tmp_path)


def test_from_env_openai_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.delenv("HF_MODE", raising=False)
    cfg = Config.from_env(tmp_path)
    assert cfg.provider is Provider.OPENAI
    assert cfg.openai_model == "gpt-image-2"
    assert cfg.openai_quality == "medium"


def test_async_openai_constructs():
    from openai import AsyncOpenAI

    c = AsyncOpenAI(api_key="sk-dummy")
    assert hasattr(c, "images")
