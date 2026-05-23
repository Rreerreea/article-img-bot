# Лёгкий образ для дешёвого/бесплатного хостинга (TZ 7.7г).
# Linux-контейнер: .docx через textutil недоступен — вход в проде
# Google Doc-ссылка или .md/.txt (см. README, раздел «Прод»).
FROM python:3.12-slim

WORKDIR /app

# Шрифт с кириллицей для текст-слоя инфографики (гибрид 10.A).
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Бот читает .env / переменные окружения хостинга.
CMD ["python", "-m", "src.telegram_bot"]
