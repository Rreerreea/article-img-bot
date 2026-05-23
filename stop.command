#!/bin/bash
# Двойной клик — остановить бота и убрать автозапуск.
cd "$(dirname "$0")"
clear
echo "Останавливаю бота…"
bash uninstall_mac.sh
echo ""
echo "Готово. Окно можно закрыть. (нажми любую клавишу)"
read -n 1 -s
