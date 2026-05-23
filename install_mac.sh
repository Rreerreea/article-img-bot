#!/bin/bash
# Установка бота на мак: venv + зависимости + автозапуск (LaunchAgent).
# Предполагается, что код уже в текущей папке (install.command выше делает клон).
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "==> Проверки"
command -v python3 >/dev/null || { echo "Нет python3. Поставь Xcode Command Line Tools: xcode-select --install"; exit 1; }
[ -f .env ] || { echo "ОШИБКА: нет файла .env с ключами рядом со скриптом."; exit 1; }
[ -d src ] || { echo "ОШИБКА: папки src/ нет — установка делается через install.command (он сам подтянет код)."; exit 1; }

echo "==> Окружение (venv + зависимости, ~1-2 мин)"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "==> Автозапуск при входе в систему (LaunchAgent)"
PLIST="$HOME/Library/LaunchAgents/com.tychyna.imagesbot.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.tychyna.imagesbot</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-i</string>
    <string>$DIR/.venv/bin/python</string>
    <string>-m</string>
    <string>src.telegram_bot</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/bot.log</string>
  <key>StandardErrorPath</key><string>$DIR/bot.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo ""
echo "✅ Готово. Бот запущен и будет сам стартовать при каждом входе в систему."
echo "   Логи: $DIR/bot.log"
echo "   Остановить: bash uninstall_mac.sh"
echo "   Подвох: мак не должен спать (закрытая крышка усыпляет — держи открытым/на питании)."
