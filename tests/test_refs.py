"""Фича 14: загрузка рефов через бот — чистая логика (бот e2e — живьём)."""

from __future__ import annotations

import io

from PIL import Image

from src.prompt_builder import refs_signature
from src.telegram_bot import IMG_EXT, MAX_REFS, REF_TYPES, _ref_count


def test_ref_count_counts_only_images(tmp_path):
    assert _ref_count(tmp_path / "nope") == 0
    (tmp_path / "ref_1.jpg").write_bytes(b"x")
    (tmp_path / "ref_2.png").write_bytes(b"y")
    (tmp_path / "notes.txt").write_text("not an image")
    assert _ref_count(tmp_path) == 2


def test_ref_types_and_limit_sane():
    assert REF_TYPES == {"infographic", "story"}
    assert MAX_REFS >= 1
    assert ".jpg" in IMG_EXT and ".png" in IMG_EXT


def test_added_refs_change_signature(tmp_path):
    folder = tmp_path / "infographic"
    folder.mkdir()
    assert refs_signature(folder) == ""  # пусто
    Image.new("RGB", (8, 8)).save(folder / "ref_1.jpg")
    sig1 = refs_signature(folder)
    assert sig1 != ""
    Image.new("RGB", (8, 8), (9, 9, 9)).save(folder / "ref_2.jpg")
    # Новый реф -> другая сигнатура -> кэш инвалидируется (перегенерация).
    assert refs_signature(folder) != sig1
