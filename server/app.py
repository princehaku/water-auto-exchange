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

if __package__:
    from .output_runs import OutputRuns, ROUND_SECONDS
else:
    from output_runs import OutputRuns, ROUND_SECONDS

ACTIVE = ('DRAINING', 'SETTLING', 'FILLING', 'EXCHANGING')
COMMANDS = ('START', 'FILL', 'DRAIN', 'FILL_OFF', 'DRAIN_OFF', 'STOP', 'RESET')
SOFT_LIMITS = dict(version=1, fill_seconds=180, drain_seconds=300, watchdog_ms=5000)
COMMAND_PRIORITY = "CASE command WHEN 'STOP' THEN 0 WHEN 'FILL_OFF' THEN 1 WHEN 'DRAIN_OFF' THEN 1 ELSE 2 END"
ADMIN_SESSION_TTL_SECONDS = 999 * 24 * 60 * 60


class Problem(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def validate_status(value):
    if not isinstance(value, dict):
        raise Problem(400, 'invalid_status')
    if value.get('project') != 'water_auto_exchange' or value.get('version') not in ('0.3.0', '0.4.0', '0.5.0', '0.5.1', '0.5.2', '0.5.3', '0.6.0', '0.7.0', '0.7.1', '0.7.2', '0.7.3', '0.7.4', '0.7.5', '0.7.6', '0.7.7', '0.8.0', '0.8.1', '0.8.2'):
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
    if result['version'] in ('0.7.0', '0.7.1', '0.7.2', '0.7.3', '0.7.4', '0.7.5', '0.7.6', '0.7.7', '0.8.0', '0.8.1', '0.8.2'):
        if value.get('control_mode') not in ('manual', 'automatic'):
            raise Problem(400, 'invalid_control_mode')
        result['control_mode'] = value['control_mode']
    concurrent = result['version'] in ('0.8.0', '0.8.1', '0.8.2') and result.get('control_mode') == 'manual'
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
            CREATE TABLE IF NOT EXISTS level_job (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS level_job_requests (id TEXT PRIMARY KEY, request TEXT NOT NULL, value TEXT NOT NULL);
        ''')
        # Restart never resurrects commands or claims that old telemetry is live.
        self.db.execute("UPDATE commands SET status='uncertain', result='server_restarted' WHERE status IN ('queued','delivered')")
        self.db.commit()
        self.status, self.seen, self.gateway = None, None, None
        self.gateway_seen = 0
        self.ws_gateway = None
        self.ws_grants = {}
        self.ws_grant_sequence = 0
        self.ws_ping_sequence = 0
        self.ws_activity_tick = None
        self.estimate_tick = None
        self._job_runtime = {}
        self._job_ticking = False
        self._job_saved_second = None
        saved_job = self.db.execute('SELECT value FROM level_job WHERE id=1').fetchone()
        self.level_job = json.loads(saved_job['value']) if saved_job else None
        if self.level_job and self.level_job['status'] == 'running':
            self.level_job.update(status='failed', phase='done', reason='server_restarted',
                                  updated_at=self.clock(), finished_at=self.clock())
            self.save_level_job(True)
        self.web_limits = False
        self.control_runs = dict(fill=None, drain=None)
        self.boundary_stops = dict(fill=None, drain=None)
        self.control_timeout = None
        self.control_uncertain = True
        self.connection_state, self.connection_since, self.connection_reason = 'offline', None, 'never_connected'
        saved = self.db.execute('SELECT value FROM device_snapshot WHERE id=1').fetchone()
        if saved:
            previous = json.loads(saved['value'])
            self.status, self.seen = previous.get('device'), previous.get('last_seen')
            self.web_limits = bool(self.status and self.status.get('version') == '0.8.2'
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
        self.simulation.setdefault('integrated_at', self.simulation['observed_at'])
        self.simulation.setdefault('estimate_basis', 'reported_outputs')
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
        self.output_runner = OutputRuns(self, Problem)

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

    def freeze_estimate(self):
        self.simulation['uncertain'] = True
        self.simulation['fill_since'] = self.simulation['drain_since'] = None
        self.save_simulation()

    def estimate_gap(self):
        return self.control_clock() - self.estimate_tick if self.estimate_tick is not None else None

    def integrate_estimate(self, maximum_gap=12):
        sim, now = self.simulation, self.clock()
        elapsed = self.estimate_gap()
        if elapsed is None and sim.get('integrated_at') is not None:
            elapsed = now - sim['integrated_at']
        active = sim['fill_on'] or sim['drain_on']
        if elapsed is not None and active and (elapsed < 0 or elapsed > maximum_gap):
            sim['uncertain'] = True
        if sim['observed_known'] and not sim['uncertain'] and sim['level'] is not None and elapsed is not None and elapsed > 0:
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
        sim['integrated_at'] = now
        self.estimate_tick = self.control_clock()

    def save_level_job(self, force=False):
        if self.level_job is None:
            return
        second = int(self.clock())
        if not force and second == self._job_saved_second:
            return
        self._job_saved_second = second
        self.db.execute('INSERT OR REPLACE INTO level_job VALUES(1,?)', (json.dumps(self.level_job),))
        request = dict(mode=self.level_job.get('mode', 'target'),
                       target_level=self.level_job['target_level'] if self.level_job.get('mode') != 'exchange' else None)
        self.db.execute('INSERT OR REPLACE INTO level_job_requests VALUES(?,?,?)',
                        (self.level_job['id'], json.dumps(request, sort_keys=True), json.dumps(self.level_job)))
        self.db.commit()

    def job_running(self):
        return self.level_job is not None and self.level_job['status'] == 'running'

    def output_estimate_trusted(self):
        sim = self.simulation
        return (type(sim['level']) in (int, float) and math.isfinite(sim['level'])
                and not sim['uncertain'] and sim['observed_known']
                and all(type(sim[key + '_seconds']) in (int, float) and math.isfinite(sim[key + '_seconds'])
                        and sim[key + '_seconds'] > 0 for key in ('fill', 'drain')))

    def output_boundary_reached(self, direction):
        if not self.output_estimate_trusted():
            return False
        return self.simulation['level'] >= 100 - 0.000001 if direction == 'fill' else self.simulation['level'] <= 0.000001

    def check_output_start(self, direction):
        if not self.output_estimate_trusted():
            raise Problem(409, 'output_requires_calibration')
        if self.output_boundary_reached(direction):
            raise Problem(409, direction + '_target_reached')

    def output_boundary_eta(self, direction):
        if not self.output_estimate_trusted():
            return None
        if self.output_boundary_reached(direction):
            return 0.0
        sim = self.simulation
        other = 'drain' if direction == 'fill' else 'fill'
        # Assume this route is enabled, including its next confirmed round;
        # the other route contributes only while its output is actually ON.
        rate = 100 / sim[direction + '_seconds'] - (100 / sim[other + '_seconds'] if sim[other + '_on'] else 0)
        difference = 100 - sim['level'] if direction == 'fill' else sim['level']
        return difference / rate if rate > 0.000000001 else None

    def job_refresh(self):
        job, runtime = self.level_job, self._job_runtime
        if not job:
            return None
        if job['status'] == 'running':
            current = max(0, runtime.get('stop_tick', self.control_clock()) - runtime['on_tick']) if runtime.get('on_tick') is not None else 0
            job['round_elapsed_seconds'] = current
            job['elapsed_seconds'] = runtime.get('completed_seconds', 0) + current
            job['current_level'] = self.simulation['level']
            job['remaining_seconds'] = self.job_remaining()
            job['progress'] = min(100, 100 * job['elapsed_seconds'] / job['total_seconds'])
            capacity = job['capacity_liters']
            job['estimated_liters'] = runtime.get('completed_liters', 0) + capacity * current / job[job['direction'] + '_seconds'] if capacity is not None else None
            job['updated_at'] = self.clock()
            job['round_remaining_seconds'] = max(0, runtime.get('stop_at', self.control_clock()) - self.control_clock()) if job['phase'] == 'active' else None
            self.save_level_job()
        return dict(job)

    def job_stage_remaining(self):
        job = self.level_job
        difference = job['target_level'] - self.simulation['level']
        remaining = difference if job['direction'] == 'fill' else -difference
        return max(0, remaining * job[job['direction'] + '_seconds'] / 100)

    def job_remaining(self):
        job = self.level_job
        following = job['fill_seconds'] if job.get('mode') == 'exchange' and job['stage'] == 'drain' else 0
        return self.job_stage_remaining() + following

    def job_finish(self, state, reason):
        if not self.job_running():
            return
        self.job_refresh()
        self.level_job.update(status=state, phase='done', reason=reason, finished_at=self.clock(), updated_at=self.clock(), round_remaining_seconds=None)
        # Revoke offers which have not been authorized yet. Already executed
        # commands retain their receipt path and their independent soft timer.
        for key in ('on_id', 'off_id'):
            command_id = self._job_runtime.get(key)
            if command_id and command_id not in self.ws_grants:
                self.db.execute("UPDATE commands SET status='cancelled', result='level_job_ended', finished=? WHERE id=? AND status IN ('queued','delivered')", (self.clock(), command_id))
        self.save_level_job(True)

    def job_command(self, command, ttl=8):
        command_id = secrets.token_hex(16)
        self.db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?)',
                        (command_id, command, self.clock(), self.clock() + ttl, 'queued', None, None))
        self.db.commit()
        return command_id

    def job_start_round(self):
        job, runtime = self.level_job, self._job_runtime
        job.update(phase='starting', round=job['round'] + 1, reason='running')
        runtime.update(on_tick=None, stop_at=None, off_id=None, start_until=self.control_clock() + 8)
        runtime.pop('stop_tick', None)
        runtime['on_id'] = self.job_command(job['direction'].upper())
        self.save_level_job(True)

    def start_level_job(self, value):
        if not isinstance(value, dict) or value.get('mode', 'target') not in ('target', 'exchange'):
            raise Problem(400, 'invalid_level_job')
        mode, request_id = value.get('mode', 'target'), value.get('id')
        target = value.get('target_level') if mode == 'target' else None
        if mode == 'target' and (type(target) not in (int, float) or not math.isfinite(target) or not 0 <= target <= 100):
            raise Problem(400, 'invalid_level_target')
        if (request_id is not None or mode == 'exchange') and (not isinstance(request_id, str) or len(request_id) != 32 or any(c not in '0123456789abcdef' for c in request_id)):
            raise Problem(400, 'invalid_level_job')
        request = json.dumps(dict(mode=mode, target_level=float(target) if target is not None else None), sort_keys=True)
        with self.lock:
            if request_id is not None:
                previous = self.db.execute('SELECT request,value FROM level_job_requests WHERE id=?', (request_id,)).fetchone()
                if previous:
                    if previous['request'] != request:
                        raise Problem(409, 'request_id_conflict')
                    return self.job_refresh() if self.level_job and self.level_job['id'] == request_id else json.loads(previous['value'])
            if self.job_running():
                raise Problem(409, 'level_job_active')
            if self.output_runner.running():
                raise Problem(409, 'output_run_active')
            self.expire()
            status = self.status or {}
            if not self.web_limits or self.ws_gateway is None:
                raise Problem(409, 'level_job_requires_web')
            if not self.online() or status.get('outputs_known') != '1' or status.get('fill') != '0' or status.get('drain') != '0' or status.get('state') not in ('IDLE', 'DONE') or status.get('ready') != '1' or status.get('overflow') != '0' or self.control_timeout or self.control_uncertain or any(self.control_runs.values()) or any(self.boundary_stops.values()):
                raise Problem(409, 'level_job_requires_idle')
            if self.simulation['level'] is None or self.simulation['uncertain']:
                raise Problem(409, 'level_job_requires_calibration')
            if self.db.execute("SELECT 1 FROM commands WHERE status IN ('queued','delivered')").fetchone():
                raise Problem(409, 'command_pending')
            if mode == 'exchange':
                target = 0 if self.simulation['level'] > 0.000001 else 100
            difference = target - self.simulation['level']
            if abs(difference) < 0.000001:
                raise Problem(409, 'level_target_reached')
            direction = 'fill' if difference > 0 else 'drain'
            seconds = abs(difference) * self.simulation[direction + '_seconds'] / 100
            limit = ROUND_SECONDS
            rounds = math.ceil(seconds / limit)
            if mode == 'exchange' and direction == 'drain':
                seconds += self.simulation['fill_seconds']
                rounds += math.ceil(self.simulation['fill_seconds'] / ROUND_SECONDS)
            self.level_job = dict(id=request_id or secrets.token_hex(16), mode=mode, stage=direction,
                                  status='running', phase='starting', direction=direction,
                                  target_level=float(target), start_level=self.simulation['level'], current_level=self.simulation['level'],
                                  round=0, elapsed_seconds=0, round_elapsed_seconds=0, round_limit_seconds=limit,
                                  total_seconds=seconds, remaining_seconds=seconds, estimated_rounds=rounds,
                                  fill_seconds=self.simulation['fill_seconds'], drain_seconds=self.simulation['drain_seconds'],
                                  capacity_liters=self.simulation['capacity_liters'],
                                  estimated_liters=0 if self.simulation['capacity_liters'] is not None else None,
                                  progress=0, estimated=True, created_at=self.clock(), updated_at=self.clock(), finished_at=None, reason='running')
            self._job_runtime = dict(session=self.ws_gateway, completed_seconds=0, completed_liters=0, cancel_reason=None)
            self.job_start_round()
            return self.job_refresh()

    def job_request_stop(self, cancel_reason=None, command_id=None):
        job, runtime = self.level_job, self._job_runtime
        stopping = job['phase'] == 'stopping'
        if stopping and cancel_reason and command_id is None:
            # A late cancellation keeps the pending OFF receipt and its original
            # deadline; it cannot grant another five seconds or start filling.
            runtime['cancel_reason'] = cancel_reason
            job['reason'] = cancel_reason
            self.save_level_job(True)
            return
        if cancel_reason:
            runtime['cancel_reason'] = cancel_reason
            for key in ('on_id', 'off_id'):
                old_command_id = runtime.get(key)
                if old_command_id and old_command_id not in self.ws_grants:
                    self.db.execute("UPDATE commands SET status='cancelled', result='level_job_cancelled', finished=? WHERE id=? AND status IN ('queued','delivered')", (self.clock(), old_command_id))
        job.update(phase='stopping', reason=cancel_reason or 'round_stopping')
        runtime['off_id'] = command_id or self.job_command('STOP' if cancel_reason else job['direction'].upper() + '_OFF', ttl=5)
        runtime['stop_until'] = min(runtime['stop_until'], self.control_clock() + 5) if stopping else self.control_clock() + 5
        self.save_level_job(True)

    def cancel_level_job(self, value=None):
        if value is not None and not isinstance(value, dict):
            raise Problem(400, 'invalid_level_job')
        job_id = value.get('job_id') if value else None
        if job_id is not None and (not isinstance(job_id, str) or len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id)):
            raise Problem(400, 'invalid_level_job')
        with self.lock:
            if self.level_job and self.level_job.get('mode') == 'exchange' and job_id is None:
                raise Problem(400, 'invalid_level_job')
            if job_id is not None and (self.level_job is None or self.level_job['id'] != job_id):
                raise Problem(409, 'level_job_changed')
            if self.job_running() and not self._job_runtime.get('cancel_reason'):
                self.job_request_stop('cancelled_by_user')
            return self.job_refresh()

    def job_observe(self, status, ack=None):
        if not self.job_running():
            return
        job, runtime = self.level_job, self._job_runtime
        if status['state'] == 'FAULT':
            self.job_finish('failed', 'device_fault')
            return
        if status['outputs_known'] != '1':
            self.job_finish('failed', 'output_unknown')
            return
        if job['phase'] == 'waiting' and (status['ready'] != '1' or status['overflow'] != '0'):
            self.job_finish('failed', 'device_not_ready')
            return
        if ack and ack.get('id') in (runtime.get('on_id'), runtime.get('off_id')) and ack.get('status') != 'succeeded':
            self.job_finish('failed', 'command_rejected')
            self.control_abort('level_job_command_rejected')
        direction = job['direction']
        other = 'drain' if direction == 'fill' else 'fill'
        if status[other] != '0':
            self.job_finish('failed', 'unexpected_output')
            self.control_abort('level_job_unexpected_output')
        if job['phase'] == 'stopping' and status['fill'] == status['drain'] == '0' and runtime.get('off_id') in self.ws_grants:
            runtime.setdefault('stop_tick', self.control_clock())
        if job['phase'] == 'starting' and status[direction] == '1' and runtime['on_id'] in self.ws_grants:
            runtime['on_tick'] = self.control_clock()
            grant = self.control_runs[direction]
            runtime['stop_at'] = min(runtime['on_tick'] + min(job['round_limit_seconds'], self.job_stage_remaining()), grant['until'] - 10)
            job['phase'] = 'active'
            self.save_level_job(True)
        elif job['phase'] == 'active' and status[direction] == '0':
            self.job_finish('failed', 'unexpected_output_off')
        elif job['phase'] == 'stopping' and ack and ack.get('id') == runtime['off_id'] and status['fill'] == status['drain'] == '0' and status['state'] in ('IDLE', 'DONE'):
            self.job_refresh()
            runtime['completed_seconds'] = job['elapsed_seconds']
            runtime['completed_liters'] = job['estimated_liters']
            runtime['on_tick'] = None
            if runtime.get('cancel_reason'):
                self.job_finish('cancelled', runtime['cancel_reason'])
            elif self.job_remaining() <= 0.00001:
                self.job_finish('completed', 'target_reached')
            else:
                transition = job.get('mode') == 'exchange' and job['stage'] == 'drain' and self.job_stage_remaining() <= 0.00001
                runtime['next_stage'] = 'fill' if transition else None
                job.update(phase='waiting', reason='between_stages' if transition else 'between_rounds')
                runtime['wait_until'] = self.control_clock() + 2
                self.save_level_job(True)

    def job_tick(self):
        if not self.job_running() or self._job_ticking:
            return
        self._job_ticking = True
        try:
            job, runtime = self.level_job, self._job_runtime
            if runtime.get('session') != self.ws_gateway:
                self.job_finish('failed', 'device_disconnected')
                return
            if not self.online():
                self.job_finish('failed', 'device_disconnected')
                self.control_abort('level_job_disconnected')
            active = self.simulation['fill_on'] or self.simulation['drain_on']
            gap = self.estimate_gap()
            if active and gap is not None and gap > 5:
                self.freeze_estimate()
            if self.simulation['uncertain']:
                self.job_finish('failed', 'estimate_uncertain')
                self.control_abort('level_job_estimate_uncertain')
            now = self.control_clock()
            if job['phase'] == 'starting' and now >= runtime['start_until']:
                self.job_finish('failed', 'start_timeout')
                self.control_abort('level_job_start_timeout')
            elif job['phase'] == 'active' and (now >= runtime['stop_at'] or self.job_stage_remaining() <= 0.00001):
                self.job_request_stop()
            elif job['phase'] == 'stopping' and now >= runtime['stop_until']:
                self.job_finish('failed', 'stop_unconfirmed')
                self.control_abort('level_job_stop_unconfirmed')
            elif job['phase'] == 'waiting' and now >= runtime['wait_until']:
                status = self.status or {}
                if status.get('outputs_known') != '1' or status.get('fill') != '0' or status.get('drain') != '0' or status.get('state') not in ('IDLE', 'DONE'):
                    self.job_finish('failed', 'output_unknown')
                elif status.get('ready') != '1' or status.get('overflow') != '0':
                    self.job_finish('failed', 'device_not_ready')
                elif self.job_remaining() <= 0.00001:
                    self.job_finish('completed', 'target_reached')
                else:
                    if runtime.pop('next_stage', None) == 'fill':
                        job.update(stage='fill', direction='fill', target_level=100.0, round_limit_seconds=ROUND_SECONDS)
                    self.job_start_round()
            self.job_refresh()
        finally:
            self._job_ticking = False

    def control_abort(self, reason):
        self.control_uncertain = True
        if self.ws_gateway:
            self.ws_close(self.ws_gateway, reason)
        raise Problem(409, reason)

    def control_start(self, key, command_id):
        if self.control_runs[key] is None:
            if not any(self.control_runs.values()):
                self.ws_activity_tick = self.control_clock()
            seconds = SOFT_LIMITS[key + '_seconds']
            now = self.clock()
            self.control_runs[key] = dict(since=now, deadline=now + seconds,
                                          until=self.control_clock() + seconds,
                                          confirmation_until=self.control_clock() + 8,
                                          command_id=command_id, observed_on=False,
                                          grant_sequence=self.ws_grants[command_id]['sequence'])

    def observe_control(self, status, ack=None):
        """Control authority is separate from calibrated water estimates.

        An execute grant reserves its deadline before any status/ACK arrives.
        An older all-OFF status cannot erase an unconfirmed execute grant.
        """
        if not self.web_limits:
            return
        if status['version'] != '0.8.2' or status.get('control_mode') != 'manual':
            self.control_abort('control_protocol_changed')
        if status['outputs_known'] != '1':
            self.control_abort('control_state_uncertain')
        all_off = status['fill'] == status['drain'] == '0'
        ack_row = self.db.execute('SELECT * FROM commands WHERE id=?', (ack.get('id'),)).fetchone() if ack else None
        ack_grant = self.ws_grants.get(ack.get('id')) if ack else None
        timeout = self.control_timeout
        normal_timeout_closed = False
        if timeout and all_off and status['state'] in ('IDLE', 'DONE'):
            # An old in-flight OFF must not acknowledge the new timeout.
            # Only a fresh successful execute receipt proves the close order.
            normal_timeout_closed = bool(
                ack_grant and ack.get('status') == 'succeeded'
                 and ack_grant['sequence'] > timeout['grant_sequence']
                 and (ack['id'] == timeout['id'] or ack_row['command'] in ('STOP', 'FILL_OFF', 'DRAIN_OFF')))
        if timeout and all_off and (status['state'] == 'FAULT' or normal_timeout_closed):
            # Normal STOP receipts allow another manual run. Actual device
            # faults remain latched until the user explicitly resets them.
            self.db.execute("UPDATE commands SET status=?, result=?, finished=? WHERE id=? AND status IN ('queued','delivered') AND id<>?",
                            ('succeeded' if normal_timeout_closed else 'cancelled',
                             'outputs_off_confirmed' if normal_timeout_closed else 'fault_confirmed',
                             self.clock(), timeout['id'], ack.get('id', '') if ack else ''))
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

    def observe_boundary_stops(self, status, ack=None):
        for direction, stop in self.boundary_stops.items():
            if not stop or not ack:
                continue
            grant = self.ws_grants.get(ack.get('id'))
            row = self.db.execute('SELECT command FROM commands WHERE id=?', (ack.get('id'),)).fetchone()
            current_stop = (ack.get('id') == stop['id'] or (grant and row and grant['sequence'] > stop['grant_sequence']
                            and row['command'] in ('STOP', direction.upper() + '_OFF')))
            if not current_stop:
                continue
            if ack.get('status') != 'succeeded':
                self.control_abort('boundary_stop_rejected')
            if status['outputs_known'] == '1' and status[direction] == '0':
                if ack.get('id') != stop['id']:
                    self.db.execute("UPDATE commands SET status='cancelled', result='superseded_by_confirmed_stop', finished=? WHERE id=? AND status IN ('queued','delivered')", (self.clock(), stop['id']))
                    self.db.commit()
                self.boundary_stops[direction] = None

    def tick_boundary_stops(self):
        if not self.web_limits or self.ws_gateway is None:
            return
        now = self.control_clock()
        for direction in ('fill', 'drain'):
            stop = self.boundary_stops[direction]
            if stop:
                if now >= stop['until']:
                    self.control_abort('boundary_stop_unconfirmed')
                continue
            if self.job_running() or self.output_runner.running(direction):
                continue
            # Include a reserved ON that has not reported its output yet.
            run = self.control_runs[direction]
            if run is None and not self.simulation[direction + '_on']:
                continue
            unconfirmed = run is not None and not run['observed_on'] and now >= run['confirmation_until']
            if unconfirmed:
                self.freeze_estimate()
            reason = 'start_unconfirmed' if unconfirmed else ('estimate_uncertain' if not self.output_estimate_trusted() else ('target_reached' if self.output_boundary_reached(direction) else None))
            if reason:
                pending = self.db.execute("SELECT id FROM commands WHERE command=? AND status IN ('queued','delivered')", (direction.upper(),)).fetchall()
                for row in pending:
                    # Retain granted control authority until OFF confirmation,
                    # while removing its missing ACK from the dispatch barrier.
                    state = 'uncertain' if row['id'] in self.ws_grants else 'cancelled'
                    self.db.execute("UPDATE commands SET status=?, result='output_boundary_stop', finished=? WHERE id=?", (state, self.clock(), row['id']))
                command_id = self.job_command(direction.upper() + '_OFF', ttl=5)
                self.boundary_stops[direction] = dict(id=command_id, until=now + 5, reason=reason, grant_sequence=self.ws_grant_sequence)

    def control_tick(self, session=None):
        """Called by the WS owner every 100 ms, even with no browser/pings."""
        with self.lock:
            if session is not None and session != self.ws_gateway:
                raise Problem(409, 'stale_session')
            try:
                self.output_runner.tick()
                self.job_tick()
                self.tick_boundary_stops()
            except Problem:
                if session is not None:
                    raise
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
            self.output_runner.end_all('control_timeout')
            # Revoke every unexecuted grant before the stop offer, including
            # delivered commands that have not yet reached their claim.
            self.db.execute("UPDATE commands SET status='cancelled', result='superseded_by_timeout', finished=? WHERE status IN ('queued','delivered')", (self.clock(),))
            command_id = secrets.token_hex(16)
            self.db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?)',
                            (command_id, 'STOP', self.clock(),
                             self.clock() + 1, 'queued', None, None))
            self.control_timeout = dict(key=key, id=command_id, confirm_until=now + 1,
                                        grant_sequence=self.ws_grant_sequence)
            self.db.commit()

    def control_limits_snapshot(self):
        status = self.status or {}
        recent = status.get('version') in ('0.8.1', '0.8.2')
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
        known = status['outputs_known'] == '1'
        # An unknown endpoint cannot confirm how long the previous output ran.
        if not known and (sim['level'] is not None or sim['fill_on'] or sim['drain_on']):
            sim['uncertain'] = True
        self.integrate_estimate(5 if self.web_limits else 12)
        sim['estimate_basis'] = 'reported_outputs'
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
            self.expire()
            pending_on = self.db.execute("SELECT 1 FROM commands WHERE status IN ('queued','delivered') AND command IN ('FILL','DRAIN','START')").fetchone()
            control_pending = self.control_timeout or any(self.control_runs.values()) or any(self.boundary_stops.values()) or (self.web_limits and self.control_uncertain)
            job_pending = self.output_runner.running() or (self.job_running() and (self.level_job['phase'] != 'waiting' or self.level_job.get('mode') == 'exchange'))
            if not self.online() or not self.status or self.status['outputs_known'] != '1' or self.status['fill'] != '0' or self.status['drain'] != '0' or pending_on or control_pending or job_pending:
                raise Problem(409, 'simulation_requires_idle')
            self.job_finish('cancelled', 'calibration_changed')
            if 'capacity_liters' not in value:
                capacity = self.simulation['capacity_liters']
            self.simulation.update(level=float(level), fill_seconds=float(fill_seconds),
                                   drain_seconds=float(drain_seconds), updated_at=self.clock(),
                                   uncertain=False, observed_at=self.clock(), observed_known=True,
                                   fill_on=False, drain_on=False, fill_since=None, drain_since=None,
                                   capacity_liters=float(capacity) if capacity is not None else None,
                                   calibrated_at=self.clock())
            self.simulation['integrated_at'] = self.clock()
            self.simulation['estimate_basis'] = 'reported_outputs'
            self.estimate_tick = self.control_clock()
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
        gap = self.estimate_gap() if self.web_limits else (self.clock() - sim['observed_at'] if sim['observed_at'] is not None else None)
        if active and gap is not None and gap > (5 if self.web_limits else 12):
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
                    estimate_basis=sim['estimate_basis'], estimated_at=sim.get('integrated_at'),
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
                        level_job=self.job_refresh(),
                        output_runs=self.output_runner.snapshot(),
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
            exchange_cancel = self.job_running() and self.level_job.get('mode') == 'exchange'
            if exchange_cancel and command not in ('STOP', 'FILL_OFF', 'DRAIN_OFF'):
                raise Problem(409, 'level_job_active')
            if self.web_limits and command in ('FILL', 'DRAIN'):
                self.check_output_start(command.lower())
                if self.boundary_stops[command.lower()]:
                    raise Problem(409, 'output_stop_pending')
            if self.control_timeout and command not in ('STOP', 'FILL_OFF', 'DRAIN_OFF'):
                raise Problem(409, 'control_timeout_pending')
            concurrent = s['version'] in ('0.8.0', '0.8.1', '0.8.2') and s.get('control_mode') == 'manual'
            if command in ('FILL_OFF', 'DRAIN_OFF') and not concurrent:
                raise Problem(409, 'firmware_upgrade_required')
            if command == 'DRAIN' and s['version'] not in ('0.6.0', '0.7.0', '0.7.1', '0.7.2', '0.7.3', '0.7.4', '0.7.5', '0.7.6', '0.7.7', '0.8.0', '0.8.1', '0.8.2'):
                raise Problem(409, 'firmware_upgrade_required')
            if command == 'START' and s.get('control_mode') == 'manual':
                raise Problem(409, 'automatic_mode_required')
            if command in ('FILL', 'DRAIN') and self.output_runner.running(command.lower()):
                raise Problem(409, 'output_run_active')
            if command in ('START', 'FILL', 'DRAIN'):
                allowed_states = ('IDLE', 'DONE', 'FILLING', 'DRAINING', 'EXCHANGING') if concurrent else ('IDLE', 'DONE')
                if s['ready'] != '1' or s['outputs_known'] != '1' or s['overflow'] != '0' or s['state'] not in allowed_states:
                    raise Problem(409, 'device_not_ready')
                if s.get('control_mode') != 'manual' and s['need_fill'] != ('1' if command == 'FILL' else '0'):
                    raise Problem(409, 'level_not_ready')
            if command == 'RESET' and s['state'] != 'FAULT':
                raise Problem(409, 'not_faulted')
            if command not in ('STOP', 'FILL_OFF', 'DRAIN_OFF'):
                cancelable = set()
                if self.job_running():
                    cancelable = {value for key, value in self._job_runtime.items() if key in ('on_id', 'off_id') and value not in self.ws_grants}
                pending = self.db.execute("SELECT id FROM commands WHERE status IN ('queued','delivered')").fetchall()
                if any(row['id'] not in cancelable for row in pending):
                    raise Problem(409, 'command_pending')
            if self.job_running() and not exchange_cancel:
                self.job_finish('cancelled', 'manual_override')
            if command == 'STOP':
                self.db.execute("UPDATE commands SET status='cancelled', result='superseded_by_stop', finished=? WHERE status='queued' AND id<>?", (self.clock(), self.control_timeout['id'] if self.control_timeout else ''))
            elif command in ('FILL_OFF', 'DRAIN_OFF'):
                self.db.execute("UPDATE commands SET status='cancelled', result='superseded_by_output_off', finished=? WHERE status='queued' AND command=?", (self.clock(), command[:-4]))
            now = self.clock()
            self.db.execute('INSERT INTO commands VALUES (?,?,?,?,?,?,?)', (request_id, command, now, now + 8, 'queued', None, None))
            if exchange_cancel:
                # Any explicit manual OFF ends the entire sequence. Wait for
                # all-OFF confirmation even when the requested route was idle.
                if command != 'STOP':
                    self.db.execute("UPDATE commands SET status='cancelled', result='exchange_stop_requested', finished=? WHERE id=?", (now, request_id))
                self.job_request_stop('manual_override', command_id=request_id if command == 'STOP' else None)
            self.output_runner.manual_command(command, request_id)
            self.db.commit()
            return dict(self.db.execute('SELECT * FROM commands WHERE id=?', (request_id,)).fetchone())

    def poll(self, gateway, status, ack=None):
        status = validate_status(status)
        if status['version'] == '0.8.2' and status.get('control_mode') == 'manual':
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
            self.ws_ping_sequence = 0
            self.ws_activity_tick = None
            self.control_runs = dict(fill=None, drain=None)
            self.boundary_stops = dict(fill=None, drain=None)
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
            soft = status['version'] == '0.8.2' and status.get('control_mode') == 'manual'
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
            self.ws_ping_sequence = 0
            self.ws_activity_tick = self.control_clock()
            self.web_limits = soft
            self.control_runs = dict(fill=None, drain=None)
            self.boundary_stops = dict(fill=None, drain=None)
            self.control_timeout = None
            self.control_uncertain = False
            self.db.execute("UPDATE commands SET status='uncertain', result='device_reconnected' WHERE status IN ('queued','delivered')")
            self.db.commit()
            self.status, self.seen = status, self.clock()
            self.observe_outputs(status)
            self.gateway_seen = self.seen
            self.connection_event(True, 'connected')
            return self.ws_gateway

    def ws_ping(self, session, sequence):
        """Extrapolate confirmed outputs during a continuous authorized lease.

        A bare ping is not a fresh output report. observed_at and the actual
        device snapshot retain their last report time and values.
        """
        with self.lock:
            if session is None or session != self.ws_gateway:
                raise Problem(409, 'stale_session')
            if type(sequence) not in (int, float) or not 1 <= sequence <= 9007199254740991 or sequence % 1:
                raise Problem(400, 'invalid_ping')
            if sequence <= self.ws_ping_sequence:
                self.control_tick(session)
                return False
            sim = self.simulation
            active = sim['fill_on'] or sim['drain_on']
            authorized = (self.web_limits and self.status and self.status['outputs_known'] == '1'
                          and all(not sim[key + '_on'] or (self.control_runs[key] and self.control_runs[key]['observed_on']) for key in ('fill', 'drain')))
            if active and authorized and sim['observed_known'] and not sim['uncertain']:
                self.integrate_estimate(5)
                sim['estimate_basis'] = 'confirmed_outputs_heartbeat_estimate'
                self.save_simulation()
            self.ws_ping_sequence = sequence
            self.ws_touch(session, effective_ping=True)
            return True

    def ws_touch(self, session, status=None, ack=None, effective_ping=False):
        with self.lock:
            if session is None or session != self.ws_gateway:
                raise Problem(409, 'stale_session')
            self.control_tick(session)
            fresh_ack = False
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
                else:
                    fresh_ack = True
            if status is not None:
                status = validate_status(status)
                if self.status and any(version == '0.8.2' for version in (self.status['version'], status['version'])) and (
                        self.status['version'] != status['version'] or self.status.get('control_mode') != status.get('control_mode')):
                    self.control_abort('control_protocol_changed')
                self.observe_control(status, ack)
                self.status = status
                self.observe_outputs(self.status)
            if ack is not None:
                self.db.execute("UPDATE commands SET status=?, result=?, finished=? WHERE id=? AND status IN ('delivered','uncertain')", (ack['status'], ack['result'], self.clock(), ack['id']))
                self.db.commit()
            if status is not None:
                self.job_observe(status, ack)
                self.output_runner.observe(status, ack)
                self.observe_boundary_stops(status, ack)
            if status is not None or fresh_ack or effective_ping:
                self.ws_activity_tick = self.control_clock()
            self.seen = self.gateway_seen = self.clock()
            self.connection_event(True, 'connected')

    def ws_offer(self, session):
        with self.lock:
            if session is None or session != self.ws_gateway:
                return None
            self.control_tick(session)
            self.expire()
            row = self.db.execute("SELECT * FROM commands WHERE status='queued' ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END, " + COMMAND_PRIORITY + ", created LIMIT 1", (self.control_timeout['id'] if self.control_timeout else '',)).fetchone()
            if not row:
                return None
            # Physical outputs may run together; ordinary command grants are
            # serialized so one lane's fresh receipt cannot obsolete the other.
            if row['command'] != 'STOP':
                pending = self.db.execute("SELECT id FROM commands WHERE status='delivered'").fetchall()
                for pending_row in pending:
                    grant = self.ws_grants.get(pending_row['id'])
                    if grant is None or (not grant['acked'] and grant['sequence'] == self.ws_grant_sequence):
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
            if self.web_limits and row['command'] in ('FILL', 'DRAIN') and command_id not in self.ws_grants:
                try:
                    self.check_output_start(row['command'].lower())
                except Problem as exc:
                    self.db.execute("UPDATE commands SET status='cancelled', result=?, finished=? WHERE id=?", (exc.message, self.clock(), command_id))
                    self.db.commit()
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
            self.output_runner.end_all('device_disconnected')
            self.job_finish('failed', 'device_disconnected')
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
        if path == '/water/api/level-job' and post:
            return self.respond(202, self.server.store.start_level_job(self.body()))
        if path == '/water/api/level-job/cancel' and post:
            return self.respond(200, self.server.store.cancel_level_job(self.body()))
        if path == '/water/api/output-run' and post:
            return self.respond(202, self.server.store.output_runner.start(self.body()))
        if path == '/water/api/output-run/cancel' and post:
            return self.respond(200, self.server.store.output_runner.cancel(self.body()))
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
