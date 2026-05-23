#!/bin/bash
# Двойной клик — поставит бота на этот мак. Терминал открыт не нужно.
cd "$(dirname "$0")"
clear
echo "=============================================="
echo "  Установка бота @tychyna_images_bot"
echo "=============================================="
echo ""

# Проверки прежде, чем дёргать сеть.
if ! command -v python3 >/dev/null; then
  echo "❌ Нет python3 — нужно поставить Xcode Command Line Tools."
  echo "   Открой Терминал и выполни:"
  echo ""
  echo "      xcode-select --install"
  echo ""
  echo "   После установки запусти эту установку снова."
  read -n 1 -s
  exit 1
fi
if [ ! -f .env ]; then
  echo "❌ Рядом с install.command нет файла .env с ключами."
  echo "   Попроси владельца прислать .env и положи в эту же папку."
  read -n 1 -s
  exit 1
fi

# Если в папке нет кода — клонируем с GitHub.
if [ ! -d src ]; then
  if ! command -v git >/dev/null; then
    echo "❌ Нет git — нужно поставить Xcode Command Line Tools."
    echo "   Открой Терминал и выполни:"
    echo ""
    echo "      xcode-select --install"
    echo ""
    read -n 1 -s
    exit 1
  fi
  echo "==> Скачиваю код бота с GitHub..."
  TMP="$(mktemp -d)"
  if ! git clone --depth 1 https://github.com/Rreerreea/article-img-bot.git "$TMP/repo" 2>&1; then
    echo "❌ Не получилось скачать с GitHub. Проверь интернет."
    read -n 1 -s
    exit 1
  fi
  shopt -s dotglob
  mv "$TMP/repo"/* .
  shopt -u dotglob
  rm -rf "$TMP"
fi

# Дальше — общий установщик (есть в репозитории).
bash install_mac.sh

echo ""
echo "Окно можно закрыть. (нажми любую клавишу)"
read -n 1 -s
