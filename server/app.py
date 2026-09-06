"""Single-controller console. Python 3.6+, standard library only."""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

ACTIVE = ('DRAINING', 'SETTLING', 'FILLING')
COMMANDS = ('START', 'FILL', 'STOP', 'RESET')


class Problem(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def validate_status(value):
    if not isinstance(value, dict):
        raise Problem(400, 'invalid_status')
    if value.get('project') != 'water_auto_exchange' or value.get('version') not in ('0.3.0', '0.4.0'):
        raise Problem(409, 'firmware_mismatch')
    if value.get('state') not in ('UNCONFIGURED', 'IDLE', 'DONE', 'FAULT') + ACTIVE:
        raise Problem(400, 'invalid_state')
    result = {}
    for key in ('project', 'version', 'state', 'reason', 'ready', 'fill', 'drain',
                'outputs_known', 'need_fill', 'overflow', 'cycle', 'overflow_protection'):
        item = value.get(key)
        if not isinstance(item, str) or len(item) > 160:
            raise Problem(400, 'invalid_status_' + key)
        result[key] = item
    for key in ('ready', 'fill', 'drain', 'outputs_known', 'overflow', 'overflow_protection'):
        if result[key] not in ('0', '1'):
            raise Problem(400, 'invalid_flag')
    if result['need_fill'] not in ('0', '1', 'unknown') or not result['cycle'].isdigit():
        raise Problem(400, 'invalid_level_or_cycle')
    return result


class Store:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS commands (
              id TEXT PRIMARY KEY, command TEXT NOT NULL, created REAL NOT NULL,
              expires REAL NOT NULL, status TEXT NOT NULL, result TEXT, finished REAL);
        ''')
        # Restart never resurrects commands or claims that old telemetry is live.
        self.db.execute("UPDATE commands SET status='uncertain', result='server_restarted' WHERE status IN ('queued','delivered')")
        self.db.commit()
        self.status, self.seen, self.gateway = None, None, None
        self.gateway_seen = 0

    def expire(self):
        now = self.clock()
        self.db.execute("UPDATE commands SET status=CASE status WHEN 'queued' THEN 'expired' ELSE 'uncertain' END, result='ack_timeout', finished=? WHERE status IN ('queued','delivered') AND expires<?", (now, now))
        self.db.commit()

    def online(self):
        return self.seen is not None and self.clock() - self.seen <= 10

    def snapshot(self):
        with self.lock:
            self.expire()
            return dict(online=self.online(), last_seen=self.seen, device=self.status,
                        commands=[dict(r) for r in self.db.execute('SELECT * FROM commands ORDER BY created DESC, rowid DESC LIMIT 60')])

    def enqueue(self, command, request_id):
        if command not in COMMANDS or not isinstance(request_id, str) or len(request_id) != 32 or any(c not in '0123456789abcdef' for c in request_id):
            raise Problem(400, 'invalid_command')
        with self.lock:
            self.expire()
            previous = self.db.execute('SELECT * FROM commands WHERE id=?', (request_id,)).fetchone()
            if previous:
                if previous['command'] != command:
                    raise Problem(409, 'request_id_conflict')
                return dict(previous)
            if not self.online():
                raise Problem(409, 'device_offline')
            s = self.status
            if command in ('START', 'FILL'):
                if s['ready'] != '1' or s['outputs_known'] != '1' or s['overflow'] != '0' or s['state'] not in ('IDLE', 'DONE'):
                    raise Problem(409, 'device_not_ready')
                if s['need_fill'] != ('1' if command == 'FILL' else '0'):
                    raise Problem(409, 'level_not_ready')
            if command == 'RESET' and s['state'] != 'FAULT':
                raise Problem(409, 'not_faulted')
            if command == 'STOP':
                self.db.execute("UPDATE commands SET status='cancelled', result='superseded_by_stop', finished=? WHERE status='queued'", (self.clock(),))
            elif self.db.execute("SELECT 1 FROM commands WHERE status IN ('queued','delivered')").fetchone():
                raise Problem(409, 'command_pending')
            now = self.clock()
            self.db.execute('INSERT INTO commands VALUES (?,?,?,?,?,?,?)', (request_id, command, now, now + 8, 'queued', None, None))
            self.db.commit()
            return dict(self.db.execute('SELECT * FROM commands WHERE id=?', (request_id,)).fetchone())

    def poll(self, gateway, status, ack=None):
        status = validate_status(status)
        if not isinstance(gateway, str) or not 16 <= len(gateway) <= 64:
            raise Problem(400, 'invalid_gateway')
        with self.lock:
            now = self.clock()
            if self.gateway is not None and self.gateway != gateway and self.gateway_seen > now - 15:
                raise Problem(409, 'another_gateway_active')
            # A new gateway does not inherit a previous session's pending work.
            if self.gateway != gateway:
                self.db.execute("UPDATE commands SET status='uncertain', result='gateway_changed' WHERE status IN ('queued','delivered')")
            self.gateway, self.gateway_seen = gateway, now
            self.expire()
            if ack is not None:
                if not isinstance(ack, dict) or ack.get('status') not in ('succeeded', 'rejected', 'uncertain') or not isinstance(ack.get('result'), str) or len(ack['result']) > 256:
                    raise Problem(400, 'invalid_ack')
                self.db.execute("UPDATE commands SET status=?, result=?, finished=? WHERE id=? AND status IN ('delivered','uncertain')", (ack['status'], ack['result'], now, ack.get('id')))
            self.status, self.seen = status, now
            row = self.db.execute("SELECT * FROM commands WHERE status='queued' ORDER BY CASE command WHEN 'STOP' THEN 0 ELSE 1 END, created LIMIT 1").fetchone()
            if row:
                self.db.execute("UPDATE commands SET status='delivered' WHERE id=?", (row['id'],))
            self.db.commit()
            return dict(command=dict(row) if row else None, ttl_ms=max(0, int((row['expires'] - now) * 1000)) if row else 0)

    def hello(self):
        with self.lock:
            now = self.clock()
            if self.gateway is not None and self.gateway_seen > now - 15:
                raise Problem(409, 'previous_session_active')
            self.db.execute("UPDATE commands SET status='uncertain', result='device_reconnected', finished=? WHERE status IN ('queued','delivered')", (now,))
            self.db.commit()
            self.gateway = secrets.token_hex(16)
            self.gateway_seen, self.seen = now, None
            return dict(gateway=self.gateway)


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True

    def __init__(self, address, store, admin_key, device_key, origin):
        self.store, self.admin_key, self.device_key, self.origin = store, admin_key, device_key, origin
        self.sessions, self.attempts = {}, []
        self.auth_lock = threading.Lock()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log credentials, bodies or URLs.

    def respond(self, code, value, cookie=None):
        data = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise Problem(400, 'invalid_length')
        if not 0 < length <= 8192:
            raise Problem(413, 'invalid_body_size')
        try:
            value = json.loads(self.rfile.read(length).decode('utf-8'))
        except (ValueError, UnicodeError):
            raise Problem(400, 'invalid_json')
        if not isinstance(value, dict):
            raise Problem(400, 'invalid_body')
        return value

    def session(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            token = cookie['water_session'].value
        except Exception:
            raise Problem(401, 'login_required')
        with self.server.auth_lock:
            if self.server.sessions.get(token, 0) < time.time():
                raise Problem(401, 'login_required')
        return token

    def route(self):
        self.connection.settimeout(10)
        path = self.path.split('?')[0]
        post = self.command == 'POST'
        if path == '/water/api/health' and not post:
            return self.respond(200, dict(ok=True, service='water-console', version='1.0.0'))
        if path in ('/water/api/device/poll', '/water/api/device/hello') and post:
            auth = self.headers.get('Authorization', '')
            if not hmac.compare_digest(auth, 'Bearer ' + self.server.device_key):
                raise Problem(401, 'device_auth_failed')
            data = self.body()
            if path.endswith('/hello'):
                return self.respond(200, self.server.store.hello())
            return self.respond(200, self.server.store.poll(data.get('gateway'), data.get('status'), data.get('ack')))
        if post and self.headers.get('Origin') != self.server.origin:
            raise Problem(403, 'invalid_origin')
        if path == '/water/api/login' and post:
            data = self.body()
            with self.server.auth_lock:
                now = time.time()
                self.server.attempts = [t for t in self.server.attempts if t > now - 60]
                if len(self.server.attempts) >= 10:
                    raise Problem(429, 'try_later')
                self.server.attempts.append(now)
                key = data.get('key')
                if not isinstance(key, str) or not hmac.compare_digest(hashlib.sha256(key.encode()).digest(), hashlib.sha256(self.server.admin_key.encode()).digest()):
                    raise Problem(401, 'invalid_key')
                self.server.sessions = {k: v for k, v in self.server.sessions.items() if v > now}
                token = secrets.token_urlsafe(32)
                self.server.sessions[token] = now + 28800
            return self.respond(200, dict(ok=True), 'water_session=' + token + '; Path=/water/api/; Secure; HttpOnly; SameSite=Strict; Max-Age=28800')
        token = self.session()
        if path == '/water/api/logout' and post:
            with self.server.auth_lock:
                self.server.sessions.pop(token, None)
            return self.respond(200, dict(ok=True), 'water_session=; Path=/water/api/; Secure; HttpOnly; SameSite=Strict; Max-Age=0')
        if path == '/water/api/status' and not post:
            return self.respond(200, self.server.store.snapshot())
        if path == '/water/api/commands' and post:
            data = self.body()
            return self.respond(202, self.server.store.enqueue(data.get('command'), data.get('id')))
        raise Problem(404, 'not_found')

    def dispatch(self):
        try:
            self.route()
        except Problem as exc:
            self.respond(exc.code, dict(error=exc.message))
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            self.respond(500, dict(error='internal_error'))

    do_GET = dispatch
    do_POST = dispatch


if __name__ == '__main__':
    admin, device = os.environ['WATER_ADMIN_KEY'], os.environ['WATER_DEVICE_KEY']
    if len(admin) < 32 or len(device) < 32 or admin == device:
        raise SystemExit('Use distinct random keys of at least 32 characters')
    store = Store(os.environ.get('WATER_DB', '/var/lib/water-console/water.db'))
    Server(('127.0.0.1', int(os.environ.get('WATER_PORT', '8790'))), store,
           admin, device, os.environ.get('WATER_ORIGIN', 'https://bytegallop.com')).serve_forever()
