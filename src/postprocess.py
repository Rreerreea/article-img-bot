"""Постобработка: привести картинку к целевому размеру (TZ 7.5).

Модель отдаёт свои пропорции — приводим к нужным масштабом по
покрытию + центральный кроп (без искажения, без полей).
"""

from __future__ import annotations

import io

from PIL import Image


def normalize(data: bytes, target_size: tuple[int, int]) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    tw, th = target_size
    sw, sh = img.size

    scale = max(tw / sw, th / sh)  # cover: покрыть всю целевую область
    nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))
    img = img.resize((nw, nh), Image.LANCZOS)

    left = (nw - tw) // 2
    top = (nh - th) // 2
    img = img.crop((left, top, left + tw, top + th))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
