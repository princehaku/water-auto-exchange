"""Single-controller console. Python 3.6+, wsproto for WebSocket framing."""
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

ACTIVE = ('DRAINING', 'SETTLING', 'FILLING', 'EXCHANGING')
COMMANDS = ('START', 'FILL', 'DRAIN', 'FILL_OFF', 'DRAIN_OFF', 'STOP', 'RESET')
ADMIN_SESSION_TTL_SECONDS = 999 * 24 * 60 * 60


class Problem(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def validate_status(value):
    if not isinstance(value, dict):
        raise Problem(400, 'invalid_status')
    if value.get('project') != 'water_auto_exchange' or value.get('version') not in ('0.3.0', '0.4.0', '0.5.0', '0.5.1', '0.5.2', '0.5.3', '0.6.0', '0.7.0', '0.7.1', '0.7.2', '0.7.3', '0.7.4', '0.7.5', '0.7.6', '0.7.7', '0.8.0'):
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
    if result['version'] in ('0.7.0', '0.7.1', '0.7.2', '0.7.3', '0.7.4', '0.7.5', '0.7.6', '0.7.7', '0.8.0'):
        if value.get('control_mode') not in ('manual', 'automatic'):
            raise Problem(400, 'invalid_control_mode')
        result['control_mode'] = value['control_mode']
    concurrent = result['version'] == '0.8.0' and result.get('control_mode') == 'manual'
    if result['state'] == 'EXCHANGING' and not concurrent:
        raise Problem(400, 'invalid_state')
    if result['outputs_known'] == '1':
        both = result['fill'] == result['drain'] == '1'
        if both and (not concurrent or result['state'] != 'EXCHANGING'):
            raise Problem(400, 'invalid_state')
        if result['state'] == 'EXCHANGING' and not both:
            raise Problem(400, 'invalid_state')
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
            CREATE TABLE IF NOT EXISTS admin_sessions (
              token_hash TEXT PRIMARY KEY, expires REAL NOT NULL,
              admin_key_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS connection_events (
              id INTEGER PRIMARY KEY, at REAL NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS device_snapshot (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS traffic_meters (
              id TEXT PRIMARY KEY, total INTEGER NOT NULL, interval_bytes INTEGER NOT NULL,
              interval_seconds INTEGER NOT NULL, updated REAL NOT NULL);
        ''')
        # Restart never resurrects commands or claims that old telemetry is live.
        self.db.execute("UPDATE commands SET status='uncertain', result='server_restarted' WHERE status IN ('queued','delivered')")
        self.db.commit()
        self.status, self.seen, self.gateway = None, None, None
        self.gateway_seen = 0
        self.ws_gateway = None
        self.connection_state, self.connection_since, self.connection_reason = 'offline', None, 'never_connected'
        saved = self.db.execute('SELECT value FROM device_snapshot WHERE id=1').fetchone()
        if saved:
            previous = json.loads(saved['value'])
            self.status, self.seen = previous.get('device'), previous.get('last_seen')
        event = self.db.execute('SELECT * FROM connection_events ORDER BY id DESC LIMIT 1').fetchone()
        if event:
            self.connection_state, self.connection_since, self.connection_reason = event['state'], event['at'], event['reason']
            if self.connection_state == 'online':
                self.connection_event(False, 'server_restarted')

    def connection_event(self, online, reason):
        state = 'online' if online else 'offline'
        if state == self.connection_state:
            return
        self.connection_state, self.connection_since, self.connection_reason = state, self.clock(), reason
        self.db.execute('INSERT INTO connection_events(at,state,reason) VALUES(?,?,?)',
                        (self.connection_since, state, reason))
        self.db.execute('DELETE FROM connection_events WHERE id NOT IN (SELECT id FROM connection_events ORDER BY id DESC LIMIT 60)')
        self.save_device_snapshot()

    def save_device_snapshot(self):
        self.db.execute('INSERT OR REPLACE INTO device_snapshot VALUES(1,?)',
                        (json.dumps(dict(device=self.status, last_seen=self.seen)),))
        self.db.commit()

    def traffic_report(self, session, value):
        with self.lock:
            if session != self.ws_gateway or session is None:
                raise Problem(409, 'stale_session')
            meter = value.get('meter')
            if not isinstance(meter, str) or len(meter) != 32 or any(c not in '0123456789abcdef' for c in meter):
                raise Problem(400, 'invalid_traffic')
            numbers = [value.get(k) for k in ('total_bytes', 'interval_bytes', 'interval_seconds')]
            if any(type(n) not in (int, float) or not 0 <= n <= 9007199254740991 or n % 1 for n in numbers):
                raise Problem(400, 'invalid_traffic')
            total, interval, seconds = map(int, numbers)
            if interval > total or not 1 <= seconds <= 31536000:
                raise Problem(400, 'invalid_traffic')
            previous = self.db.execute('SELECT total FROM traffic_meters WHERE id=?', (meter,)).fetchone()
            # Cumulative boot counters make lost receipts and reconnect replay idempotent.
            if previous is None or total > previous['total']:
                self.db.execute('INSERT OR REPLACE INTO traffic_meters VALUES(?,?,?,?,?)',
                                (meter, total, interval, seconds, self.clock()))
                self.db.commit()
            self.ws_touch(session)

    def traffic_snapshot(self):
        latest = self.db.execute('SELECT * FROM traffic_meters ORDER BY updated DESC, rowid DESC LIMIT 1').fetchone()
        total = self.db.execute('SELECT SUM(total) FROM traffic_meters').fetchone()[0]
        return dict(available=latest is not None, total_bytes=total,
                    boot_bytes=latest['total'] if latest else None,
                    interval_bytes=latest['interval_bytes'] if latest else None,
                    interval_seconds=latest['interval_seconds'] if latest else None,
                    last_report_at=latest['updated'] if latest else None)

    def prune_admin_sessions(self, admin_key):
        with self.lock:
            self.db.execute('DELETE FROM admin_sessions WHERE expires<=? OR admin_key_hash<>?',
                            (self.clock(), hashlib.sha256(admin_key.encode()).hexdigest()))
            self.db.commit()

    def create_admin_session(self, admin_key):
        with self.lock:
            self.prune_admin_sessions(admin_key)
            token = secrets.token_urlsafe(32)
            self.db.execute('INSERT INTO admin_sessions VALUES (?,?,?)',
                            (hashlib.sha256(token.encode()).hexdigest(),
                             self.clock() + ADMIN_SESSION_TTL_SECONDS,
                             hashlib.sha256(admin_key.encode()).hexdigest()))
            self.db.commit()
            return token

    def valid_admin_session(self, token, admin_key):
        with self.lock:
            return self.db.execute('SELECT 1 FROM admin_sessions WHERE token_hash=? AND expires>? AND admin_key_hash=?',
                                   (hashlib.sha256(token.encode()).hexdigest(), self.clock(),
                                    hashlib.sha256(admin_key.encode()).hexdigest())).fetchone() is not None

    def revoke_admin_session(self, token):
        with self.lock:
            self.db.execute('DELETE FROM admin_sessions WHERE token_hash=?',
                            (hashlib.sha256(token.encode()).hexdigest(),))
            self.db.commit()

    def expire(self):
        now = self.clock()
        self.db.execute("UPDATE commands SET status=CASE status WHEN 'queued' THEN 'expired' ELSE 'uncertain' END, result='ack_timeout', finished=? WHERE status IN ('queued','delivered') AND expires<?", (now, now))
        self.db.commit()

    def online(self):
        with self.lock:
            limit = 75 if self.ws_gateway and self.status and self.status['state'] not in ACTIVE else 10
            online = self.gateway is not None and self.seen is not None and self.clock() - self.seen <= limit
            self.connection_event(online, 'connected' if online else 'heartbeat_timeout')
            return online

    def snapshot(self):
        with self.lock:
            self.expire()
            online = self.online()
            return dict(online=online, last_seen=self.seen, device=self.status, server_time=self.clock(),
                        connection=dict(state=self.connection_state, since=self.connection_since, reason=self.connection_reason,
                                        events=[dict(r) for r in self.db.execute('SELECT at,state,reason FROM connection_events ORDER BY id DESC LIMIT 60')]),
                        traffic=self.traffic_snapshot(),
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
            concurrent = s['version'] == '0.8.0' and s.get('control_mode') == 'manual'
            if command in ('FILL_OFF', 'DRAIN_OFF') and not concurrent:
                raise Problem(409, 'firmware_upgrade_required')
            if command == 'DRAIN' and s['version'] not in ('0.6.0', '0.7.0', '0.7.1', '0.7.2', '0.7.3', '0.7.4', '0.7.5', '0.7.6', '0.7.7', '0.8.0'):
                raise Problem(409, 'firmware_upgrade_required')
            if command == 'START' and s.get('control_mode') == 'manual':
                raise Problem(409, 'automatic_mode_required')
            if command in ('START', 'FILL', 'DRAIN'):
                allowed_states = ('IDLE', 'DONE', 'FILLING', 'DRAINING', 'EXCHANGING') if concurrent else ('IDLE', 'DONE')
                if s['ready'] != '1' or s['outputs_known'] != '1' or s['overflow'] != '0' or s['state'] not in allowed_states:
                    raise Problem(409, 'device_not_ready')
                if s.get('control_mode') != 'manual' and s['need_fill'] != ('1' if command == 'FILL' else '0'):
                    raise Problem(409, 'level_not_ready')
            if command == 'RESET' and s['state'] != 'FAULT':
                raise Problem(409, 'not_faulted')
            if command == 'STOP':
                self.db.execute("UPDATE commands SET status='cancelled', result='superseded_by_stop', finished=? WHERE status='queued'", (self.clock(),))
            elif command in ('FILL_OFF', 'DRAIN_OFF'):
                self.db.execute("UPDATE commands SET status='cancelled', result='superseded_by_output_off', finished=? WHERE status='queued' AND command=?", (self.clock(), command[:-4]))
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
            if self.ws_gateway or (self.gateway is not None and self.gateway != gateway and self.gateway_seen > now - 15):
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
            self.connection_event(True, 'connected')
            row = self.db.execute("SELECT * FROM commands WHERE status='queued' ORDER BY CASE command WHEN 'STOP' THEN 0 WHEN 'FILL_OFF' THEN 1 WHEN 'DRAIN_OFF' THEN 1 ELSE 2 END, created LIMIT 1").fetchone()
            if row:
                self.db.execute("UPDATE commands SET status='delivered' WHERE id=?", (row['id'],))
            self.db.commit()
            return dict(command=dict(row) if row else None, ttl_ms=max(0, int((row['expires'] - now) * 1000)) if row else 0)

    def hello(self):
        with self.lock:
            now = self.clock()
            if self.ws_gateway or (self.gateway is not None and self.gateway_seen > now - 15):
                raise Problem(409, 'previous_session_active')
            self.db.execute("UPDATE commands SET status='uncertain', result='device_reconnected', finished=? WHERE status IN ('queued','delivered')", (now,))
            self.db.commit()
            self.gateway = secrets.token_hex(16)
            self.gateway_seen, self.seen = now, None
            return dict(gateway=self.gateway)

    def ws_open(self, status, previous_session=None):
        status = validate_status(status)
        with self.lock:
            # Only the authenticated previous owner can replace its half-open
            # session immediately; another client still cannot evict a live owner.
            if self.ws_gateway and isinstance(previous_session, str) and hmac.compare_digest(previous_session, self.ws_gateway):
                self.ws_close(self.ws_gateway, 'session_replaced')
            elif self.ws_gateway and not self.online():
                self.ws_close(self.ws_gateway, 'heartbeat_timeout')
            if self.ws_gateway or (self.gateway and self.gateway_seen > self.clock() - 15):
                raise Problem(409, 'another_device_active')
            self.ws_gateway = self.gateway = secrets.token_hex(16)
            self.db.execute("UPDATE commands SET status='uncertain', result='device_reconnected' WHERE status IN ('queued','delivered')")
            self.db.commit()
            self.status, self.seen = status, self.clock()
            self.gateway_seen = self.seen
            self.connection_event(True, 'connected')
            return self.ws_gateway

    def ws_touch(self, session, status=None, ack=None):
        with self.lock:
            if session != self.ws_gateway:
                raise Problem(409, 'stale_session')
            if status is not None:
                self.status = validate_status(status)
            if ack is not None:
                if not isinstance(ack, dict) or ack.get('status') not in ('succeeded', 'rejected', 'uncertain') or not isinstance(ack.get('result'), str) or len(ack['result']) > 256 or not isinstance(ack.get('id'), str):
                    raise Problem(400, 'invalid_ack')
                self.db.execute("UPDATE commands SET status=?, result=?, finished=? WHERE id=? AND status IN ('delivered','uncertain')", (ack['status'], ack['result'], self.clock(), ack['id']))
                self.db.commit()
            self.seen = self.gateway_seen = self.clock()
            self.connection_event(True, 'connected')

    def ws_offer(self, session):
        with self.lock:
            if session != self.ws_gateway:
                return None
            self.expire()
            row = self.db.execute("SELECT * FROM commands WHERE status='queued' ORDER BY CASE command WHEN 'STOP' THEN 0 WHEN 'FILL_OFF' THEN 1 WHEN 'DRAIN_OFF' THEN 1 ELSE 2 END, created LIMIT 1").fetchone()
            if not row:
                return None
            self.db.execute("UPDATE commands SET status='delivered' WHERE id=?", (row['id'],))
            self.db.commit()
            return dict(type='offer', id=row['id'], command=row['command'])

    def ws_claim(self, session, command_id):
        with self.lock:
            if session != self.ws_gateway or not isinstance(command_id, str):
                raise Problem(409, 'stale_session')
            self.expire()
            row = self.db.execute("SELECT * FROM commands WHERE id=? AND status='delivered'", (command_id,)).fetchone()
            if not row:
                return dict(type='expired', id=command_id)
            return dict(type='execute', id=row['id'], command=row['command'], ttl_ms=max(0, int((row['expires'] - self.clock()) * 1000)))

    def ws_close(self, session, reason='connection_closed'):
        with self.lock:
            if session != self.ws_gateway:
                return
            self.db.execute("UPDATE commands SET status='uncertain', result='device_disconnected', finished=? WHERE status IN ('queued','delivered')", (self.clock(),))
            self.db.commit()
            self.ws_gateway = self.gateway = None
            self.gateway_seen = 0
            self.connection_event(False, reason)
            self.save_device_snapshot()


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True

    def __init__(self, address, store, admin_key, device_key, origin):
        self.store, self.admin_key, self.device_key, self.origin = store, admin_key, device_key, origin
        self.attempts = []
        self.auth_lock = threading.Lock()
        self.store.prune_admin_sessions(admin_key)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    # Avoid buffering WS frame bytes while the HTTP upgrade headers are parsed.
    rbufsize = 0
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
            chunks, remaining = [], length
            while remaining:
                chunk = self.rfile.read(remaining)
                if not chunk:
                    raise Problem(400, 'incomplete_body')
                chunks.append(chunk)
                remaining -= len(chunk)
            value = json.loads(b''.join(chunks).decode('utf-8'))
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
        if not self.server.store.valid_admin_session(token, self.server.admin_key):
            raise Problem(401, 'login_required')
        return token

    def route(self):
        self.connection.settimeout(10)
        path = self.path.split('?')[0]
        post = self.command == 'POST'
        if path == '/water/api/device/ws' and not post:
            if __package__:
                from .ws_endpoint import serve
            else:
                from ws_endpoint import serve
            return serve(self)
        if path == '/water/api/health' and not post:
            return self.respond(200, dict(ok=True, service='water-console', version='1.1.0', device_transport='wss'))
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
                token = self.server.store.create_admin_session(self.server.admin_key)
            return self.respond(200, dict(ok=True), 'water_session=' + token + '; Path=/water/api/; Secure; HttpOnly; SameSite=Strict; Max-Age=' + str(ADMIN_SESSION_TTL_SECONDS))
        token = self.session()
        if path == '/water/api/logout' and post:
            self.server.store.revoke_admin_session(token)
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
    if len(admin) < 8 or len(device) < 32 or admin == device:
        raise SystemExit('Use distinct keys: admin at least 8 characters, device at least 32')
    store = Store(os.environ.get('WATER_DB', '/var/lib/water-console/water.db'))
    Server(('127.0.0.1', int(os.environ.get('WATER_PORT', '8790'))), store,
           admin, device, os.environ.get('WATER_ORIGIN', 'https://bytegallop.com')).serve_forever()
