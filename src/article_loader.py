"""Загрузка статьи в плоский текст.

Вход (TZ 8.1): файл .docx/.md/.txt и ссылка.
- .docx — через mammoth → markdown (кроссплатформенно, работает на Linux);
- Google Docs — нативный txt-экспорт (чисто, без HTML-мусора);
- обычная web-страница и публичный Notion (notion.site) — HTML→текст;
- приватный Notion требует интеграцию-токен — это зависит от друга,
  сознательно вне scope (см. README).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

SUPPORTED_EXT = {".txt", ".md", ".docx"}
_GOOGLE_DOC = re.compile(r"docs\.google\.com/document/d/([A-Za-z0-9_-]+)")
# «li» сюда НЕ входит: пункты обрабатываются в starttag как «• »,
# а закрывающий перенос создал бы пустую строку между буллетами и
# обрывал бы блок в парсере. Граница списка задаётся соседними блоками.
_BLOCK_TAGS = {
    "p", "div", "br", "tr", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6",
}


def load_article(path: str | Path) -> str:
    p = Path(path)
    ext = p.suffix.lower()

    if not p.exists():
        raise FileNotFoundError(f"Файл статьи не найден: {p}")

    if ext in {".txt", ".md"}:
        return p.read_text(encoding="utf-8")

    if ext == ".docx":
        # mammoth → HTML (markdown теряет структуру таблиц). Дальше HTML
        # конвертим в наш «плоский» формат с маркерами картинок и
        # markdown-таблицами — парсер их понимает.
        import mammoth

        with open(p, "rb") as fh:
            result = mammoth.convert_to_html(fh)
        text = _html_to_flat(result.value or "")
        if not text.strip():
            raise RuntimeError(
                "Файл .docx прочитался, но текста внутри не нашлось "
                "(возможно, картинки-сканы без OCR или сложный layout). "
                "Попробуй сохранить как .md или .txt."
            )
        return text

    raise ValueError(
        f"Неподдерживаемое расширение: {ext}. Допустимы: {sorted(SUPPORTED_EXT)}"
    )


def load_from_url(url: str) -> str:
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError(f"Ожидалась http(s)-ссылка, получено: {url!r}")

    m = _GOOGLE_DOC.search(url)
    if m:
        export = (
            f"https://docs.google.com/document/d/{m.group(1)}/export?format=txt"
        )
        return _http_get(export)

    # Обычная страница / публичный Notion — это HTML, чистим до текста.
    return _html_to_text(_http_get(url))


# Папка для inline-картинок из .docx. /tmp очищается ОС периодически.
INLINE_REFS_DIR = Path("/tmp/article-img-bot-inline-refs")


def _save_inline_image(b64_data: str, mime: str) -> Path:
    """Сохраняет одну base64-картинку из docx как файл, имя по хэшу
    содержимого (дедупликация). Возвращает абсолютный путь."""
    import base64
    import hashlib

    INLINE_REFS_DIR.mkdir(parents=True, exist_ok=True)
    data = base64.b64decode(b64_data)
    ext = ".jpg"
    m = mime.lower()
    if "png" in m:
        ext = ".png"
    elif "webp" in m:
        ext = ".webp"
    elif "gif" in m:
        ext = ".gif"
    digest = hashlib.sha1(data).hexdigest()[:16]
    path = INLINE_REFS_DIR / f"inline_{digest}{ext}"
    if not path.exists():
        path.write_bytes(data)
    return path


def _html_to_flat(html: str) -> str:
    """HTML из mammoth → плоский текст для парсера.

    Что делает:
    - `<img src="data:...">` → сохраняет на диск, оставляет
      `[INLINE_IMAGE:/abs/path]` (как было в markdown-режиме)
    - `<table>` → markdown-таблица `| col | col |` с разделителем —
      парсер её распознаёт под маркером Рис.
    - `<p>`/`<br>` → переводы строк
    - Остальные теги → удаление, текст сохраняется
    """
    # 1) Images first — до удаления тегов.
    def _img_repl(m: re.Match) -> str:
        mime = m.group("mime")
        b64 = m.group("b64")
        try:
            path = _save_inline_image(b64, mime)
        except Exception:  # noqa: BLE001
            return ""
        return f"\n\n[INLINE_IMAGE:{path}]\n\n"

    html = re.sub(
        r"<img[^>]*src=\"data:(?P<mime>image/[^;]+);base64,(?P<b64>[^\"]+)\"[^>]*/?>",
        _img_repl,
        html,
    )
    # Картинки с обычным URL → пустая строка (URL-картинки пока не
    # скачиваем синхронно).
    html = re.sub(r"<img[^>]*/?>", "", html)

    # 2) Таблицы → markdown. Каждая <tr> = ряд, ячейки <td>/<th> = столбцы.
    def _table_repl(m: re.Match) -> str:
        body = m.group(0)
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, flags=re.DOTALL)
        if not rows:
            return ""
        md_rows = []
        first = True
        for row in rows:
            cells = re.findall(r"<(?:td|th)[^>]*>(.*?)</(?:td|th)>", row, flags=re.DOTALL)
            clean_cells = []
            for c in cells:
                c = re.sub(r"<[^>]+>", "", c)  # внутренние теги (p, strong)
                c = re.sub(r"\s+", " ", c).strip()
                clean_cells.append(c)
            if not clean_cells:
                continue
            md_rows.append("| " + " | ".join(clean_cells) + " |")
            if first:
                md_rows.append("|" + "|".join([" --- "] * len(clean_cells)) + "|")
                first = False
        return "\n\n" + "\n".join(md_rows) + "\n\n"

    html = re.sub(
        r"<table[^>]*>.*?</table>", _table_repl, html, flags=re.DOTALL
    )

    # 3) Структурные блоки → переводы строк.
    html = re.sub(r"<(?:br|hr)\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</(?:p|div|h[1-6]|li)>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<li[^>]*>", "• ", html, flags=re.IGNORECASE)

    # 4) Остальные теги вырезаем.
    html = re.sub(r"<[^>]+>", "", html)

    # 5) HTML entities.
    import html as html_mod
    text = html_mod.unescape(html)

    # 6) Схлопываем лишние пустые строки.
    text = re.sub(r"\n[ \t]*\n[ \t\n]*", "\n\n", text)
    return text.strip()


def _clean_markdown(text: str) -> str:
    """Чистит mammoth-разметку до плоского текста для нашего парсера.

    - data:image base64 → сохраняем на диск, оставляем маркер
      [INLINE_IMAGE:/abs/path], чтобы парсер привязал к ближайшему Рис.
    - HTML-тэги (anchor'ы и др.) → удаляем
    - Markdown-экранирования `\\.`, `\\-` → возвращаем символ
    - Жирный/курсив `**`/`__`/`*`/`_` → снимаем
    """

    def _img_to_marker(match: re.Match) -> str:
        mime = match.group("mime")
        b64 = match.group("b64")
        try:
            path = _save_inline_image(b64, mime)
        except Exception:  # noqa: BLE001 — на криво-закодированный image
            return ""
        return f"\n[INLINE_IMAGE:{path}]\n"

    # ![](data:image/jpeg;base64,...) → маркер с путём к файлу.
    text = re.sub(
        r"!\[[^\]]*\]\(data:(?P<mime>image/[^;]+);base64,(?P<b64>[A-Za-z0-9+/=]+)\)",
        _img_to_marker,
        text,
    )
    # Прочие inline картинки ![](url) — оставим без следа, у нас нет
    # стабильного способа сохранить URL-картинку синхронно в этом этапе.
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    # HTML тэги
    text = re.sub(r"<a[^>]*>(.*?)</a>", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"</?[a-z][^>]*>", "", text)
    # Markdown escape backslashes: \. \- \( \) \[ \] ...
    text = re.sub(r"\\([.,!?\-()\[\]{}*_<>])", r"\1", text)
    # **text** / __text__ — снимаем
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.DOTALL)
    # *text* / _text_ (одиночные) — снимаем, но не трогаем * как буллет
    # (он в начале строки, после него пробел; здесь — внутри строки)
    text = re.sub(r"(?<![*\w])\*([^\n*]+?)\*(?![*\w])", r"\1", text)
    text = re.sub(r"(?<![_\w])_([^\n_]+?)_(?![_\w])", r"\1", text)
    return text


def _http_get(url: str) -> str:
    """Сетевой вызов вынесен отдельно — точка подмены в тестах."""
    import httpx

    resp = httpx.get(url, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    return resp.text


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        elif tag == "li":
            self.chunks.append("\n• ")  # чтобы парсер увидел буллет
        elif tag in _BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.chunks.append(data)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.chunks)
    # Схлопываем лишние пустые строки, чтобы парсер «Рис.»-блоков не путался.
    return re.sub(r"\n[ \t]*\n[ \t\n]*", "\n\n", text).strip()
