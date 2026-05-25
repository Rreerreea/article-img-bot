"""Автоперевод title+bullets слотов через OpenAI Chat API.

Используется когда юзер тапает «🌍 Перевести» → выбирает язык кнопкой.
Альтернатива (флоу с файлом-переводом) — оставлена как ручной режим
для редких языков или своего тонкого перевода.
"""

from __future__ import annotations

import dataclasses
import json

from .models import ImageSlot


async def translate_slots(
    slots: list[ImageSlot],
    target_lang_label: str,
    openai_api_key: str,
    model: str = "gpt-4o-mini",
) -> list[ImageSlot]:
    """Возвращает новые слоты с переведённым title и bullets.

    id, type, category — сохраняются. Один запрос на все тексты для
    скорости и атомарности (если упало — слоты не покалечены).
    """
    if not slots:
        return slots

    texts: list[str] = []
    for s in slots:
        texts.append(s.title)
        texts.extend(s.bullets)

    # Нечего переводить.
    if not any(t.strip() for t in texts):
        return slots

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=openai_api_key or None)
    system = (
        f"You are a professional translator. Translate every string in the "
        f"input array to {target_lang_label}. Preserve numbers, currency "
        "symbols, proper names, brand names, code, special characters as-is. "
        "Do not paraphrase. Output a valid JSON object with key 'items' "
        "containing an array of translated strings in the SAME order and "
        "SAME length as the input."
    )
    user = json.dumps({"items": texts}, ensure_ascii=False)

    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    content = resp.choices[0].message.content
    parsed = json.loads(content)
    translated = parsed.get("items", [])

    if len(translated) != len(texts):
        raise RuntimeError(
            "Translator length mismatch: "
            f"input {len(texts)} → output {len(translated)}"
        )

    # Восстановим структуру слотов.
    new_slots = []
    idx = 0
    for s in slots:
        new_title = translated[idx]
        idx += 1
        n_bullets = len(s.bullets)
        new_bullets = tuple(translated[idx:idx + n_bullets])
        idx += n_bullets
        new_slots.append(
            dataclasses.replace(s, title=new_title, bullets=new_bullets)
        )
    return new_slots
