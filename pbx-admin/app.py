#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Мини-админка PBX (Asterisk) — pbx.example.com. Python stdlib only."""
import json, re, subprocess, html, os, datetime, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

def load_cfg():
    try:
        return json.load(open('/opt/pbx-admin/config.json'))
    except Exception:
        return {'admin_user': 'admin', 'admin_password': '', 'listen_host': '127.0.0.1', 'listen_port': 8090, 'sip_port': 21967, 'extension': '', 'extension_password': '', 'server_ip': '', 'server_host': ''}
CFG = load_cfg()
STATE_FILE = '/opt/pbx-admin/trunk.json'
TRUNK_FILE = '/etc/asterisk/pjsip_trunk.conf'
PHONES_FILE = '/etc/asterisk/pjsip_phones.conf'
PJSIP_FILE = '/etc/asterisk/pjsip.conf'
EXTENSIONS_FILE = '/etc/asterisk/extensions.conf'
REC_DIR = '/var/spool/asterisk/monitor'
AST_TIMEOUT = 30
FAILS_FILE = '/opt/pbx-admin/auth_fails.json'
MAX_FAILS = 3
AST_TIMEOUT = 30
BAN_SECS = 1800
SETTINGS_FILE = '/opt/pbx-admin/settings.json'

def load_state():
    try:
        return json.load(open(STATE_FILE, encoding='utf-8'))
    except Exception:
        try:
            t = open(TRUNK_FILE, encoding='utf-8').read()
            def m(pat):
                r = re.search(pat, t)
                return r.group(1).strip() if r else ''
            return {'host': m(r'contact=sip:([A-Za-z0-9.\-]+):'),
                    'username': m(r'username=([\w\-\.\+@]+)'),
                    'password': m(r'password=([^\s]+)'),
                    'enabled': 'trunk' in t}
        except Exception:
            return {'host': '', 'username': '', 'password': '', 'enabled': False}

