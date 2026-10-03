"""Persistent, bounded recovery for newly created server-owned level jobs.

Intervals describe calibrated software estimates, never measured water flow.
Manual output runs and jobs written before this opt-in format do not resume.
"""
RECOVERY_PHASES = ('paused', 'recovering', 'resetting')
MAX_ATTEMPTS = 5
WAIT_SECONDS = 1800
UNKNOWN_TAIL_SECONDS = 10.1
LEVEL_EPSILON = 0.000001
IDLE_STABILITY_GAP_SECONDS = 35


def recovery_enabled(job):
    r = job.get('recovery') if isinstance(job, dict) else None
    return (isinstance(r, dict) and r.get('enabled') is True and r.get('version') == 1
            and all(key in job for key in ('stage', 'fill_seconds', 'drain_seconds', 'capacity_liters'))
            and all(isinstance(r.get(key), dict) and all(direction in r[key] for direction in ('fill', 'drain'))
                    for key in ('budgets', 'used_min', 'used_max'))
            and all(key in r for key in ('level_min', 'level_max', 'checkpoint_at', 'attempts')))


class AutoRecovery:
    def __init__(self, store):
        self.store = store
        # Recovery may finish a persisted cancellation during construction;
        # job_refresh must already be able to reach this instance.
        store.auto_recovery = self
        self._wait_tick = store.control_clock()
        self._tail_tick = None
        if self.enabled() and store.job_running():
            store._job_runtime = dict(completed_seconds=store.level_job.get('elapsed_seconds', 0),
                                      completed_liters=store.level_job.get('estimated_liters', 0), cancel_reason=None)
            self.pause('server_restarted', restarting=True)

    def enabled(self):
        return recovery_enabled(self.store.level_job)

    def waiting(self):
        return self.enabled() and self.store.job_running() and self.store.level_job['phase'] in RECOVERY_PHASES

    def initialize_new(self):
        job = self.store.level_job
        direction = job['direction']
        budgets = dict(fill=0.0, drain=0.0)
        budgets[direction] = abs(job['target_level'] - job['start_level']) * job[direction + '_seconds'] / 100
        if job.get('mode') == 'exchange' and direction == 'drain':
            budgets['fill'] = job['fill_seconds']
        job['recovery'] = dict(enabled=True, version=1, attempts=0, last_reason=None,
                               deadline=None, wait_remaining_seconds=None,
                               uncertain=False, resumed=False,
                               level_min=job['start_level'], level_max=job['start_level'],
                               used_min=dict(fill=0.0, drain=0.0),
                               used_max=dict(fill=0.0, drain=0.0), budgets=budgets,
                               possible_extra_seconds=0.0, authorized=False,
                               granted_elapsed=0.0,
                               calibrated_at=self.store.simulation.get('calibrated_at'),
                               checkpoint_at=self.store.clock(), highwater_at=self.store.clock())

    def add_duration(self, direction, lower, upper):
        job, r = self.store.level_job, self.store.level_job['recovery']
        lower, upper = max(0, lower), max(0, upper)
        r['used_min'][direction] += lower
        r['used_max'][direction] += upper
        scale = 100 / job[direction + '_seconds']
        if direction == 'fill':
            r['level_min'] = min(100, r['level_min'] + lower * scale)
            r['level_max'] = min(100, r['level_max'] + upper * scale)
        else:
            r['level_min'] = max(0, r['level_min'] - upper * scale)
            r['level_max'] = max(0, r['level_max'] - lower * scale)

    def checkpoint(self, proven=False):
        s = self.store
        if not self.enabled() or not s.job_running() or self.waiting():
            return
        job, rt, r = s.level_job, s._job_runtime, s.level_job['recovery']
        if rt.get('on_tick') is not None:
            tick = s.control_clock() if proven else s.ws_activity_tick
            if tick is not None:
                end = min(tick, rt.get('stop_tick', tick))
                upper = max(0, end - rt['on_tick'])
                lower = max(0, min(end, rt.get('off_grant_tick', end)) - rt['on_tick'])
                delta_min = max(0, lower - rt.get('accounted_min_seconds', 0))
                delta_max = max(0, upper - rt.get('accounted_seconds', 0))
                self.add_duration(job['direction'], delta_min, delta_max)
                r['possible_extra_seconds'] += max(0, delta_max - delta_min)
                rt['accounted_min_seconds'], rt['accounted_seconds'] = lower, upper
                r['checkpoint_at'] = s.clock() - max(0, s.control_clock() - end)
        elif proven:
            r['checkpoint_at'] = s.clock()
            if r.get('authorized') and rt.get('grant_tick') is not None:
                r['granted_elapsed'] = max(r.get('granted_elapsed', 0), s.control_clock() - rt['grant_tick'])
        r['highwater_at'] = max(r.get('highwater_at', s.clock()), s.clock())

    def authorize(self, command_id):
        s = self.store
        if not self.enabled() or not s.job_running():
            return
        if s._job_runtime.get('off_id') == command_id:
            # The OFF may execute immediately after its grant. The interval
            # until its fresh report contributes only to the upper bound.
            s._job_runtime.setdefault('off_grant_tick', s.control_clock())
            s.save_level_job(True)
            return
        if s._job_runtime.get('on_id') != command_id:
            return
        r = s.level_job['recovery']
        if s._job_runtime.get('granted_id') == command_id:
            return
        s._job_runtime['granted_id'] = command_id
        s._job_runtime['grant_tick'] = s.control_clock()
        r.update(authorized=True, grant_at=s.clock(), checkpoint_at=s.clock(), granted_elapsed=0.0)
        s.save_level_job(True)  # Persist before the execute frame can leave.

    def owns(self, command_id):
        s = self.store
        status = s.status or {}
        return (self.enabled() and s.job_running() and not self.waiting()
                and s.level_job['recovery'].get('resumed')
                and s.level_job['phase'] == 'starting'
                and command_id == s._job_runtime.get('on_id')
                and s.ws_gateway == s._job_runtime.get('session')
                and status.get('outputs_known') == '1' and status.get('fill') == status.get('drain') == '0'
                and status.get('overflow') == '0' and status.get('ready') == '1'
                and status.get('state') in ('IDLE', 'DONE')
                and s.ws_activity_tick is not None and s.control_clock() - s.ws_activity_tick <= 5
                and self.stage_remaining() > 0)

    def stage_remaining(self):
        s, job = self.store, self.store.level_job
        r, direction = job['recovery'], job['direction']
        budget = max(0, r['budgets'][direction] - r['used_max'][direction])
        difference = (job['target_level'] - r['level_max']) if direction == 'fill' else (r['level_min'] - job['target_level'])
        capacity = 0 if difference <= LEVEL_EPSILON else difference * job[direction + '_seconds'] / 100
        return min(budget, max(0, capacity))

    def pause(self, reason, restarting=False, confirmed_off=False):
        s = self.store
        if not self.enabled() or not s.job_running():
            return False
        job, rt, r = s.level_job, s._job_runtime, s.level_job['recovery']
        cancel_reason = rt.get('cancel_reason') or r.get('cancel_reason')
        if cancel_reason:
            s.job_finish('cancelled', cancel_reason)
            return True
        already = job['phase'] in RECOVERY_PHASES
        retry = already and (reason == 'reset_unconfirmed' or (reason == 'communication_timeout' and job['phase'] == 'recovering'))
        if not already:
            if not restarting:
                self.checkpoint()
            # Only an execute authority can have opened a physical output.
            if r.get('authorized'):
                checkpoint = r.get('checkpoint_at', s.clock())
                self._tail_tick = None if restarting else (rt['on_tick'] + rt.get('accounted_seconds', 0) if rt.get('on_tick') is not None else rt.get('grant_tick'))
                # A granted start without a receipt may precede the first
                # checkpoint; its unknown startup interval is included too.
                start_slop = max(0, r.get('granted_elapsed', 0)) if job['phase'] == 'starting' else 0
                startup = start_slop
                gap = max(0, s.control_clock() - self._tail_tick) if self._tail_tick is not None else UNKNOWN_TAIL_SECONDS
                extra = (min(UNKNOWN_TAIL_SECONDS, gap) if confirmed_off else UNKNOWN_TAIL_SECONDS) + startup
                before_low, before_high = r['level_min'], r['level_max']
                self.add_duration(job['direction'], 0, extra)
                r['possible_extra_seconds'] += extra
                r.update(uncertain=True, tail_direction=job['direction'], tail_seconds=extra,
                         tail_checkpoint_at=checkpoint,
                         tail_level_min_before=before_low, tail_level_max_before=before_high,
                         tail_startup_slop=startup)
                if confirmed_off:
                    r.pop('tail_seconds', None)
                s.simulation['uncertain'] = True
                s.save_simulation()
            r['attempts'] += 1
            r.update(deadline=s.clock() + WAIT_SECONDS, wait_remaining_seconds=float(WAIT_SECONDS),
                     highwater_at=max(r.get('highwater_at', s.clock()), s.clock()))
        elif restarting:
            highwater = max(s.clock(), r.get('highwater_at', s.clock()))
            r['wait_remaining_seconds'] = min(r.get('wait_remaining_seconds', WAIT_SECONDS),
                                              max(0, r['deadline'] - highwater))
        elif retry:
            r['attempts'] += 1
            r['last_reason'] = reason
        if r['attempts'] > MAX_ATTEMPTS:
            s.job_finish('failed', 'recovery_limit_reached')
            return True
        for key in ('on_id', 'off_id', 'reset_id'):
            command_id = rt.get(key)
            if command_id:
                s.db.execute("UPDATE commands SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'uncertain' END, result='recovery_paused', finished=? WHERE id=? AND status IN ('queued','delivered')", (s.clock(), command_id))
        if not already:
            r['last_reason'] = reason
        elif restarting:
            r['last_reason'] = 'server_restarted'
        r.update(authorized=False, startup_slop=0)
        if any(r['used_max'][direction] > r['used_min'][direction] + 0.000001 for direction in ('fill', 'drain')):
            r['uncertain'] = True
            s.simulation['uncertain'] = True
            s.save_simulation()
        job['elapsed_seconds'] = max(job.get('elapsed_seconds', 0), sum(r['used_min'].values()))
        if job.get('capacity_liters') is not None:
            job['estimated_liters'] = sum(job['capacity_liters'] * r['used_min'][direction] / job[direction + '_seconds'] for direction in ('fill', 'drain'))
        job.update(phase='paused', reason=r['last_reason'], round_remaining_seconds=None)
        s._job_runtime = dict(completed_seconds=job.get('elapsed_seconds', 0),
                              completed_liters=job.get('estimated_liters', 0), cancel_reason=None)
        self._wait_tick = s.control_clock()
        s.save_level_job(True)
        return True

    def truncate_tail(self):
        s, r = self.store, self.store.level_job['recovery']
        extra = r.get('tail_seconds')
        if extra is None:
            return
        # Only a same-process monotonic endpoint can refine this interval.
        # Wall-clock corrections (including positive deltas after a rewind)
        # and a restart cannot prove a shorter physical operating duration.
        elapsed = s.control_clock() - self._tail_tick if self._tail_tick is not None else None
        upper = min(extra, max(0, elapsed) + r.get('tail_startup_slop', 0)) if elapsed is not None and elapsed >= 0 else extra
        reduction = max(0, extra - upper)
        direction = r['tail_direction']
        r['used_max'][direction] = max(r['used_min'][direction], r['used_max'][direction] - reduction)
        r['possible_extra_seconds'] = max(0, r['possible_extra_seconds'] - reduction)
        if direction == 'fill':
            r['level_max'] = min(100, r['tail_level_max_before'] + upper * 100 / s.level_job['fill_seconds'])
        else:
            r['level_min'] = max(0, r['tail_level_min_before'] - upper * 100 / s.level_job['drain_seconds'])
        r.pop('tail_seconds', None)
        self._tail_tick = None

    def observe(self, status, ack=None, new_session=False):
        s = self.store
        if not self.enabled() or not s.job_running():
            return False
        bad = None
        if status.get('version') != '0.8.2' or status.get('control_mode') != 'manual':
            bad = 'control_protocol_changed'
        elif status['overflow'] != '0':
            bad = 'overflow'
        elif status['outputs_known'] != '1':
            bad = 'output_unknown'
        elif status['state'] == 'UNCONFIGURED':
            bad = 'device_not_ready'
        elif status['state'] == 'FAULT' and status['reason'] != 'communication_timeout':
            bad = 'device_fault'
        if bad:
            s.job_finish('failed', bad)
            return False
        if status['state'] == 'FAULT' and not self.waiting():
            self.pause('communication_timeout', confirmed_off=status['fill'] == status['drain'] == '0')
            s.ws_close(s.ws_gateway, 'communication_timeout')
            return True
        if not self.waiting():
            self.checkpoint(proven=True)
            return False
        if status['fill'] != '0' or status['drain'] != '0':
            s.job_finish('failed', 'unexpected_output')
            return True
        if s.level_job['phase'] == 'recovering' and status['state'] == 'FAULT':
            self.pause('communication_timeout', confirmed_off=True)
            s.ws_close(s.ws_gateway, 'communication_timeout')
            return True
        rt, r = s._job_runtime, s.level_job['recovery']
        if new_session:
            self.truncate_tail()
            rt.update(recovery_session=s.ws_gateway, stable_since=s.control_clock(), stable_pings=0, stable_last_ping=None)
        if rt.get('recovery_session') != s.ws_gateway:
            return True
        if s.level_job['phase'] == 'resetting' and ack and ack.get('id') == rt.get('reset_id'):
            if ack.get('status') != 'succeeded' or status['state'] not in ('IDLE', 'DONE') or status['ready'] != '1':
                s.job_finish('failed', 'recovery_reset_rejected')
            else:
                s.level_job.update(phase='recovering', reason='recovery_waiting')
                rt['resume_at'] = s.control_clock() + 2
                s.save_level_job(True)
        return True

    def ping(self):
        s = self.store
        if self.waiting() and s._job_runtime.get('recovery_session') == s.ws_gateway:
            rt, now = s._job_runtime, s.control_clock()
            previous = rt.get('stable_last_ping')
            # Frozen 0.8.2 uses 30 s pings in IDLE/FAULT. A gap beyond that
            # interval plus its 5 s communication margin requires new proof.
            if previous is not None and now - previous > IDLE_STABILITY_GAP_SECONDS:
                rt.update(stable_since=now, stable_pings=0)
                rt.pop('resume_at', None)
            rt['stable_pings'] = rt.get('stable_pings', 0) + 1
            rt['stable_last_ping'] = now
        else:
            self.checkpoint(proven=True)
        if self.enabled() and s.job_running():
            s.save_level_job(True)

    def tick(self):
        s = self.store
        if not self.waiting():
            return False
        job, rt, r = s.level_job, s._job_runtime, s.level_job['recovery']
        now = s.control_clock()
        elapsed = max(0, now - self._wait_tick)
        self._wait_tick = now
        r['highwater_at'] = max(r.get('highwater_at', s.clock()), s.clock())
        r['wait_remaining_seconds'] = min(max(0, r['wait_remaining_seconds'] - elapsed),
                                          max(0, r['deadline'] - r['highwater_at']))
        if r['wait_remaining_seconds'] <= 0:
            s.job_finish('failed', 'recovery_wait_expired')
            return True
        if (any(s.simulation[key] != job[key] for key in ('fill_seconds', 'drain_seconds', 'capacity_liters'))
                or s.simulation.get('calibrated_at') != r.get('calibrated_at')):
            s.job_finish('failed', 'calibration_changed')
            return True
        if not s.online() or rt.get('recovery_session') != s.ws_gateway:
            s.save_level_job()
            return True
        status = s.status
        if not self.observe(status):
            return True
        if job['status'] != 'running':
            return True
        rt = s._job_runtime
        if not s.online() or rt.get('recovery_session') != s.ws_gateway:
            return True
        if job['phase'] == 'resetting':
            if now >= rt['reset_until']:
                self.pause('reset_unconfirmed')
                s.ws_close(s.ws_gateway, 'reset_unconfirmed')
            return True
        if rt.get('stable_last_ping') is None or now - rt['stable_last_ping'] > 5:
            return True
        if rt.get('resume_at') is None:
            if rt.get('stable_pings', 0) < 2 or now - rt['stable_since'] < 5:
                s.save_level_job()
                return True
            if status['state'] == 'FAULT':
                rt['reset_id'] = s.job_command('RESET')
                rt['reset_until'] = now + 8
                job.update(phase='resetting', reason='communication_reset')
                s.save_level_job(True)
                return True
            if status['state'] not in ('IDLE', 'DONE') or status['ready'] != '1':
                return True
            job.update(phase='recovering', reason='recovery_waiting')
            rt['resume_at'] = now + 2
            s.save_level_job(True)
            return True
        if now < rt['resume_at']:
            return True
        if status['state'] not in ('IDLE', 'DONE') or status['ready'] != '1':
            s.job_finish('failed', 'device_not_ready')
            return True
        r['resumed'] = True
        if self.stage_remaining() <= 0.00001:
            if job.get('mode') == 'exchange' and job['stage'] == 'drain':
                job.update(stage='fill', direction='fill', target_level=100.0)
            if self.stage_remaining() <= 0.00001:
                s.job_finish('completed', 'completed_with_uncertainty' if r['uncertain'] else 'target_reached')
                return True
        s._job_runtime = dict(session=s.ws_gateway, completed_seconds=job['elapsed_seconds'],
                              completed_liters=job['estimated_liters'], cancel_reason=None)
        s.job_start_round()
        return True
