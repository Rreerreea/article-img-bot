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
        # mammoth → markdown. Кроссплатформенно, не нужен macOS-textutil.
        # Markdown с экранированиями (*Fig\.* и т.д.) парсер не понимает —
        # чистим разметку до плоского текста.
        import mammoth

        with open(p, "rb") as fh:
            result = mammoth.convert_to_markdown(fh)
        return _clean_markdown(result.value)

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


def _clean_markdown(text: str) -> str:
    """Чистит mammoth-разметку до плоского текста для нашего парсера.

    - data:image base64 (огромные) → удаляем
    - HTML-тэги (anchor'ы и др.) → удаляем
    - Markdown-экранирования `\\.`, `\\-` → возвращаем символ
    - Жирный/курсив `**`/`__`/`*`/`_` → снимаем
    """
    # ![](data:image/jpeg;base64,...) — иногда занимает 99% файла
    text = re.sub(r"!\[[^\]]*\]\(data:[^)]+\)", "", text)
    # Прочие inline картинки ![](url) — оставим хотя бы маркер пустым
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
