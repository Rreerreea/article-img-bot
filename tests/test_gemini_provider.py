"""Gemini-провайдер: разбор ответа, рефы, диспатч, конфиг — без вызовов API."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from src.config import Config, Mode, Provider
from src.higgsfield_worker import HiggsfieldWorker
from src.models import ImageSlot, SlotType
from src.prompt_builder import build

extract = HiggsfieldWorker._extract_gemini_bytes
load_refs = HiggsfieldWorker._load_ref_images


def _part(*, data=None, text=None):
    return SimpleNamespace(
        inline_data=SimpleNamespace(data=data) if data else None, text=text
    )


def test_extract_from_parts():
    resp = SimpleNamespace(parts=[_part(data=b"PNGBYTES")], candidates=None)
    assert extract(resp) == b"PNGBYTES"


def test_extract_from_candidates():
    resp = SimpleNamespace(
        parts=None,
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[_part(data=b"X")]))],
    )
    assert extract(resp) == b"X"


def test_extract_text_only_raises_with_reason():
    resp = SimpleNamespace(parts=[_part(text="refused: policy")], candidates=None)
    with pytest.raises(RuntimeError, match="refused: policy"):
        extract(resp)


def test_extract_empty_raises():
    with pytest.raises(RuntimeError):
        extract(SimpleNamespace(parts=[], candidates=None))


def test_load_ref_images_missing_dir(tmp_path):
    assert load_refs(tmp_path / "nope") == []


def test_load_ref_images_reads_and_limits(tmp_path):
    for i in range(6):
        Image.new("RGB", (8, 8), (i, i, i)).save(tmp_path / f"r{i}.png")
    imgs = load_refs(tmp_path, limit=4)
    assert len(imgs) == 4
    assert all(isinstance(im, Image.Image) for im in imgs)


def _cfg(tmp_path, **kw) -> Config:
    base = dict(
        mode=Mode.REAL, credentials="", model="m", quality="economy",
        concurrency=1, max_retries=0, price_per_image=0.0, base_dir=tmp_path,
        provider=Provider.GEMINI, gemini_api_key="k", gemini_model="gemini-2.5-flash-image",
    )
    base.update(kw)
    return Config(**base)


async def test_dispatch_gemini(tmp_path, monkeypatch):
    w = HiggsfieldWorker(_cfg(tmp_path, provider=Provider.GEMINI))
    slot = ImageSlot("x", "T", ("a",), SlotType.STORY)

    async def fake_gemini(spec):
        return b"GEM"

    monkeypatch.setattr(w, "_generate_gemini", fake_gemini)
    assert await w._generate(slot, build(slot, w.cfg.refs_dir)) == b"GEM"


async def test_dispatch_higgsfield(tmp_path, monkeypatch):
    w = HiggsfieldWorker(_cfg(tmp_path, provider=Provider.HIGGSFIELD))
    slot = ImageSlot("x", "T", ("a",), SlotType.STORY)

    async def fake_hf(spec):
        return b"HF"

    monkeypatch.setattr(w, "_generate_higgsfield", fake_hf)
    assert await w._generate(slot, build(slot, w.cfg.refs_dir)) == b"HF"


async def test_mock_wins_over_provider(tmp_path):
    w = HiggsfieldWorker(_cfg(tmp_path, mode=Mode.MOCK, provider=Provider.GEMINI))
    slot = ImageSlot("x", "T", ("a",), SlotType.STORY)
    data = await w._generate(slot, build(slot, w.cfg.refs_dir))
    assert Image.open(io.BytesIO(data)).size == (900, 600)  # это мок-заглушка


def test_from_env_default_provider_is_gemini(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_PROVIDER", raising=False)
    monkeypatch.delenv("HF_MODE", raising=False)
    assert Config.from_env(tmp_path).provider is Provider.GEMINI


def test_from_env_real_gemini_without_key_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_MODE", "REAL")
    monkeypatch.setenv("HF_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        Config.from_env(tmp_path)


def test_from_env_real_higgsfield_without_creds_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_MODE", "REAL")
    monkeypatch.setenv("HF_PROVIDER", "higgsfield")
    monkeypatch.delenv("HF_CREDENTIALS", raising=False)
    with pytest.raises(RuntimeError, match="HF_CREDENTIALS"):
        Config.from_env(tmp_path)


def test_genai_client_constructs_without_network():
    from google import genai

    client = genai.Client(api_key="dummy-key")
    assert hasattr(client, "models")
