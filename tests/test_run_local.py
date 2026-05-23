"""CLI-демо: сквозной прогон фикстуры в MOCK даёт ZIP."""

from __future__ import annotations

from pathlib import Path

import pytest

from run_local import demo
from src.config import Config, Mode

FIXTURE = Path(__file__).parent / "fixtures" / "8blocks_excerpt.txt"


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        mode=Mode.MOCK,
        credentials="",
        model="m",
        quality="economy",
        concurrency=3,
        max_retries=1,
        price_per_image=0.05,
        base_dir=tmp_path,
    )


async def test_demo_produces_zip(cfg):
    result = await demo(str(FIXTURE), cfg)
    assert result.zip_path is not None
    assert result.zip_path.exists()
    assert result.ok == 3
    assert result.failed == 0
