"""Оркестратор end-to-end в MOCK: фикстура 8Blocks -> смета -> ZIP."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from src.config import Config, Mode
from src.pipeline import PipelineService

FIXTURE = Path(__file__).parent / "fixtures" / "8blocks_excerpt.txt"


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


def test_prepare_from_fixture(cfg):
    slots, est = PipelineService(cfg).prepare(FIXTURE)
    assert len(slots) == 3
    assert est.total == 3
    assert est.to_generate == 3  # кэш пуст в свежем tmp
    assert est.cached == 0


def test_prepare_from_text(cfg):
    text = "Рис. Демо\n\t•\tпервый пункт\n\t•\tвторой пункт\n"
    slots, est = PipelineService(cfg).prepare(text=text)
    assert len(slots) == 1
    assert slots[0].title == "Демо"


def test_prepare_requires_input(cfg):
    with pytest.raises(ValueError):
        PipelineService(cfg).prepare()


def test_prepare_from_url(cfg, monkeypatch):
    import src.article_loader as al

    monkeypatch.setattr(
        al, "_http_get", lambda url: "Рис. Демо\n• раз\n• два\n"
    )
    slots, est = PipelineService(cfg).prepare(
        "https://docs.google.com/document/d/ABC/edit"
    )
    assert len(slots) == 1
    assert est.total == 1


async def test_run_builds_zip_with_all_images(cfg):
    svc = PipelineService(cfg)
    slots, _ = svc.prepare(FIXTURE)
    result = await svc.run(slots)

    assert result.failed == 0
    assert result.ok == 3
    assert result.zip_path is not None and result.zip_path.exists()

    names = zipfile.ZipFile(result.zip_path).namelist()
    assert len(names) == 3
    assert all(n.endswith(".png") for n in names)


async def test_rerun_uses_cache(cfg):
    svc = PipelineService(cfg)
    slots, _ = svc.prepare(FIXTURE)
    await svc.run(slots)

    again = await svc.run(slots)  # второй прогон — всё из кэша, API не тронут
    assert again.from_cache == 3
    assert again.ok == 0
