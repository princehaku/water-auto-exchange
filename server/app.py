"""Single-controller console. Python 3.6+, wsproto for WebSocket framing."""
import hashlib
import hmac
import json
import math
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
SOFT_LIMITS = dict(version=1, fill_seconds=180, drain_seconds=300, watchdog_ms=5000)
COMMAND_PRIORITY = "CASE command WHEN 'FILL_TIMEOUT' THEN 0 WHEN 'DRAIN_TIMEOUT' THEN 0 WHEN 'STOP' THEN 1 WHEN 'FILL_OFF' THEN 2 WHEN 'DRAIN_OFF' THEN 2 ELSE 3 END"
ADMIN_SESSION_TTL_SECONDS = 999 * 24 * 60 * 60


class Problem(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def validate_status(value):
    if not isinstance(value, dict):
        raise Problem(400, 'invalid_status')
    if value.get('project') != 'water_auto_exchange' or value.get('version') not in ('0.3.0', '0.4.0', '0.5.0', '0.5.1', '0.5.2', '0.5.3', '0.6.0', '0.7.0', '0.7.1', '0.7.2', '0.7.3', '0.7.4', '0.7.5', '0.7.6', '0.7.7', '0.8.0', '0.8.1', '0.8.2', '0.8.3'):
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
    if result['version'] in ('0.7.0', '0.7.1', '0.7.2', '0.7.3', '0.7.4', '0.7.5', '0.7.6', '0.7.7', '0.8.0', '0.8.1', '0.8.2', '0.8.3'):
        if value.get('control_mode') not in ('manual', 'automatic'):
            raise Problem(400, 'invalid_control_mode')
        result['control_mode'] = value['control_mode']
    concurrent = result['version'] in ('0.8.0', '0.8.1', '0.8.2', '0.8.3') and result.get('control_mode') == 'manual'
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
    soft_limits = SOFT_LIMITS

    def __init__(self, path, clock=time.time, control_clock=None):
        self.clock = clock
        # Deadlines must survive wall-clock corrections. A supplied test clock
        # controls both clocks unless the test explicitly separates them.
        self.control_clock = control_clock or (time.monotonic if clock is time.time else clock)
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
            CREATE TABLE IF NOT EXISTS aquarium_simulation (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
        ''')
        # Restart never resurrects commands or claims that old telemetry is live.
        self.db.execute("UPDATE commands SET status='uncertain', result='server_restarted' WHERE status IN ('queued','delivered')")
        self.db.commit()
        self.status, self.seen, self.gateway = None, None, None
        self.gateway_seen = 0
        self.ws_gateway = None
        self.ws_grants = {}
        self.ws_grant_sequence = 0
        self.web_limits = False
        self.control_runs = dict(fill=None, drain=None)
        self.control_timeout = None
        self.control_uncertain = True
        self.connection_state, self.connection_since, self.connection_reason = 'offline', None, 'never_connected'
        saved = self.db.execute('SELECT value FROM device_snapshot WHERE id=1').fetchone()
        if saved:
            previous = json.loads(saved['value'])
            self.status, self.seen = previous.get('device'), previous.get('last_seen')
            self.web_limits = bool(self.status and self.status.get('version') in ('0.8.2', '0.8.3')
                                   and self.status.get('control_mode') == 'manual')
        saved_sim = self.db.execute('SELECT value FROM aquarium_simulation WHERE id=1').fetchone()
        self.simulation = json.loads(saved_sim['value']) if saved_sim else dict(
            level=None, fill_seconds=None, drain_seconds=None, updated_at=None,
            uncertain=False, observed_at=None, observed_known=False,
            fill_on=False, drain_on=False, fill_since=None, drain_since=None)
        # Older installations have percentage calibration but no capacity or
        # volume history. Do not invent liters for their existing estimate.
        self.simulation.setdefault('capacity_liters', None)
        self.simulation.setdefault('calibrated_at', None)
        for key in ('fill', 'drain'):
            self.simulation.setdefault(key + '_last_known_on',
                                       self.simulation[key + '_on'] if self.simulation['observed_known'] else None)
            self.simulation.setdefault(key + '_run_liters', None)
            self.simulation.setdefault(key + '_total_liters', None)
        # An output that was active before a server restart has an unknown stop time.
        if self.simulation['fill_on'] or self.simulation['drain_on']:
            self.simulation['uncertain'] = True
            self.simulation['observed_known'] = False
            self.simulation['observed_at'] = None
            self.simulation['fill_since'] = self.simulation['drain_since'] = None
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
        if not online and (self.simulation['fill_on'] or self.simulation['drain_on']):
            self.simulation['uncertain'] = True
            self.simulation['observed_known'] = False
            self.simulation['observed_at'] = None
            self.simulation['fill_since'] = self.simulation['drain_since'] = None
            self.save_simulation()
        self.db.execute('INSERT INTO connection_events(at,state,reason) VALUES(?,?,?)',
                        (self.connection_since, state, reason))
        self.db.execute('DELETE FROM connection_events WHERE id NOT IN (SELECT id FROM connection_events ORDER BY id DESC LIMIT 60)')
        self.save_device_snapshot()

    def save_device_snapshot(self):
        self.db.execute('INSERT OR REPLACE INTO device_snapshot VALUES(1,?)',
                        (json.dumps(dict(device=self.status, last_seen=self.seen)),))
        self.db.commit()

    def save_simulation(self):
        self.db.execute('INSERT OR REPLACE INTO aquarium_simulation VALUES(1,?)',
                        (json.dumps(self.simulation),))
        self.db.commit()

    def control_abort(self, reason):
        self.control_uncertain = True
        if self.ws_gateway:
            self.ws_close(self.ws_gateway, reason)
        raise Problem(409, reason)

    def control_start(self, key, command_id):
        if self.control_runs[key] is None:
            seconds = SOFT_LIMITS[key + '_seconds']
            now = self.clock()
            self.control_runs[key] = dict(since=now, deadline=now + seconds,
                                          until=self.control_clock() + seconds,
                                          command_id=command_id, observed_on=False,
                                          grant_sequence=self.ws_grants[command_id]['sequence'])

    def observe_control(self, status, ack=None):
        """Control authority is separate from calibrated water estimates.

        An execute grant reserves its deadline before any status/ACK arrives.
        An older all-OFF status cannot erase an unconfirmed execute grant.
        """
        if not self.web_limits:
            return
        if status['version'] not in ('0.8.2', '0.8.3') or status.get('control_mode') != 'manual':
            self.control_abort('control_protocol_changed')
        if status['outputs_known'] != '1':
            self.control_abort('control_state_uncertain')
        all_off = status['fill'] == status['drain'] == '0'
        ack_row = self.db.execute('SELECT * FROM commands WHERE id=?', (ack.get('id'),)).fetchone() if ack else None
        ack_grant = self.ws_grants.get(ack.get('id')) if ack else None
        timeout = self.control_timeout
        normal_timeout_closed = False
        if timeout and all_off and status['version'] == '0.8.3' and status['state'] in ('IDLE', 'DONE'):
            timeout_grant = self.ws_grants.get(timeout['id'])
            expected_reason = timeout['key'] + '_timeout'
            # An old in-flight OFF must not acknowledge the new timeout.
            # A current execute receipt proves ordering directly; unsolicited
            # status additionally needs the expected timeout completion reason.
            normal_timeout_closed = bool(
                (ack_grant and ack.get('status') == 'succeeded'
                 and ack_grant['sequence'] > timeout['grant_sequence']
                 and (ack['id'] == timeout['id'] or ack_row['command'] in ('STOP', 'FILL_OFF', 'DRAIN_OFF')))
                or (timeout_grant and ack is None and status['reason'] == expected_reason))
        if timeout and all_off and (status['state'] == 'FAULT' or normal_timeout_closed):
            # 0.8.3 completes normal time limits without faulting. Earlier
            # firmware, and actual faults on any version, retain their latch.
            self.db.execute("UPDATE commands SET status=?, result=?, finished=? WHERE command IN ('FILL_TIMEOUT','DRAIN_TIMEOUT') AND status IN ('queued','delivered') AND id<>?",
                            ('succeeded' if normal_timeout_closed else 'cancelled',
                             'outputs_off_confirmed' if normal_timeout_closed else 'fault_confirmed',
                             self.clock(), ack.get('id', '') if ack else ''))
            self.control_runs = dict(fill=None, drain=None)
            self.control_timeout = None
            self.control_uncertain = False
            self.db.commit()
            return
        for key in ('fill', 'drain'):
            run = self.control_runs[key]
            if status[key] == '1':
                if run is None:
                    self.control_abort('control_unowned_output')
                run['observed_on'] = True
            elif run is not None and not self.control_timeout:
                rejected_start = ack and ack.get('status') == 'rejected' and ack.get('id') == run['command_id']
                confirmed_stop = (ack_row and ack_grant and ack.get('status') == 'succeeded'
                                  and ack_row['command'] in ('STOP', key.upper() + '_OFF')
                                  and ack_grant['sequence'] >= run['grant_sequence'])
                if run['observed_on'] or rejected_start or confirmed_stop or (all_off and status['state'] == 'FAULT'):
                    self.control_runs[key] = None
        self.control_uncertain = False

    def control_tick(self, session=None):
        """Called by the WS owner every 100 ms, even with no browser/pings."""
        with self.lock:
            if session is not None and session != self.ws_gateway:
                raise Problem(409, 'stale_session')
            if not self.web_limits or self.ws_gateway is None:
                return
            now = self.control_clock()
            if self.control_timeout:
                if now >= self.control_timeout['confirm_until']:
                    self.control_uncertain = True
                    if session is not None:
                        self.control_abort('control_stop_unconfirmed')
                return
            expired = [(run['until'], key) for key, run in self.control_runs.items()
                       if run is not None and now >= run['until']]
            if not expired:
                return
            _, key = min(expired)
            # Revoke every unexecuted grant before the stop offer, including
            # delivered commands that have not yet reached their claim.
            self.db.execute("UPDATE commands SET status='cancelled', result='superseded_by_timeout', finished=? WHERE status IN ('queued','delivered')", (self.clock(),))
            command_id = secrets.token_hex(16)
            self.db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?)',
                            (command_id, key.upper() + '_TIMEOUT', self.clock(),
                             self.clock() + 1, 'queued', None, None))
            self.control_timeout = dict(key=key, id=command_id, confirm_until=now + 1,
                                        grant_sequence=self.ws_grant_sequence)
            self.db.commit()

    def control_limits_snapshot(self):
        status = self.status or {}
        recent = status.get('version') in ('0.8.1', '0.8.2', '0.8.3')
        result = dict(source='web' if self.web_limits else 'firmware',
                      fill_seconds=180 if recent else 120,
                      drain_seconds=300 if recent else 120,
                      uncertain=self.control_uncertain if self.web_limits else self.simulation['uncertain'],
                      timeout_pending=self.control_timeout['key'] if self.control_timeout else None)
        for key in ('fill', 'drain'):
            run = self.control_runs[key] if self.web_limits else None
            since = None if self.web_limits else self.simulation[key + '_since']
            deadline = since + result[key + '_seconds'] if since is not None else None
            if run:
                # Project monotonic remaining time onto the current wall clock
                # so a clock correction cannot lengthen the UI countdown.
                deadline = self.clock() + max(0, run['until'] - self.control_clock())
                since = deadline - result[key + '_seconds']
            result[key + '_on_since'] = since
            result[key + '_deadline'] = deadline
        return result

    def observe_outputs(self, status):
        """Integrate only spans supported by consecutive, fresh device reports."""
        sim, now = self.simulation, self.clock()
        previous_at = sim['observed_at']
        known = status['outputs_known'] == '1'
        # An unknown endpoint cannot confirm how long the previous output ran.
        if not known and (sim['level'] is not None or sim['fill_on'] or sim['drain_on']):
            sim['uncertain'] = True
        if sim['observed_known'] and previous_at is not None and not sim['uncertain']:
            elapsed = max(0, now - previous_at)
            if elapsed > 12 and (sim['fill_on'] or sim['drain_on']):
                sim['uncertain'] = True
            elif elapsed and sim['level'] is not None:
                delta = (int(sim['fill_on']) * 100 / sim['fill_seconds']
                         - int(sim['drain_on']) * 100 / sim['drain_seconds']) * elapsed
                sim['level'] = min(100, max(0, sim['level'] + delta))
                sim['updated_at'] = now
                if sim['capacity_liters'] is not None:
                    for key in ('fill', 'drain'):
                        if sim[key + '_on']:
                            liters = sim['capacity_liters'] * elapsed / sim[key + '_seconds']
                            sim[key + '_run_liters'] += liters
                            sim[key + '_total_liters'] += liters
        # Without a calibrated water history, confirmed all-OFF is enough to
        # establish a fresh timer baseline. A calibrated estimate stays frozen
        # until the user supplies a new observed water level.
        if sim['level'] is None and known and status['fill'] == status['drain'] == '0':
            sim['uncertain'] = False
        for key in ('fill', 'drain'):
            on = known and status[key] == '1'
            # Repeated ON, unknown reports and reconnects must not erase the
            # last run. Only a confirmed OFF followed by ON starts a new one.
            if on and sim[key + '_last_known_on'] is False:
                sim[key + '_run_liters'] = 0.0 if sim['capacity_liters'] is not None else None
            if known:
                sim[key + '_last_known_on'] = on
            if on and not sim['uncertain'] and (not sim['observed_known'] or not sim[key + '_on']):
                sim[key + '_since'] = now
            elif not on or sim['uncertain']:
                sim[key + '_since'] = None
            sim[key + '_on'] = on
        sim['observed_known'], sim['observed_at'] = known, now
        self.save_simulation()

    def configure_simulation(self, value):
        if not isinstance(value, dict):
            raise Problem(400, 'invalid_simulation')
        numbers = [value.get(key) for key in ('level', 'fill_seconds', 'drain_seconds')]
        if any(type(n) not in (int, float) or not math.isfinite(n) for n in numbers):
            raise Problem(400, 'invalid_simulation')
        level, fill_seconds, drain_seconds = numbers
        if not (0 <= level <= 100 and 1 <= fill_seconds <= 86400 and 1 <= drain_seconds <= 86400):
            raise Problem(400, 'invalid_simulation')
        capacity = value.get('capacity_liters')
        if capacity is not None and (type(capacity) not in (int, float) or not math.isfinite(capacity)
                                     or not 0.1 <= capacity <= 100000):
            raise Problem(400, 'invalid_simulation')
        with self.lock:
            if not self.online() or not self.status or self.status['outputs_known'] != '1' or self.status['fill'] != '0' or self.status['drain'] != '0':
                raise Problem(409, 'simulation_requires_idle')
            if 'capacity_liters' not in value:
                capacity = self.simulation['capacity_liters']
            self.simulation.update(level=float(level), fill_seconds=float(fill_seconds),
                                   drain_seconds=float(drain_seconds), updated_at=self.clock(),
                                   uncertain=False, observed_at=self.clock(), observed_known=True,
                                   fill_on=False, drain_on=False, fill_since=None, drain_since=None,
                                   capacity_liters=float(capacity) if capacity is not None else None,
                                   calibrated_at=self.clock())
            for key in ('fill', 'drain'):
                self.simulation[key + '_last_known_on'] = False
                self.simulation[key + '_run_liters'] = 0.0 if capacity is not None else None
                self.simulation[key + '_total_liters'] = 0.0 if capacity is not None else None
            self.save_simulation()
            return self.simulation_snapshot()

    def simulation_snapshot(self):
        sim = self.simulation
        online = self.online()
        active = sim['fill_on'] or sim['drain_on']
        if active and sim['observed_at'] is not None and self.clock() - sim['observed_at'] > 12:
            sim['uncertain'] = True
            sim['observed_known'] = False
            sim['observed_at'] = None
            sim['fill_since'] = sim['drain_since'] = None
            self.save_simulation()
        calibrated = sim['level'] is not None
        capacity = sim['capacity_liters']
        has_volume = calibrated and capacity is not None
        trusted = calibrated and online and sim['observed_known'] and not sim['uncertain']
        net_percent = (int(sim['fill_on']) * 100 / sim['fill_seconds']
                       - int(sim['drain_on']) * 100 / sim['drain_seconds']) if trusted else None
        return dict(calibrated=sim['level'] is not None, level=sim['level'],
                    fill_seconds=sim['fill_seconds'], drain_seconds=sim['drain_seconds'],
                    updated_at=sim['updated_at'], calibrated_at=sim['calibrated_at'], uncertain=sim['uncertain'],
                    observed_at=sim['observed_at'], fill_on_since=sim['fill_since'],
                    drain_on_since=sim['drain_since'], capacity_liters=capacity,
                    volume_liters=capacity * sim['level'] / 100 if has_volume else None,
                    fill_rate_lpm=capacity * 60 / sim['fill_seconds'] if has_volume else None,
                    drain_rate_lpm=capacity * 60 / sim['drain_seconds'] if has_volume else None,
                    net_lpm=capacity * net_percent * 0.6 if has_volume and trusted else None,
                    fill_run_liters=sim['fill_run_liters'], drain_run_liters=sim['drain_run_liters'],
                    fill_total_liters=sim['fill_total_liters'], drain_total_liters=sim['drain_total_liters'],
                    eta_full_seconds=(100 - sim['level']) / net_percent if trusted and net_percent > 0 else None,
                    eta_empty_seconds=sim['level'] / -net_percent if trusted and net_percent < 0 else None)

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
            active = self.status and self.status['state'] in ACTIVE
            active = active or (self.web_limits and any(self.control_runs.values()))
            limit = 75 if self.ws_gateway and not active else (5 if self.web_limits else 10)
            online = self.gateway is not None and self.seen is not None and self.clock() - self.seen <= limit
            self.connection_event(online, 'connected' if online else 'heartbeat_timeout')
            return online

    def snapshot(self):
        with self.lock:
            self.control_tick()
            self.expire()
            online = self.online()
            return dict(online=online, last_seen=self.seen, device=self.status, server_time=self.clock(),
                        connection=dict(state=self.connection_state, since=self.connection_since, reason=self.connection_reason,
                                        events=[dict(r) for r in self.db.execute('SELECT at,state,reason FROM connection_events ORDER BY id DESC LIMIT 60')]),
                        traffic=self.traffic_snapshot(),
                        simulation=self.simulation_snapshot(),
                        control_limits=self.control_limits_snapshot(),
                        commands=[dict(r) for r in self.db.execute('SELECT * FROM commands ORDER BY created DESC, rowid DESC LIMIT 60')])

    def enqueue(self, command, request_id):
        if command not in COMMANDS or not isinstance(request_id, str) or len(request_id) != 32 or any(c not in '0123456789abcdef' for c in request_id):
            raise Problem(400, 'invalid_command')
        with self.lock:
            self.control_tick()
            self.expire()
            previous = self.db.execute('SELECT * FROM commands WHERE id=?', (request_id,)).fetchone()
            if previous:
                if previous['command'] != command:
                    raise Problem(409, 'request_id_conflict')
                return dict(previous)
            if not self.online():
                raise Problem(409, 'device_offline')
            s = self.status
            if self.control_timeout and command not in ('STOP', 'FILL_OFF', 'DRAIN_OFF'):
                raise Problem(409, 'control_timeout_pending')
            concurrent = s['version'] in ('0.8.0', '0.8.1', '0.8.2', '0.8.3') and s.get('control_mode') == 'manual'
            if command in ('FILL_OFF', 'DRAIN_OFF') and not concurrent:
                raise Problem(409, 'firmware_upgrade_required')
            if command == 'DRAIN' and s['version'] not in ('0.6.0', '0.7.0', '0.7.1', '0.7.2', '0.7.3', '0.7.4', '0.7.5', '0.7.6', '0.7.7', '0.8.0', '0.8.1', '0.8.2', '0.8.3'):
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
                self.db.execute("UPDATE commands SET status='cancelled', result='superseded_by_stop', finished=? WHERE status='queued' AND command NOT IN ('FILL_TIMEOUT','DRAIN_TIMEOUT')", (self.clock(),))
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
        if status['version'] in ('0.8.2', '0.8.3') and status.get('control_mode') == 'manual':
            raise Problem(409, 'firmware_requires_wss')
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
            self.web_limits = False
            self.ws_grants = {}
            self.ws_grant_sequence = 0
            self.control_runs = dict(fill=None, drain=None)
            self.control_timeout = None
            self.expire()
            if ack is not None:
                if not isinstance(ack, dict) or ack.get('status') not in ('succeeded', 'rejected', 'uncertain') or not isinstance(ack.get('result'), str) or len(ack['result']) > 256:
                    raise Problem(400, 'invalid_ack')
                self.db.execute("UPDATE commands SET status=?, result=?, finished=? WHERE id=? AND status IN ('delivered','uncertain')", (ack['status'], ack['result'], now, ack.get('id')))
            self.status, self.seen = status, now
            self.observe_outputs(status)
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
            soft = status['version'] in ('0.8.2', '0.8.3') and status.get('control_mode') == 'manual'
            # Only the authenticated previous owner can replace its half-open
            # session immediately; another client still cannot evict a live owner.
            if self.ws_gateway and isinstance(previous_session, str) and hmac.compare_digest(previous_session, self.ws_gateway):
                self.ws_close(self.ws_gateway, 'session_replaced')
            elif self.ws_gateway and not self.online():
                self.ws_close(self.ws_gateway, 'heartbeat_timeout')
            if self.ws_gateway or (self.gateway and self.gateway_seen > self.clock() - 15):
                raise Problem(409, 'another_device_active')
            if soft and (status['outputs_known'] != '1' or status['fill'] != '0' or status['drain'] != '0'):
                raise Problem(409, 'control_state_uncertain')
            self.ws_gateway = self.gateway = secrets.token_hex(16)
            self.ws_grants = {}
            self.ws_grant_sequence = 0
            self.web_limits = soft
            self.control_runs = dict(fill=None, drain=None)
            self.control_timeout = None
            self.control_uncertain = False
            self.db.execute("UPDATE commands SET status='uncertain', result='device_reconnected' WHERE status IN ('queued','delivered')")
            self.db.commit()
            self.status, self.seen = status, self.clock()
            self.observe_outputs(status)
            self.gateway_seen = self.seen
            self.connection_event(True, 'connected')
            return self.ws_gateway

    def ws_touch(self, session, status=None, ack=None):
        with self.lock:
            if session is None or session != self.ws_gateway:
                raise Problem(409, 'stale_session')
            self.control_tick(session)
            if ack is not None and (not isinstance(ack, dict) or ack.get('status') not in ('succeeded', 'rejected', 'uncertain') or not isinstance(ack.get('result'), str) or len(ack['result']) > 256 or not isinstance(ack.get('id'), str)):
                raise Problem(400, 'invalid_ack')
            if ack is not None:
                grant = self.ws_grants.get(ack['id'])
                if grant is None:
                    raise Problem(400, 'unclaimed_ack')
                if grant['acked']:
                    return False
                self.expire()
                row = self.db.execute('SELECT status FROM commands WHERE id=?', (ack['id'],)).fetchone()
                grant['acked'] = True
                if row is None or row['status'] != 'delivered':
                    return False
                if grant['sequence'] < self.ws_grant_sequence:
                    # A late first receipt is useful history, but its attached
                    # OFF/ON snapshot predates newer execute authority.
                    status = None
            if status is not None:
                status = validate_status(status)
                if self.status and any(version in ('0.8.2', '0.8.3') for version in (self.status['version'], status['version'])) and (
                        self.status['version'] != status['version'] or self.status.get('control_mode') != status.get('control_mode')):
                    self.control_abort('control_protocol_changed')
                self.observe_control(status, ack)
                self.status = status
                self.observe_outputs(self.status)
            if ack is not None:
                self.db.execute("UPDATE commands SET status=?, result=?, finished=? WHERE id=? AND status IN ('delivered','uncertain')", (ack['status'], ack['result'], self.clock(), ack['id']))
                self.db.commit()
            self.seen = self.gateway_seen = self.clock()
            self.connection_event(True, 'connected')

    def ws_offer(self, session):
        with self.lock:
            if session is None or session != self.ws_gateway:
                return None
            self.control_tick(session)
            self.expire()
            row = self.db.execute("SELECT * FROM commands WHERE status='queued' ORDER BY " + COMMAND_PRIORITY + ", created LIMIT 1").fetchone()
            if not row:
                return None
            self.db.execute("UPDATE commands SET status='delivered' WHERE id=?", (row['id'],))
            self.db.commit()
            return dict(type='offer', id=row['id'], command=row['command'])

    def ws_claim(self, session, command_id):
        with self.lock:
            if session is None or session != self.ws_gateway or not isinstance(command_id, str):
                raise Problem(409, 'stale_session')
            self.control_tick(session)
            self.expire()
            row = self.db.execute("SELECT * FROM commands WHERE id=? AND status='delivered'", (command_id,)).fetchone()
            if not row:
                return dict(type='expired', id=command_id)
            if command_id not in self.ws_grants:
                self.ws_grant_sequence += 1
                self.ws_grants[command_id] = dict(sequence=self.ws_grant_sequence, acked=False)
            if self.web_limits and row['command'] in ('FILL', 'DRAIN'):
                self.control_start(row['command'].lower(), row['id'])
            return dict(type='execute', id=row['id'], command=row['command'], ttl_ms=max(0, int((row['expires'] - self.clock()) * 1000)))

    def ws_close(self, session, reason='connection_closed'):
        with self.lock:
            if session != self.ws_gateway:
                return
            self.db.execute("UPDATE commands SET status='uncertain', result='device_disconnected', finished=? WHERE status IN ('queued','delivered')", (self.clock(),))
            self.db.commit()
            self.ws_gateway = self.gateway = None
            self.control_uncertain = True
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
        if path == '/water/api/simulation' and post:
            return self.respond(200, self.server.store.configure_simulation(self.body()))
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
