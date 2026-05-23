"""Загрузчик по ссылке: Google Docs txt-export + web/Notion HTML→текст."""

from __future__ import annotations

import pytest

import src.article_loader as al
from src.article_loader import load_from_url
from src.parser import parse


def test_google_docs_uses_txt_export(monkeypatch):
    seen = {}

    def fake_get(url: str) -> str:
        seen["url"] = url
        return "Рис. Демо\n• первый\n• второй\n"

    monkeypatch.setattr(al, "_http_get", fake_get)

    text = load_from_url(
        "https://docs.google.com/document/d/ABC_123-xyz/edit?usp=sharing"
    )
    assert seen["url"] == (
        "https://docs.google.com/document/d/ABC_123-xyz/export?format=txt"
    )
    assert "Рис. Демо" in text


def test_generic_web_html_is_parsed(monkeypatch):
    html = (
        "<html><body><p>Вступление</p>"
        "<p>Рис. ТОКЕНЫ</p>"
        "<ul><li>BNB — Binance</li><li>HTR — Hathor</li></ul>"
        "<p>Заключение</p></body></html>"
    )
    monkeypatch.setattr(al, "_http_get", lambda url: html)

    slots = parse(load_from_url("https://example.com/article"))
    assert len(slots) == 1
    assert slots[0].title == "ТОКЕНЫ"
    assert slots[0].bullets == ("BNB — Binance", "HTR — Hathor")


def test_html_to_text_strips_script_and_style():
    out = al._html_to_text(
        "<p>Видно</p><script>alert(1)</script><style>b{color:red}</style>"
    )
    assert "Видно" in out
    assert "alert" not in out
    assert "color:red" not in out


def test_rejects_non_http():
    with pytest.raises(ValueError):
        load_from_url("ftp://example.com/article.txt")


def test_parser_accepts_dash_bullets():
    # Markdown/Google Docs нередко дают «- », а не «•» — парсер должен понять.
    slots = parse("Рис. Список\n- один\n- два\n")
    assert len(slots) == 1
    assert slots[0].bullets == ("один", "два")
