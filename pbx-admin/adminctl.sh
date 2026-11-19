#!/usr/bin/env bash
# Включение/выключение веб-админки мини-АТС (АТС продолжает работать)
# Использование: adminctl.sh on | off | status
F=/opt/pbx-admin/settings.json
case "$1" in
  on)     printf '{"admin_enabled": true}'  > $F; echo "Админка ВКЛЮЧЕНА";;
  off)    printf '{"admin_enabled": false}' > $F; echo "Админка ВЫКЛЮЧЕНА — сайт отдаёт 404";;
  status) grep -q true $F && echo "Админка включена" || echo "Админка выключена";;
  *) echo "Использование: adminctl.sh on|off|status";;
esac