def save_state(st):
    json.dump(st, open(STATE_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

def trunk_conf(host, user, pwd):
    return f'''; Управляется админ-панелью — вручную не править
[trunk]
type=endpoint
transport=transport-udp
context=incoming
disallow=all
allow=alaw,ulaw
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
outbound_auth=trunk-auth
from_user={user}
from_domain={host}
aors=trunk-aor

[trunk-auth]
type=auth
auth_type=digest
username={user}
password={pwd}

[trunk-aor]
type=aor
contact=sip:{host}:5060

[trunk-reg]
type=registration
transport=transport-udp
outbound_auth=trunk-auth
server_uri=sip:{host}:5060
client_uri=sip:{user}@{host}
retry_interval=60
forbidden_retry_interval=300
expiration=120

[trunk-identify]
type=identify
endpoint=trunk
match=37.139.38.0/24
'''

def phones_conf(cfg):
    return f'''; Управляется админ-панелью — вручную не править
[{cfg['extension']}]
type=endpoint
context=from-internal
transport=transport-udp
disallow=all
allow=alaw
auth={cfg['extension']}
aors={cfg['extension']}
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
dtmf_mode=rfc4733

[{cfg['extension']}]
type=auth
auth_type=digest
username={cfg['extension']}
password={cfg['extension_password']}

[{cfg['extension']}]
type=aor
max_contacts=2
remove_existing=yes
qualify_frequency=45
qualify_timeout=3
'''

TRUNK_OFF = '''; Транк ОТКЛЮЧЁН в админ-панели pbx.example.com
; Настройки сохранены и будут восстановлены при включении.
'''

def apply_trunk(st):
    if st.get('enabled') and st.get('host') and st.get('username'):
        open(TRUNK_FILE, 'w', encoding='utf-8').write(
            trunk_conf(st['host'], st['username'], st['password']))
    else:
        try:
            ast('pjsip send unregister trunk-reg')
        except Exception:
            pass
        open(TRUNK_FILE, 'w', encoding='utf-8').write(TRUNK_OFF)
    ast('core reload')

def ast(*args):
    try:
        r = subprocess.run(['asterisk', '-rx', ' '.join(args)], capture_output=True, text=True, timeout=AST_TIMEOUT)
        return (r.stdout or '') + (r.stderr or '')
    except Exception as e:
        return 'Ошибка: ' + str(e)

def esc(s):
    return html.escape(str(s or ''))

def list_records():
    try:
        fs = []
        for f in os.listdir(REC_DIR):
            if f.endswith('.wav') and re.fullmatch(r'[\w\-.]+', f):
                p = os.path.join(REC_DIR, f)
                fs.append((f, os.path.getmtime(p), os.path.getsize(p)))
        fs.sort(key=lambda x: -x[1])
        return fs[:50]
    except Exception:
        return []

def rec_row(f, mt, size):
    m = re.match(r'(in|out)-([0-9]*)-(.+)-([0-9]+)-([\w]+)\.wav', f)
    if m:
        direction = '📞 исходящий' if m.group(1) == 'out' else '☎️ входящий'
        number = m.group(2) or '—'
        name = esc(direction) + ' · <code>' + esc(number) + '</code>'
    else:
        name = esc(f)
    dt = datetime.datetime.fromtimestamp(mt).strftime('%d.%m.%Y %H:%M:%S')
    mb = '%.1f' % (size / 1048576.0)
    return ('<tr><td>' + dt + '</td><td>' + name + '</td>'
            '<td>' + mb + ' МБ</td><td><audio controls preload=none src="rec?f=' + esc(f) + '"></audio></td></tr>')

def load_fails():
    try:
        return json.load(open(FAILS_FILE))
    except Exception:
        return {}

def save_fails(d):
    try:
        json.dump(d, open(FAILS_FILE, 'w'))
    except Exception:
        pass

def load_settings():
    try:
        return json.load(open(SETTINGS_FILE))
    except Exception:
        return {}

def admin_enabled():
    # выключатель админки: {"admin_enabled": false} в settings.json — сайт отдаёт 404
    return load_settings().get('admin_enabled', True)

PAGE = '''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PBX — pbx.example.com</title><style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{font:15px/1.5 system-ui,sans-serif;background:#141a21;color:#dfe6ee;margin:0;padding:24px;max-width:900px;margin:auto}
h1{font-size:22px}h2{font-size:17px;margin:28px 0 10px;color:#9fe8cd}
.card{background:#1c252e;border:1px solid #2a3642;border-radius:10px;padding:16px 20px;margin:14px 0}
label{display:block;margin:10px 0 4px;font-size:13px;color:#9aa7b2}
input{width:100%;padding:9px 10px;border-radius:8px;border:1px solid #2a3642;background:#141a21;color:#dfe6ee;font-size:15px}
button{margin-top:16px;background:#00a76f;color:#06120d;border:0;border-radius:8px;padding:11px 22px;font-size:15px;font-weight:600;cursor:pointer}
button:hover{background:#00c07f}
button.danger{background:#b3541e;color:#fff}
button.danger:hover{background:#d06529}
pre{background:#10161c;border:1px solid #2a3642;border-radius:8px;padding:12px;overflow:auto;font-size:13px;white-space:pre-wrap}
code{background:#10161c;padding:2px 6px;border-radius:6px}
.ok{color:#00c07f}.bad{color:#ff7d6b}
table{border-collapse:collapse;width:100%}td{padding:4px 8px;border-top:1px solid #2a3642;font-size:14px}
td:first-child{color:#9aa7b2;width:46%}
#rectable td{border-top:1px solid #2a3642;font-size:14px;padding:6px 8px;vertical-align:middle}
#rectable td:nth-child(1){color:#9aa7b2;white-space:nowrap;width:150px}
#rectable td:nth-child(2){white-space:nowrap}
#rectable td:nth-child(3){color:#9aa7b2;white-space:nowrap;width:70px}
#rectable td:nth-child(4){width:42%}
audio{width:100%;height:32px}
.msg{padding:12px 16px;border-radius:8px;margin-bottom:14px}
.msg.ok{background:#0d2b21;color:#7ff0c0}.msg.bad{background:#2b1410;color:#ffb0a3}
.st-on{color:#00c07f;font-weight:600}.st-off{color:#ff7d6b;font-weight:600}
</style></head><body>
<h1>☎️ Мини-АТС · pbx.example.com</h1>
__MSG__
<h2>Транк SIP-провайдера</h2>
<div class="card">
<p>Состояние: __STATE__
__TOGGLE__
<form method="post" action="save">
<label>SIP-сервер провайдера (домен или IP, например sip.provider.ru)</label>
<input name="host" value="__HOST__" placeholder="sip.provider.ru" required>
<label>Логин (username) SIP-провайдера</label>
<input name="username" value="__USER__" placeholder="ваш логин" required>
<label>Пароль SIP-провайдера</label>
<input name="password" type="password" value="__PWD__" placeholder="пароль" required>
<button type="submit">Сохранить и применить</button>
</form>
</div>
<h2>Настройки для телефона</h2>
<div class="card"><table>
<tr><td>Сервер (SIP-прокси / домен)</td><td><code>__SERVER__</code> (или IP <code>__IP__</code>)</td></tr>
<tr><td>Порт</td><td><code>__PORT__</code> (UDP, нестандартный — от ботов)</td></tr>
<tr><td>Имя пользователя / внутренний номер</td><td><code>__EXT__</code></td></tr>
<tr><td>Пароль</td><td><code>__EXTPWD__</code></td></tr>
<tr><td>Кодеки</td><td>alaw, ulaw, gsm</td></tr>
</table></div>
<h2>Настройки (можно менять здесь)</h2>
<div class="card">
<form method="post" action="/settings">
<label>Имя пользователя SIP / внутренний номер</label>
<input name="extension" value="__EXTSET__" required>
<label>Пароль SIP-аккаунта</label>
<input name="extension_password" value="__EXTPWDSET__" required>
<label>SIP-порт (UDP; при смене АТС перезапустится, откроется порт в файрволе)</label>
<input name="sip_port" value="__PORTSET__" required>
<label>Логин админки</label>
<input name="admin_user" value="__USERSET__" required>
<label>Новый пароль админки (пусто = не менять)</label>
<input name="admin_password" type="password" placeholder="оставьте пустым, чтобы не менять">
<button type="submit">Сохранить настройки</button>
</form>
<p style="color:#9aa7b2;font-size:13px;margin-bottom:0">После смены логина/пароля/порта перенастройте софтфон новыми значениями. Кодек: только ALAW.</p>
</div>
<h2>Отключение админки</h2>
<div class="card">
<p>Поставив галочку и нажав кнопку, вы полностью отключите эту панель: сайт начнёт отдавать 404,
как будто его не существует. Включить обратно можно будет только на сервере командой
<code>/opt/pbx-admin/adminctl.sh on</code> (АТС и звонки продолжат работать).</p>
<form method="post" action="/disable" onsubmit="return confirm('Точно отключить админку? Включить обратно можно будет только через SSH на сервере!');">
<label><input type="checkbox" name="sure" value="1" required style="width:auto"> Да, отключить админку навсегда (до ручного включения)</label>
<button class="danger" type="submit">Отключить админку</button>
</form>
</div>
<h2>Статус</h2>
<div class="card"><pre>__STATUS__</pre></div>
<h2>Записи разговоров</h2>
<div class="card"><table id="rectable">
<tr><td colspan="4" style="border:0;padding:0 0 6px">Все входящие и исходящие через транк записываются. Последние 50.</td></tr>
__RECS__
</table></div>
</body></html>'''

class H(BaseHTTPRequestHandler):
    def client_ip(self):
        # реальный IP берём из nginx (X-Real-IP), прямой доступ к 8090 закрыт localhost'ом
        return self.headers.get('X-Real-IP') or self.client_address[0]

    def ban_check(self):
        ip = self.client_ip()
        fails = load_fails()
        ent = fails.get(ip)
        if ent and ent.get('ban_until', 0) > time.time():
            left = int((ent['ban_until'] - time.time()) // 60) + 1
            self.send_response(429)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Retry-After', str(int(ent['ban_until'] - time.time())))
            self.end_headers()
            try:
                self.wfile.write(('Слишком много неудачных попыток входа. Подождите %d мин.' % left).encode())
            except Exception:
                pass
            return True
        return False

    def do_auth(self):
        import base64
        if self.ban_check():
            return False
        ip = self.client_ip()
        h = self.headers.get('Authorization', '')
        ok = False
        if h.startswith('Basic '):
            try:
                u, p = base64.b64decode(h.split()[1]).decode().split(':', 1)
                cfg_now = load_cfg()
                if u == cfg_now['admin_user'] and p == cfg_now['admin_password']:
                    ok = True
            except Exception:
                pass
        if ok:
            fails = load_fails()
            if ip in fails:
                del fails[ip]
                save_fails(fails)
            return True
        # неверный логин/пароль
        fails = load_fails()
        ent = fails.get(ip) or {}
        cnt = ent.get('count', 0) + 1
        if cnt >= MAX_FAILS:
            fails[ip] = {'ban_until': time.time() + BAN_SECS, 'count': 0}
            save_fails(fails)
            self.ban_check()
        else:
            fails[ip] = {'count': cnt, 'ban_until': 0}
            save_fails(fails)
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="PBX admin"')
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write('Требуется авторизация'.encode())
        return False

    def render(self, msg=''):
        st = load_state()
        cfg = load_cfg()
        status = ast('pjsip show registrations') + '\n' + ast('pjsip show contacts')
        if st.get('enabled'):
            state = '<span class="st-on">● Транк включён</span>'
            toggle = ('<form method="post" action="toggle">'
                      '<input type="hidden" name="action" value="off">'
                      '<button class="danger" type="submit">Отключить транк</button></form>')
        else:
            state = '<span class="st-off">○ Транк отключён — исходящие звонки и входящие с провайдера не работают</span>'
            toggle = ('<form method="post" action="/toggle">'
                      '<input type="hidden" name="action" value="on">'
                      '<button type="submit" style="display:block">Включить транк</button></form>')
        recs = list_records()
        recs_html = ''.join(rec_row(*r) for r in recs) if recs else '<tr><td colspan="4">Пока нет записей.</td></tr>'
        page = (PAGE.replace('__MSG__', msg)
                    .replace('__STATE__', state).replace('__TOGGLE__', toggle)
                    .replace('__HOST__', esc(st.get('host', ''))).replace('__USER__', esc(st.get('username', '')))
                    .replace('__PWD__', esc(st.get('password', '')))
                    .replace('__SERVER__', esc(cfg['server_host'])).replace('__IP__', esc(cfg['server_ip']))
                    .replace('__PORT__', str(cfg['sip_port'])).replace('__EXT__', esc(cfg['extension']))
                    .replace('__EXTPWD__', esc(cfg['extension_password']))
                    .replace('__STATUS__', esc(status))
                    .replace('__EXTSET__', esc(cfg.get('extension', '')))
                    .replace('__EXTPWDSET__', esc(cfg.get('extension_password', '')))
                    .replace('__PORTSET__', str(cfg.get('sip_port', '')))
                    .replace('__USERSET__', esc(cfg.get('admin_user', '')))
                    .replace('__RECS__', recs_html))
        body = page.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not admin_enabled():
            self.send_response(404)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'Not Found')
            return
        if not self.do_auth():
            return
        if self.path.startswith('/rec?'):
            q = parse_qs(self.path.split('?', 1)[1])
            f = (q.get('f', [''])[0])
            if re.fullmatch(r'[\w\-.]+\.wav', f) and os.path.isfile(os.path.join(REC_DIR, f)):
                data = open(os.path.join(REC_DIR, f), 'rb').read()
                self.send_response(200)
                self.send_header('Content-Type', 'audio/wav')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write(b'Not found')
            return
        self.render()

    def do_POST(self):
        if not admin_enabled():
            self.send_response(404)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'Not Found')
            return
        if not self.do_auth():
            return
        n = int(self.headers.get('Content-Length', 0))
        q = parse_qs(self.rfile.read(n).decode('utf-8'))
        if self.path == '/save':
            host = (q.get('host', [''])[0]).strip()
            user = (q.get('username', [''])[0]).strip()
            pwd = (q.get('password', [''])[0]).strip()
            if not re.fullmatch(r'[A-Za-z0-9.\-]+', host or ''):
                self.render('<div class="msg bad">Неверный адрес SIP-сервера (только домен/IP, латиница, точки, дефисы).</div>'); return
            if not user or not pwd or any(c in user + pwd for c in ' \t\r\n'):
                self.render('<div class="msg bad">Логин и пароль обязательны и не должны содержать пробелов.</div>'); return
            st = load_state()
            st.update({'host': host, 'username': user, 'password': pwd, 'enabled': True})
            save_state(st); apply_trunk(st)
            self.render('<div class="msg ok">Настройки транка сохранены, Asterisk перезагружен. Статус регистрации ниже.</div>')
        elif self.path == '/toggle':
            st = load_state()
            action = q.get('action', [''])[0]
            if action == 'off':
                st['enabled'] = False
                save_state(st); apply_trunk(st)
                self.render('<div class="msg ok">Транк отключён: регистрация у провайдера остановлена, исходящие заблокированы.</div>')
            elif action == 'on':
                if not (st.get('host') and st.get('username') and st.get('password')):
                    self.render('<div class="msg bad">Нет сохранённых настроек транка — сначала заполните форму ниже.</div>'); return
                st['enabled'] = True
                save_state(st); apply_trunk(st)
                self.render('<div class="msg ok">Транк включён, настройки восстановлены, Asterisk перезагружен.</div>')
            else:
                self.render()
        elif self.path == '/settings':
            ext = (q.get('extension', [''])[0]).strip()
            extpwd = (q.get('extension_password', [''])[0]).strip()
            port = (q.get('sip_port', [''])[0]).strip()
            auser = (q.get('admin_user', [''])[0]).strip()
            apwd = (q.get('admin_password', [''])[0]).strip()
            if not re.fullmatch(r'[A-Za-z0-9_\-]{2,32}', ext or ''):
                self.render('<div class="msg bad">Имя SIP-пользователя: 2-32 символа, латиница/цифры/_-.</div>'); return
            if not extpwd or ' ' in extpwd:
                self.render('<div class="msg bad">Пароль SIP обязателен и без пробелов.</div>'); return
            if not port.isdigit() or not (1024 <= int(port) <= 65535):
                self.render('<div class="msg bad">Порт: число 1024-65535.</div>'); return
            if not re.fullmatch(r'[A-Za-z0-9_\-]{2,32}', auser or ''):
                self.render('<div class="msg bad">Логин админки: 2-32 символа.</div>'); return
            cfg = load_cfg()
            old_port = str(cfg.get('sip_port'))
            old_ext = cfg.get('extension')
            cfg['extension'] = ext
            cfg['extension_password'] = extpwd
            cfg['sip_port'] = int(port)
            cfg['admin_user'] = auser
            if apwd:
                cfg['admin_password'] = apwd
            json.dump(cfg, open('/opt/pbx-admin/config.json', 'w'), ensure_ascii=False, indent=1)
            # телефон
            open(PHONES_FILE, 'w', encoding='utf-8').write(phones_conf(cfg))
            if old_ext != ext:
                import subprocess as sp
                t = open(EXTENSIONS_FILE, encoding='utf-8').read().replace(old_ext, ext)
                open(EXTENSIONS_FILE, 'w', encoding='utf-8').write(t)
                ast('dialplan reload')
            ast('pjsip reload')
            extra = ''
            if port != old_port:
                import subprocess as sp
                t = open(PJSIP_FILE, encoding='utf-8').read().replace(old_port, port)
                open(PJSIP_FILE, 'w', encoding='utf-8').write(t)
                try:
                    sp.run(['ufw', 'allow', port + '/udp'], capture_output=True, timeout=30)
                except Exception:
                    pass
                sp.run(['systemctl', 'restart', 'asterisk'], capture_output=True, timeout=90)
                extra = ' Порт изменён: sip перезапущен, порт ' + port + ' открыт в файрволе. Старый порт ' + old_port + ' тоже пока открыт.'
            self.render('<div class="msg ok">Настройки сохранены. Настройте софтфон: сервер тот же, порт ' + port + ', логин ' + ext + '.' + extra + '</div>')
        elif self.path == '/disable':
            if q.get('sure', [''])[0] != '1':
                self.render(); return
            open(SETTINGS_FILE, 'w').write('{"admin_enabled": false}')
            self.render('<div class="msg ok">Админка отключена. Это последний ответ. Включить обратно: /opt/pbx-admin/adminctl.sh on (по SSH на сервере). АТС продолжает работать.</div>')
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass

if __name__ == '__main__':
    ThreadingHTTPServer((CFG['listen_host'], CFG['listen_port']), H).serve_forever()
