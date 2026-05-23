"""Текстовый слой для инфографики (гибрид TZ 10.A).

Gemini рисует визуал без текста (модель искажает длинный русский).
Правильные подписи берём из данных ТЗ и кладём программно — текст
тогда 100% верный, не зависит от модели.

Шрифт ищем по списку кандидатов (env FONT_PATH в приоритете). Для
Linux/Docker нужен пакет шрифтов с кириллицей (см. Dockerfile).
"""

from __future__ import annotations

import io
import os
import textwrap

from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = [
    os.getenv("FONT_PATH", ""),
    # macOS
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    # Linux / Docker (apt: fonts-dejavu-core)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if path and os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    # Крайний фолбэк: дефолт PIL (мелкий, но не падаем).
    return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """Перенос по ширине в пикселях (кириллица — поэтому не textwrap по символам)."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render(image_bytes: bytes, slot, size: tuple[int, int]) -> bytes:
    """Кладёт заголовок + буллеты поверх визуала. Возвращает PNG-байты."""
    base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    if base.size != size:
        base = base.resize(size)
    w, h = size

    # Лёгкая светлая вуаль для контраста тёмного текста поверх визуала.
    veil = Image.new("RGBA", size, (255, 255, 255, 96))
    base = Image.alpha_composite(base, veil)
    draw = ImageDraw.Draw(base)

    pad = int(w * 0.05)
    x = pad
    y = pad
    max_w = w - 2 * pad
    dark = (20, 22, 28, 255)

    def plate(x0, y0, x1, y1):
        # Полупрозрачная подложка под строку — читаемость на любом фоне.
        ov = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(ov).rounded_rectangle(
            [x0 - 10, y0 - 6, x1 + 10, y1 + 6], radius=10,
            fill=(255, 255, 255, 175),
        )
        return ov

    if slot.title:
        tf = _font(54)
        for line in _wrap(draw, slot.title, tf, max_w):
            bbox = draw.textbbox((x, y), line, font=tf)
            base.alpha_composite(plate(*bbox))
            ImageDraw.Draw(base).text((x, y), line, font=tf, fill=dark)
            y += (bbox[3] - bbox[1]) + 16
        y += 12

    bf = _font(30)
    for b in slot.bullets:
        for i, line in enumerate(_wrap(draw, b, bf, max_w - 34)):
            txt = ("•  " if i == 0 else "    ") + line
            bbox = draw.textbbox((x, y), txt, font=bf)
            base.alpha_composite(plate(*bbox))
            ImageDraw.Draw(base).text((x, y), txt, font=bf, fill=dark)
            y += (bbox[3] - bbox[1]) + 12
        y += 10
        if y > h - pad:  # вышли за кадр — дальше не рисуем
            break

    out = io.BytesIO()
    base.convert("RGB").save(out, format="PNG")
    return out.getvalue()
