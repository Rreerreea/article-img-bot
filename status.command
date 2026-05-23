#!/bin/bash
# Двойной клик — проверить, работает ли бот.
cd "$(dirname "$0")"
clear
if pgrep -f "src.telegram_bot" >/dev/null; then
  echo "✅ Бот РАБОТАЕТ. Пиши ему @tychyna_images_bot в Телеграме."
else
  echo "❌ Бот не запущен. Двойной клик по install.command."
fi
echo ""
echo "--- последние строки лога ---"
tail -n 8 bot.log 2>/dev/null || echo "(лога пока нет)"
echo ""
echo "Окно можно закрыть. (нажми любую клавишу)"
read -n 1 -s
