"""Однократная рассылка сообщения всем чатам, которые когда-либо
взаимодействовали с ботом (т.е. есть в PicklePersistence).

Использование на сервере:
    cd /opt/article-img-bot
    .venv/bin/python -m scripts.broadcast scripts/changelog.txt

или с heredoc:
    .venv/bin/python -m scripts.broadcast - <<'EOF'
    🎉 Бот обновлён ...
    EOF
"""

from __future__ import annotations

import asyncio
import logging
import os
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PICKLE = ROOT / ".state" / "bot.pickle"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("broadcast")


def _load_chat_ids() -> list[int]:
    """Извлекает chat_id из PicklePersistence. PTB кладёт там user_data,
    chat_data, bot_data, conversations. Берём из chat_data."""
    if not PICKLE.exists():
        log.warning("Нет %s — некому слать", PICKLE)
        return []
    with open(PICKLE, "rb") as f:
        data = pickle.load(f)
    chat_data = data.get("chat_data", {})
    user_data = data.get("user_data", {})
    # На всякий случай объединяем ключи из обоих (для приватных чатов это
    # один и тот же id).
    ids = set(chat_data.keys()) | set(user_data.keys())
    return sorted(i for i in ids if isinstance(i, int))


async def _send(chat_id: int, token: str, text: str) -> bool:
    import httpx
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            })
        if r.status_code == 200:
            return True
        log.warning("chat=%s HTTP %s: %s", chat_id, r.status_code, r.text[:200])
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("chat=%s exc: %s", chat_id, exc)
        return False


async def main():
    if len(sys.argv) < 2:
        log.error("Usage: python -m scripts.broadcast <text_file|->")
        sys.exit(1)

    src = sys.argv[1]
    if src == "-":
        text = sys.stdin.read()
    else:
        text = Path(src).read_text(encoding="utf-8")
    text = text.strip()
    if not text:
        log.error("Пустое сообщение — нечего слать.")
        sys.exit(1)

    # Загружаем .env (где TELEGRAM_TOKEN)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ModuleNotFoundError:
        pass
    token = os.getenv("TELEGRAM_TOKEN", "")
    if not token:
        log.error("TELEGRAM_TOKEN пуст")
        sys.exit(1)

    ids = _load_chat_ids()
    log.info("Найдено %d чатов: %s", len(ids), ids)
    if not ids:
        return
    log.info("Текст:\n%s", text)

    ok, fail = 0, 0
    for cid in ids:
        if await _send(cid, token, text):
            ok += 1
        else:
            fail += 1
        await asyncio.sleep(0.05)  # лёгкий троттлинг
    log.info("Готово. Отправлено: %d, ошибок: %d", ok, fail)


if __name__ == "__main__":
    asyncio.run(main())
