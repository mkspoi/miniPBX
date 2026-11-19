#!/usr/bin/env bash
#
# miniPBX — установка "в одну команду" на чистый Ubuntu 22/24/26
#
#   bash install.sh                              # интерактивно
#   PBX_DOMAIN=pbx.example.com bash install.sh   # без вопросов
#
# Параметры через переменные окружения:
#   PBX_DOMAIN    домен админки (пусто = HTTP-only, без certbot)
#   PBX_SIP_PORT  SIP-порт (по умолчанию 21967, нестандартный — от сканеров)
#   PBX_RTP_START / PBX_RTP_END  диапазон RTP (по умолчанию 10000-10050)
#   PBX_EXT       имя внутреннего номера (по умолчанию phone1)
#   PBX_EXT_PWD   пароль SIP (пусто = сгенерировать)
#   PBX_ADMIN_PWD пароль админки (пусто = сгенерировать)
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SIP_PORT="${PBX_SIP_PORT:-21967}"
RTP_START="${PBX_RTP_START:-10000}"
RTP_END="${PBX_RTP_END:-10050}"
EXT="${PBX_EXT:-phone1}"
EXT_PWD="${PBX_EXT_PWD:-$(openssl rand -hex 6)}"
ADMIN_PWD="${PBX_ADMIN_PWD:-$(openssl rand -hex 6)}"
DOMAIN="${PBX_DOMAIN:-}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Запустите от root (sudo bash install.sh)"; exit 1
fi
command -v openssl >/dev/null || { apt-get update -qq; apt-get install -y -qq openssl; }

if [ -z "$DOMAIN" ] && [ -t 0 ]; then
  read -rp "Домен для админки (например pbx.example.com, Enter = пропустить HTTPS): " DOMAIN
fi

echo "== 1/7. Установка пакетов (asterisk, nginx, python3, certbot)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq asterisk nginx python3 certbot python3-certbot-nginx ufw >/dev/null

echo "== 2/7. Конфиги Asterisk (порт $SIP_PORT, RTP $RTP_START-$RTP_END, внутренний номер $EXT)..."
install -m 644 "$REPO_DIR/asterisk/extensions.conf" /etc/asterisk/extensions.conf
sed -i "s/masterlab1245/$EXT/g" /etc/asterisk/extensions.conf

install -m 644 "$REPO_DIR/asterisk/pjsip.conf" /etc/asterisk/pjsip.conf
sed -i "s/21967/$SIP_PORT/g" /etc/asterisk/pjsip.conf

install -m 644 "$REPO_DIR/asterisk/pjsip_phones.conf.example" /etc/asterisk/pjsip_phones.conf
sed -i "s/masterlab1245/$EXT/g; s/CHANGE_ME_SIP_PASSWORD/$EXT_PWD/" /etc/asterisk/pjsip_phones.conf

cat > /etc/asterisk/rtp.conf <<EOF
[general]
rtpstart=$RTP_START
rtpend=$RTP_END
EOF
chown asterisk:asterisk /etc/asterisk/*.conf 2>/dev/null || true

# транк на старте выключен — настраивается из веб-админки
printf '; Транк ещё не настроен — откройте админку и введите данные провайдера\n' > /etc/asterisk/pjsip_trunk.conf

echo "== 3/7. Каталог для записей разговоров..."
mkdir -p /var/spool/asterisk/monitor
chown -R asterisk:asterisk /var/spool/asterisk/monitor

echo "== 4/7. Веб-админка (/opt/pbx-admin)..."
mkdir -p /opt/pbx-admin
install -m 644 "$REPO_DIR/pbx-admin/app.py" /opt/pbx-admin/app.py
cat > /opt/pbx-admin/config.json <<EOF
{
  "admin_user": "admin",
  "admin_password": "$ADMIN_PWD",
  "listen_host": "127.0.0.1",
  "listen_port": 8090,
  "sip_port": $SIP_PORT,
  "extension": "$EXT",
  "extension_password": "$EXT_PWD",
  "server_ip": "$(curl -s -4 ifconfig.me 2>/dev/null || echo 127.0.0.1)",
  "server_host": "${DOMAIN:-localhost}"
}
EOF
install -m 644 "$REPO_DIR/pbx-admin/pbx-admin.service" /etc/systemd/system/pbx-admin.service
printf '{"admin_enabled": true}' > /opt/pbx-admin/settings.json
install -m 700 "$REPO_DIR/pbx-admin/adminctl.sh" /opt/pbx-admin/adminctl.sh
systemctl daemon-reload
systemctl enable --now asterisk pbx-admin

echo "== 5/7. Файрвол (SSH, HTTP/HTTPS, SIP/$SIP_PORT UDP, RTP $RTP_START-$RTP_END)..."
ufw allow 22/tcp  >/dev/null 2>&1 || ufw allow 22/tcp
ufw allow 80/tcp  >/dev/null 2>&1 || ufw allow 80/tcp
ufw allow 443/tcp >/dev/null 2>&1 || ufw allow 443/tcp
ufw allow "$SIP_PORT/udp"
ufw allow "$RTP_START:$RTP_END/udp"
yes | ufw enable >/dev/null 2>&1 || true

echo "== 6/7. nginx (админка на скрытом пути /root/)..."
sed "s/pbx.example.com/${DOMAIN:-_}/g" "$REPO_DIR/nginx/pbx-nossl.conf" > /etc/nginx/sites-available/pbx.conf
ln -sf /etc/nginx/sites-available/pbx.conf /etc/nginx/sites-enabled/pbx.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t >/dev/null && systemctl reload nginx

echo "== 7/7. HTTPS (certbot)..."
HAVE_HTTPS=no
if [ -n "$DOMAIN" ]; then
  MYIP="$(curl -s -4 ifconfig.me 2>/dev/null)"
  if host -t A "$DOMAIN" 2>/dev/null | grep -q "$MYIP"; then
    if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos          --register-unsafely-without-email --redirect >/dev/null 2>&1; then
      HAVE_HTTPS=yes
    fi
  else
    echo "   Внимание: домен $DOMAIN не указывает на этот сервер ($MYIP) — HTTP-only."
  fi
else
  echo "   Домен не задан — HTTP-only (админка всё равно закрыта паролем)."
fi

echo
echo "===================== ГОТОВО ====================="
if [ "$HAVE_HTTPS" = yes ]; then SCHEME=https; else SCHEME=http; fi
echo "Админка:        $SCHEME://${DOMAIN:-$MYIP}/root/"
echo "Логин админки:  admin"
echo "Пароль админки: $ADMIN_PWD"
echo
echo "SIP-телефон:"
echo "  Сервер: ${DOMAIN:-$MYIP}   порт: $SIP_PORT (UDP)"
echo "  Логин:  $EXT"
echo "  Пароль: $EXT_PWD"
echo "  Кодеки: только ALAW"
echo
echo "Данные SIP-провайдера вводятся в админке (форма Транк)."
echo "Записи разговоров выключены; включить: RECORD_ENABLED => YES в extensions.conf."
echo "==================================================="

