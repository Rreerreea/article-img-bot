"""Лёгкое состояние на чат: пресет стиля, ожидание рефов, последние
картинки (для правок). Файловое хранилище — переживает рестарт бота.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class ChatState:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        # Атомарно: пишем во временный и переименовываем.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get(self, chat_id: int, key: str, default: Any = None) -> Any:
        return self._load().get(str(chat_id), {}).get(key, default)

    def set(self, chat_id: int, key: str, value: Any) -> None:
        data = self._load()
        data.setdefault(str(chat_id), {})[key] = value
        self._save(data)
