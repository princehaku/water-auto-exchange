"""Two independently scheduled, finite output runs over existing WSS commands."""
import json
import math


class OutputRuns:
    def __init__(self, store, problem):
        self.store, self.problem = store, problem
        self.runs = dict(fill=None, drain=None)
        self.runtime = {}
        self.ticking = False
        self.saved_second = {}
        store.db.execute('CREATE TABLE IF NOT EXISTS output_runs (id TEXT PRIMARY KEY, direction TEXT NOT NULL, value TEXT NOT NULL)')
        for direction in self.runs:
            row = store.db.execute('SELECT value FROM output_runs WHERE direction=? ORDER BY rowid DESC LIMIT 1', (direction,)).fetchone()
            if row:
                run = json.loads(row['value'])
                self.runs[direction] = run
                if run['status'] == 'running':
                    run.update(status='failed', phase='done', reason='server_restarted', finished_at=store.clock(), updated_at=store.clock(), round_remaining_seconds=None)
                    self.save(run, True)

    def running(self, direction=None):
        if direction is None:
            return any(self.running(key) for key in self.runs)
        return self.runs[direction] is not None and self.runs[direction]['status'] == 'running'

    def save(self, run, force=False):
        second = int(self.store.clock())
        if not force and self.saved_second.get(run['id']) == second:
            return
        self.saved_second[run['id']] = second
        self.store.db.execute('INSERT OR IGNORE INTO output_runs VALUES(?,?,?)',
                              (run['id'], run['direction'], json.dumps(run)))
        self.store.db.execute('UPDATE output_runs SET value=? WHERE id=?', (json.dumps(run), run['id']))
        self.store.db.commit()

    def refresh(self, direction):
        run = self.runs[direction]
        if run is None:
            return None
        if self.running(direction):
            runtime, now = self.runtime[direction], self.store.control_clock()
            current = max(0, runtime.get('stop_tick', now) - runtime['on_tick']) if runtime.get('on_tick') is not None else 0
            run.update(round_elapsed_seconds=current,
                       elapsed_seconds=runtime.get('completed_seconds', 0) + current,
                       updated_at=self.store.clock())
            run['remaining_seconds'] = max(0, run['total_seconds'] - run['elapsed_seconds'])
            run['progress'] = min(100, 100 * run['elapsed_seconds'] / run['total_seconds'])
            run['estimated_liters'] = run['capacity_liters'] * run['elapsed_seconds'] / run['calibration_seconds'] if run['capacity_liters'] is not None else None
            run['round_remaining_seconds'] = max(0, runtime['stop_at'] - now) if run['phase'] == 'active' else None
            current_budget = run['round_remaining_seconds'] or 0
            future = max(0, run['remaining_seconds'] - current_budget)
            if future <= 0.00001:
                future = 0
            run['estimated_rounds'] = max(run['round'], run['round'] + math.ceil(future / run['round_limit_seconds']) if run['phase'] != 'starting' else run['round'] - 1 + math.ceil(run['remaining_seconds'] / run['round_limit_seconds']))
            self.save(run)
        return dict(run)

    def snapshot(self):
        return {key: self.refresh(key) for key in self.runs}

    def revoke_unclaimed(self, direction):
        runtime = self.runtime[direction]
        for key in ('on_id', 'off_id'):
            command_id = runtime.get(key)
            if command_id and command_id not in self.store.ws_grants:
                self.store.db.execute("UPDATE commands SET status='cancelled', result='output_run_ended', finished=? WHERE id=? AND status IN ('queued','delivered')", (self.store.clock(), command_id))

    def finish(self, direction, status, reason):
        if not self.running(direction):
            return
        self.refresh(direction)
        run = self.runs[direction]
        run.update(status=status, phase='done', reason=reason, finished_at=self.store.clock(), updated_at=self.store.clock(), round_remaining_seconds=None)
        if status == 'completed':
            run['estimated_rounds'] = run['round']
        self.revoke_unclaimed(direction)
        self.save(run, True)

    def end_all(self, reason):
        for direction in self.runs:
            self.finish(direction, 'failed', reason)

    def start(self, value):
        direction = value.get('direction') if isinstance(value, dict) else None
        request_id = value.get('id') if isinstance(value, dict) else None
        if not isinstance(direction, str) or direction not in self.runs or not isinstance(request_id, str) or len(request_id) != 32 or any(c not in '0123456789abcdef' for c in request_id):
            raise self.problem(400, 'invalid_output_run')
        store = self.store
        with store.lock:
            previous = store.db.execute('SELECT direction,value FROM output_runs WHERE id=?', (request_id,)).fetchone()
            if previous:
                if previous['direction'] != direction:
                    raise self.problem(409, 'request_id_conflict')
                if self.runs[direction] and self.runs[direction]['id'] == request_id:
                    return self.refresh(direction)
                return json.loads(previous['value'])
            store.control_tick()
            store.expire()
            if self.running(direction):
                raise self.problem(409, 'output_run_active')
            if store.job_running():
                raise self.problem(409, 'level_job_active')
            status = store.status or {}
            if not store.web_limits or store.ws_gateway is None:
                raise self.problem(409, 'output_run_requires_web')
            other_state = 'DRAINING' if direction == 'fill' else 'FILLING'
            if not store.online() or status.get('outputs_known') != '1' or status.get(direction) != '0' or status.get('state') not in ('IDLE', 'DONE', other_state) or status.get('ready') != '1' or status.get('overflow') != '0' or store.control_uncertain or store.control_timeout or store.control_runs[direction]:
                raise self.problem(409, 'output_run_requires_idle')
            own_commands = (direction.upper(), direction.upper() + '_OFF', 'START', 'STOP', 'RESET')
            if store.db.execute("SELECT 1 FROM commands WHERE status IN ('queued','delivered') AND command IN (?,?,?,?,?)", own_commands).fetchone():
                raise self.problem(409, 'command_pending')
            duration = store.simulation[direction + '_seconds']
            if type(duration) not in (int, float) or not math.isfinite(duration) or not 1 <= duration <= 86400:
                raise self.problem(409, 'output_run_requires_calibration')
            limit = 170 if direction == 'fill' else 290
            run = dict(id=request_id, mode='calibrated_duration', direction=direction, status='running', phase='starting',
                       calibration_seconds=duration, total_seconds=duration, elapsed_seconds=0, remaining_seconds=duration,
                       round=0, round_elapsed_seconds=0, round_remaining_seconds=None, round_limit_seconds=limit,
                       estimated_rounds=math.ceil(duration / limit), capacity_liters=store.simulation['capacity_liters'],
                       estimated_liters=0 if store.simulation['capacity_liters'] is not None else None, progress=0,
                       reason='running', estimated=True, created_at=store.clock(), updated_at=store.clock(), finished_at=None)
            self.runs[direction] = run
            self.runtime[direction] = dict(session=store.ws_gateway, completed_seconds=0, cancel_reason=None)
            self.start_round(direction)
            return self.refresh(direction)

    def start_round(self, direction):
        run, runtime = self.runs[direction], self.runtime[direction]
        run.update(phase='starting', round=run['round'] + 1, reason='running')
        runtime.update(on_tick=None, stop_at=None, off_id=None, start_until=self.store.control_clock() + 8)
        runtime.pop('stop_tick', None)
        runtime['on_id'] = self.store.job_command(direction.upper())
        self.save(run, True)

    def request_stop(self, direction, reason=None, command_id=None):
        run, runtime = self.runs[direction], self.runtime[direction]
        stopping = run['phase'] == 'stopping'
        command = self.store.db.execute('SELECT command FROM commands WHERE id=?', (command_id,)).fetchone() if command_id else None
        if stopping and reason and (command is None or command['command'] != 'STOP'):
            # Cancelling a round that is already closing changes its outcome,
            # not its OFF receipt or five-second confirmation deadline.
            runtime['cancel_reason'] = reason
            run['reason'] = reason
            self.save(run, True)
            return
        if reason:
            runtime['cancel_reason'] = reason
            self.revoke_unclaimed(direction)
        run.update(phase='stopping', reason=reason or 'round_stopping')
        runtime['off_id'] = command_id or self.store.job_command(direction.upper() + '_OFF', ttl=5)
        runtime['stop_until'] = min(runtime['stop_until'], self.store.control_clock() + 5) if stopping else self.store.control_clock() + 5
        self.save(run, True)

    def cancel(self, value):
        direction = value.get('direction') if isinstance(value, dict) else None
        run_id = value.get('run_id') if isinstance(value, dict) else None
        if not isinstance(direction, str) or direction not in self.runs or not isinstance(run_id, str) or len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
            raise self.problem(400, 'invalid_output_run')
        with self.store.lock:
            run = self.runs[direction]
            if run is None or run['id'] != run_id:
                raise self.problem(409, 'stale_output_run')
            if self.running(direction) and not self.runtime[direction].get('cancel_reason'):
                self.request_stop(direction, 'cancelled_by_user')
            return self.refresh(direction)

    def manual_command(self, command, command_id):
        for direction in self.runs:
            if self.running(direction) and command in ('STOP', direction.upper() + '_OFF'):
                self.request_stop(direction, 'manual_override', command_id)

    def observe(self, status, ack=None):
        for direction in self.runs:
            if not self.running(direction):
                continue
            run, runtime = self.runs[direction], self.runtime[direction]
            if status['state'] == 'FAULT':
                self.finish(direction, 'failed', 'device_fault')
                continue
            if status['outputs_known'] != '1':
                self.store.control_abort('control_state_uncertain')
            if ack and ack.get('id') in (runtime.get('on_id'), runtime.get('off_id')) and ack.get('status') != 'succeeded':
                self.finish(direction, 'failed', 'command_rejected')
                self.store.control_abort('output_run_command_rejected')
            if run['phase'] == 'stopping' and status[direction] == '0' and runtime.get('off_id') in self.store.ws_grants:
                runtime.setdefault('stop_tick', self.store.control_clock())
            if run['phase'] == 'stopping' and status[direction] == '1' and runtime.get('on_tick') is None and runtime.get('on_id') in self.store.ws_grants:
                # A cancellation can precede the opening receipt. Account for
                # its confirmed ON interval without resuming the cancelled run.
                runtime['on_tick'] = self.store.control_clock()
            if run['phase'] == 'starting' and status[direction] == '1' and runtime['on_id'] in self.store.ws_grants:
                runtime['on_tick'] = self.store.control_clock()
                remaining = max(0, run['total_seconds'] - runtime['completed_seconds'])
                runtime['stop_at'] = min(runtime['on_tick'] + min(run['round_limit_seconds'], remaining), self.store.control_runs[direction]['until'] - 10)
                run['phase'] = 'active'
                self.save(run, True)
            elif run['phase'] == 'active' and status[direction] == '0':
                self.finish(direction, 'failed', 'unexpected_output_off')
            elif run['phase'] == 'stopping' and ack and ack.get('id') == runtime['off_id'] and ack.get('status') == 'succeeded' and status[direction] == '0':
                self.refresh(direction)
                runtime['completed_seconds'] = run['elapsed_seconds']
                runtime['on_tick'] = None
                if runtime.get('cancel_reason'):
                    self.finish(direction, 'cancelled', runtime['cancel_reason'])
                elif run['remaining_seconds'] <= 0.00001:
                    self.finish(direction, 'completed', 'duration_completed')
                else:
                    run.update(phase='waiting', reason='between_rounds')
                    runtime['wait_until'] = self.store.control_clock() + 2
                    self.save(run, True)
            if self.running(direction) and run['phase'] == 'waiting' and (status['ready'] != '1' or status['overflow'] != '0'):
                self.finish(direction, 'failed', 'device_not_ready')

    def tick(self):
        if self.ticking or not self.running():
            return
        self.ticking = True
        try:
            store, now = self.store, self.store.control_clock()
            if not store.online():
                store.control_abort('output_run_disconnected')
            if any(store.control_runs.values()) and (store.ws_activity_tick is None or now - store.ws_activity_tick > 5):
                store.control_abort('output_run_communication_timeout')
            for direction in self.runs:
                if not self.running(direction):
                    continue
                run, runtime = self.runs[direction], self.runtime[direction]
                if runtime['session'] != store.ws_gateway:
                    self.finish(direction, 'failed', 'device_disconnected')
                    continue
                if run['phase'] == 'starting' and now >= runtime['start_until']:
                    self.finish(direction, 'failed', 'start_timeout')
                    store.control_abort('output_run_start_timeout')
                elif run['phase'] == 'active' and now >= runtime['stop_at']:
                    self.request_stop(direction)
                elif run['phase'] == 'stopping' and now >= runtime['stop_until']:
                    self.finish(direction, 'failed', 'stop_unconfirmed')
                    store.control_abort('output_run_stop_unconfirmed')
                elif run['phase'] == 'waiting' and now >= runtime['wait_until']:
                    status = store.status or {}
                    if status.get('outputs_known') != '1' or status.get(direction) != '0' or store.control_runs[direction] or status.get('ready') != '1' or status.get('overflow') != '0':
                        self.finish(direction, 'failed', 'device_not_ready')
                    else:
                        self.start_round(direction)
                self.refresh(direction)
        finally:
            self.ticking = False
