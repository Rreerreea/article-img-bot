"""Парсер проверяется на реальном фрагменте статьи 8Blocks."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.article_loader import load_article, load_from_url
from src.classifier import classify_type
from src.models import SlotType
from src.parser import parse

FIXTURE = Path(__file__).parent / "fixtures" / "8blocks_excerpt.txt"


@pytest.fixture
def slots():
    return parse(load_article(FIXTURE))


def test_finds_exactly_three_blocks(slots):
    # Буллеты-варианты заголовков в начале (без «Рис.») и обычные
    # абзацы между блоками в слоты не попадают.
    assert len(slots) == 3


def test_titles(slots):
    assert [s.title for s in slots] == ["", "Функции Блокчейна", "ТОКЕНЫ"]


def test_bullet_counts(slots):
    assert [len(s.bullets) for s in slots] == [4, 7, 7]


def test_all_infographic(slots):
    # Все три блока — перечни данных => инфографика (TZ 8a).
    assert all(s.type is SlotType.INFOGRAPHIC for s in slots)


def test_ids_unique_and_nonempty(slots):
    ids = [s.id for s in slots]
    assert all(ids)
    assert len(set(ids)) == 3
    assert ids[0] == "img1"  # пустой заголовок -> imgN


def test_bullet_text_clean(slots):
    # Маркер «•» и табы убраны, текст осмысленный.
    assert slots[2].bullets[0].startswith("BNB")


def test_classifier_few_bullets_is_story():
    assert classify_type("Подпись", ()) is SlotType.STORY
    assert classify_type("Подпись", ("единственный пункт",)) is SlotType.STORY


def test_loader_rejects_unknown_ext(tmp_path):
    bad = tmp_path / "article.pdf"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        load_article(bad)


def test_url_loader_rejects_non_http():
    # load_from_url реализован (Task 10); на не-http даёт понятную ошибку.
    with pytest.raises(ValueError):
        load_from_url("file:///etc/passwd")
