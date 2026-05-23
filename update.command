#!/bin/bash
# Двойной клик — подтянуть свежую версию бота из github и перезапустить.
cd "$(dirname "$0")"
clear
echo "=============================================="
echo "  Обновление бота @tychyna_images_bot"
echo "=============================================="
echo ""

if [ ! -d .git ]; then
  echo "❌ Это не git-папка. Переустанови бот заново через install.command."
  echo ""
  echo "(нажми любую клавишу)"
  read -n 1 -s
  exit 1
fi

echo "==> Тяну свежий код с github..."
if ! git pull --ff-only origin main; then
  echo ""
  echo "⚠️ git pull не удался. Возможные причины:"
  echo "   — нет интернета"
  echo "   — кто-то правил файлы локально (восстанови: git reset --hard origin/main)"
  echo ""
  echo "(нажми любую клавишу)"
  read -n 1 -s
  exit 1
fi

echo ""
echo "==> Обновляю зависимости..."
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo ""
echo "==> Перезапускаю бота..."
PLIST="$HOME/Library/LaunchAgents/com.tychyna.imagesbot.plist"
if [ -f "$PLIST" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  sleep 2
else
  echo "   (LaunchAgent не найден — запусти install.command, чтобы поставить автозапуск)"
fi

if pgrep -f "src.telegram_bot" >/dev/null; then
  echo ""
  echo "✅ Готово. Бот обновлён и работает."
else
  echo ""
  echo "⚠️ Бот не запустился. Лог: $(pwd)/bot.log"
fi

echo ""
echo "Окно можно закрыть. (нажми любую клавишу)"
read -n 1 -s
