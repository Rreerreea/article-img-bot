"""Загрузка ref-картинок на публичный URL для Krea API.

Krea принимает рефы через `imageUrls` / `styleImages` — только публичные
URL, не base64 и не файлы. У нас же рефы локальные. Льём на 0x0.st
(публичный uploader, без аутентификации, файлы 365 дней).

Кэшируем по SHA1 содержимого: один реф = одна загрузка, повторно
переиспользуем URL пока 0x0.st его помнит.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger("bot")

UPLOAD_URL = "https://0x0.st"
CACHE_FILE = Path("/tmp/article-img-bot-krea-url-cache.json")


def _load_cache() -> dict[str, str]:
    if not CACHE_FILE.is_file():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict[str, str]) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))
    except OSError:
        pass


async def upload_for_krea(path: Path) -> str | None:
    """Возвращает публичный URL для файла или None при ошибке.

    Кэширует по SHA1 содержимого. Дедупликация снижает расход
    лимитов 0x0.st при повторных генерациях.
    """
    if not path.is_file():
        return None

    data = path.read_bytes()
    digest = hashlib.sha1(data).hexdigest()
    cache = _load_cache()
    if digest in cache:
        return cache[digest]

    import httpx
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.post(
                UPLOAD_URL,
                files={"file": (path.name, data)},
                # 0x0.st просит User-Agent (без него 403).
                headers={"User-Agent": "article-img-bot/0.6"},
            )
        if r.status_code >= 400:
            log.warning(
                "0x0.st upload %s HTTP %s: %s",
                path.name, r.status_code, r.text[:200],
            )
            return None
        url = r.text.strip()
        if not url.startswith("http"):
            log.warning("0x0.st вернул не URL: %s", url[:200])
            return None
        cache[digest] = url
        _save_cache(cache)
        return url
    except Exception as exc:  # noqa: BLE001
        log.warning("0x0.st upload failed for %s: %s", path.name, exc)
        return None


async def upload_many(paths: list[Path]) -> list[str]:
    """Параллельная загрузка списка путей. Падают по одному — игнорим
    битые, остальные возвращаем."""
    import asyncio
    results = await asyncio.gather(
        *(upload_for_krea(p) for p in paths),
        return_exceptions=False,
    )
    return [u for u in results if u]
