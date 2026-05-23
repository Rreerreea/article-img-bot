#!/bin/bash
# Собирает мини-ZIP для друга: install.command + .env + FRIEND_SETUP.md
# Запуск: bash dist/pack_for_friend.sh
# Результат: ~/Desktop/article-img-bot-friend.zip

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HOME/Desktop/article-img-bot-friend"
ZIP="$OUT.zip"

rm -rf "$OUT" "$ZIP"
mkdir -p "$OUT"

cp "$ROOT/install.command"    "$OUT/install.command"
cp "$ROOT/dist/.env.friend"   "$OUT/.env"
cp "$ROOT/FRIEND_SETUP.md"    "$OUT/FRIEND_SETUP.md"
chmod +x "$OUT/install.command"

cd "$HOME/Desktop"
zip -r "$ZIP" "$(basename "$OUT")" >/dev/null
rm -rf "$OUT"

echo "✅ Готово: $ZIP"
echo "Отправь другу — пусть распакует на Рабочий стол и кликнет install.command."
