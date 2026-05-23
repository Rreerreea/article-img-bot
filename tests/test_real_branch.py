"""REAL-ветка: парсер URL ответа + конструируемость SDK — без ключей и сети.

Сам вызов Higgsfield платный и в тестах не делается. Проверяем то,
что можно без аккаунта: устойчивость разбора ответа и то, что SDK
сверен (импорт/конструктор реального пакета).
"""

from __future__ import annotations

import pytest

from src.higgsfield_worker import HiggsfieldWorker

extract = HiggsfieldWorker._extract_image_url


def test_images_form():
    assert extract({"images": [{"url": "http://x/a.png"}]}) == "http://x/a.png"


def test_result_url_form():
    assert extract({"result": {"url": "http://x/b.png"}}) == "http://x/b.png"


def test_result_raw_form():
    assert extract({"result": {"raw": {"url": "http://x/c.png"}}}) == "http://x/c.png"


def test_top_url_form():
    assert extract({"url": "http://x/d.png"}) == "http://x/d.png"


def test_unknown_dict_raises_with_shape():
    with pytest.raises(RuntimeError, match="сверить форму"):
        extract({"weird": 1, "shape": 2})


def test_non_dict_raises():
    with pytest.raises(RuntimeError):
        extract(["not", "a", "dict"])


def test_sdk_is_the_expected_one():
    # SDK сверен: фактический пакет даёт AsyncClient(api_key=...).
    import higgsfield_client

    client = higgsfield_client.AsyncClient(api_key="KEY:SECRET")  # без сети
    assert hasattr(client, "subscribe")
