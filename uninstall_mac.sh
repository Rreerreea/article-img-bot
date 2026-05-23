#!/bin/bash
# Остановить бота и убрать автозапуск. Запуск: bash uninstall_mac.sh
PLIST="$HOME/Library/LaunchAgents/com.tychyna.imagesbot.plist"
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
pkill -f "src.telegram_bot" 2>/dev/null || true
echo "✅ Бот остановлен, автозапуск убран. Файлы проекта не тронуты."
