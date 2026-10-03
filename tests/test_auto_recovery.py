"""Automatic recovery through real Store authority; no physical IO/network."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from server.app import Problem, Store


STATUS = dict(project='water_auto_exchange', version='0.8.2', control_mode='manual',
              state='IDLE', reason='ready', ready='1', fill='0', drain='0',
              outputs_known='1', need_fill='unknown', overflow='0', cycle='0',
              overflow_protection='0')
COMM_FAULT = dict(STATUS, state='FAULT', reason='communication_timeout', ready='0')


class AutoRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'water.db')
        self.wall, self.mono = 1000.0, 1000.0
        self.store = Store(self.path, lambda: self.wall, lambda: self.mono)
        self.session = self.store.ws_open(STATUS)
        self.device, self.sequence = dict(STATUS), 0
        self.store.configure_simulation(dict(level=100, fill_seconds=120,
                                            drain_seconds=320, capacity_liters=5))

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def start(self, mode='exchange', request_id='a' * 32, target=50):
        body = dict(mode=mode, id=request_id)
        if mode == 'target':
            body['target_level'] = target
        return self.store.start_level_job(body)

    def advance(self, seconds, ping=True):
        until = self.mono + seconds
        while self.mono < until:
            step = min(1, until - self.mono)
            self.mono += step
            self.wall += step
            if ping:
                self.sequence += 1
                self.store.ws_ping(self.session, self.sequence)
            else:
                self.store.control_tick()

    def command(self, expected):
        offer = self.store.ws_offer(self.session)
        self.assertIsNotNone(offer, self.store.level_job)
        self.assertEqual(offer['command'], expected)
        execute = self.store.ws_claim(self.session, offer['id'])
        self.assertEqual(execute['type'], 'execute', execute)
        self.assertEqual(execute['command'], expected)
        return offer['id']

    def ack(self, command_id, **changes):
        self.device.update(changes)
        return self.store.ws_touch(self.session, self.device,
                                   dict(id=command_id, status='succeeded', result='OK'))

    def on(self, direction='drain'):
        command_id = self.command(direction.upper())
        self.ack(command_id, state='DRAINING' if direction == 'drain' else 'FILLING',
                 **{direction: '1'})
        return command_id

    def off(self, direction='drain'):
        command_id = self.command(direction.upper() + '_OFF')
        self.ack(command_id, state='IDLE', fill='0', drain='0', reason='stopped')
        return command_id

    def disconnect(self, reason='peer_disconnected'):
        self.store.ws_close(self.session, reason)
        self.assertEqual(self.store.level_job['status'], 'running')
        self.assertEqual(self.store.level_job['phase'], 'paused')
        self.assertIsNone(self.store.ws_gateway)

    def reconnect(self, status=STATUS):
        self.device = dict(status)
        self.session = self.store.ws_open(self.device)
        self.sequence = 0
        return self.session

    def stabilize(self):
        self.advance(6)
        self.store.control_tick(self.session)

    def resume(self, direction='drain', fault=False):
        self.reconnect(COMM_FAULT if fault else STATUS)
        self.stabilize()
        if fault:
            command_id = self.command('RESET')
            self.ack(command_id, state='IDLE', reason='reset', ready='1')
        self.advance(2)
        return self.on(direction)

    def assert_no_live_on(self):
        row = self.store.db.execute("SELECT 1 FROM commands WHERE command IN ('FILL','DRAIN') AND status IN ('queued','delivered')").fetchone()
        self.assertIsNone(row)

    def complete(self, maximum_seconds=1000):
        for _ in range(maximum_seconds * 10):
            if self.store.level_job['status'] != 'running':
                return self.store.snapshot()['level_job']
            self.advance(.1)
            offer = self.store.ws_offer(self.session)
            if offer is None:
                continue
            execute = self.store.ws_claim(self.session, offer['id'])
            self.assertEqual(execute['type'], 'execute', execute)
            command = execute['command']
            if command in ('FILL', 'DRAIN'):
                direction = command.lower()
                self.ack(offer['id'], state='FILLING' if direction == 'fill' else 'DRAINING',
                         fill='1' if direction == 'fill' else '0',
                         drain='1' if direction == 'drain' else '0')
            else:
                self.ack(offer['id'], state='IDLE', reason='stopped', ready='1', fill='0', drain='0')
        self.fail('task did not reach a terminal state: ' + repr(self.store.level_job))

    def test_disconnect_preserves_id_progress_and_unknown_water(self):
        job = self.start()
        self.on()
        self.advance(20)
        before = self.store.snapshot()['level_job']
        self.disconnect()
        paused = self.store.snapshot()['level_job']
        self.assertEqual(paused['id'], job['id'])
        self.assertGreaterEqual(paused['elapsed_seconds'], before['elapsed_seconds'])
        self.assertGreaterEqual(paused['progress'], before['progress'])
        self.assertTrue(self.store.simulation['uncertain'])
        self.assert_no_live_on()
        self.resume()
        self.assertTrue(self.store.simulation['uncertain'])
        self.assertEqual(self.store.level_job['id'], job['id'])
        self.assertEqual(self.store.level_job['stage'], 'drain')

    def test_communication_fault_requires_stable_new_pings_and_reset_receipt(self):
        self.start()
        self.on()
        self.advance(10)
        self.disconnect()
        self.reconnect(COMM_FAULT)
        self.assertIsNone(self.store.ws_offer(self.session))
        self.advance(4)
        self.assertIsNone(self.store.ws_offer(self.session))
        self.advance(2)
        reset_id = self.command('RESET')
        self.assert_no_live_on()
        self.advance(1)
        self.assert_no_live_on()
        self.ack(reset_id, state='IDLE', reason='reset', ready='1')
        self.advance(1)
        self.assert_no_live_on()
        self.advance(1)
        self.on()

    def test_offline_cancel_is_persistent_and_prevents_later_reset_or_on(self):
        job = self.start()
        self.on()
        self.advance(10)
        self.disconnect()
        self.store.cancel_level_job(dict(job_id=job['id']))
        self.assertEqual(self.store.level_job['status'], 'cancelled')
        self.store.db.close()
        self.store = Store(self.path, lambda: self.wall, lambda: self.mono)
        self.reconnect(COMM_FAULT)
        self.advance(10)
        self.assertEqual(self.store.level_job['status'], 'cancelled')
        self.assertIsNone(self.store.ws_offer(self.session))

    def test_old_session_close_and_ack_cannot_replace_recovered_authority(self):
        self.start()
        old_on = self.on()
        old_session = self.session
        self.advance(10)
        self.disconnect()
        new_on = self.resume()
        self.assertNotEqual(new_on, old_on)
        new_session = self.session
        self.store.ws_close(old_session)
        self.assertEqual(self.store.ws_gateway, new_session)
        with self.assertRaises(Problem):
            self.store.ws_touch(old_session, dict(STATUS, fill='1', state='FILLING'),
                                dict(id=old_on, status='succeeded', result='late'))
        self.assertEqual(self.store.status['drain'], '1')
        self.assertEqual(self.store.status['fill'], '0')

    def test_restart_checkpoints_active_task_without_replaying_old_command(self):
        self.start()
        old_on = self.on()
        self.advance(20)
        checkpoint = self.store.snapshot()['level_job']
        self.store.db.close()
        self.wall += 60
        self.mono += 60
        self.store = Store(self.path, lambda: self.wall, lambda: self.mono)
        job = self.store.snapshot()['level_job']
        self.assertEqual((job['id'], job['status'], job['phase']),
                         (checkpoint['id'], 'running', 'paused'))
        self.assertTrue(self.store.simulation['uncertain'])
        self.assertGreaterEqual(job['elapsed_seconds'], checkpoint['elapsed_seconds'])
        self.assert_no_live_on()
        new_on = self.resume(fault=True)
        self.assertNotEqual(new_on, old_on)

    def test_restart_between_confirmed_rounds_does_not_charge_unknown_tail(self):
        self.start()
        self.on()
        self.advance(60)
        self.off()
        original = copy.deepcopy(self.store.level_job['recovery'])
        self.store.db.close()
        self.store = Store(self.path, lambda: self.wall, lambda: self.mono)
        current = self.store.level_job['recovery']
        self.assertEqual(current['possible_extra_seconds'], original['possible_extra_seconds'])
        self.assertEqual(current['used_max'], original['used_max'])
        self.resume()

    def test_legacy_job_missing_marker_stays_failed_on_restart(self):
        self.start()
        saved = copy.deepcopy(self.store.level_job)
        saved.pop('recovery')
        self.store.db.execute('UPDATE level_job SET value=?', (json.dumps(saved),))
        self.store.db.commit()
        self.store.db.close()
        self.store = Store(self.path, lambda: self.wall, lambda: self.mono)
        self.reconnect()
        self.advance(10)
        self.assertEqual(self.store.level_job['status'], 'failed')
        self.assertIsNone(self.store.ws_offer(self.session))

    def test_true_faults_never_automatically_reset(self):
        for index, reason in enumerate(('overflow', 'fill_timeout', 'drain_timeout',
                                        'communication_timeout;io_error', 'io_error')):
            with self.subTest(reason=reason):
                self.store.configure_simulation(dict(level=100, fill_seconds=120, drain_seconds=320))
                self.start(request_id=format(index + 1, '032x'))
                self.on()
                self.advance(2)
                self.disconnect()
                self.reconnect(dict(COMM_FAULT, reason=reason))
                self.advance(10)
                self.assertEqual(self.store.level_job['status'], 'failed')
                self.assertIsNone(self.store.ws_offer(self.session))
                self.store.ws_close(self.session)
                self.reconnect()

    def test_overflow_seen_during_recovery_cannot_be_erased_by_next_safe_status(self):
        self.start()
        self.on()
        self.advance(3)
        self.disconnect()
        self.reconnect(COMM_FAULT)
        try:
            self.store.ws_touch(self.session, dict(COMM_FAULT, overflow='1'))
        except Problem:
            pass
        if self.store.ws_gateway is None:
            self.reconnect()
        else:
            self.store.ws_touch(self.session, STATUS)
        self.advance(10)
        self.assertEqual(self.store.level_job['status'], 'failed')
        self.assertIsNone(self.store.ws_offer(self.session))

    def test_target_job_uses_same_recovery_while_manual_run_does_not(self):
        self.start(mode='target', target=50)
        self.on()
        self.advance(10)
        self.disconnect()
        self.resume()
        self.assertEqual(self.store.level_job['mode'], 'target')
        self.store.cancel_level_job(dict(job_id=self.store.level_job['id']))
        self.ack(self.command('STOP'), state='IDLE', fill='0', drain='0')
        self.store.configure_simulation(dict(level=50, fill_seconds=120, drain_seconds=320))
        self.store.output_runner.start(dict(direction='fill', id='c' * 32))
        self.on('fill')
        self.advance(2)
        self.store.ws_close(self.session)
        self.assertEqual(self.store.output_runner.runs['fill']['status'], 'failed')
        self.reconnect()
        self.advance(10)
        self.assertIsNone(self.store.ws_offer(self.session))

    def test_missing_on_ack_deducts_possible_execution_without_replaying_command(self):
        self.start()
        old_on = self.command('DRAIN')
        self.advance(8, ping=False)
        self.assertEqual(self.store.level_job['phase'], 'paused')
        self.assertGreater(self.store.level_job['recovery']['used_max']['drain'], 0)
        new_on = self.resume()
        self.assertNotEqual(new_on, old_on)
        self.assertTrue(self.store.simulation['uncertain'])

    def test_unknown_tail_is_not_charged_twice_after_repeated_restart(self):
        self.start()
        self.on()
        self.advance(10)
        self.disconnect()
        initial = copy.deepcopy(self.store.level_job['recovery'])
        for _ in range(3):
            self.store.db.close()
            self.wall += 20
            self.mono += 20
            self.store = Store(self.path, lambda: self.wall, lambda: self.mono)
        current = self.store.level_job['recovery']
        self.assertEqual(current['used_max'], initial['used_max'])
        self.assertEqual(current['possible_extra_seconds'], initial['possible_extra_seconds'])

    def test_paused_job_blocks_calibration_and_new_manual_or_automatic_runs(self):
        self.start()
        self.on()
        self.advance(2)
        self.disconnect()
        self.reconnect()
        for operation in (
                lambda: self.store.configure_simulation(dict(level=50, fill_seconds=120, drain_seconds=320)),
                lambda: self.store.output_runner.start(dict(direction='fill', id='b' * 32)),
                lambda: self.store.start_level_job(dict(target_level=30, id='c' * 32)),
                lambda: self.store.enqueue('FILL', 'd' * 32)):
            with self.subTest(operation=operation), self.assertRaises(Problem):
                operation()
        self.assertEqual(self.store.level_job['status'], 'running')

    def test_duplicate_start_returns_paused_job_and_stale_cancel_is_rejected(self):
        original = self.start()
        self.on()
        self.advance(2)
        self.disconnect()
        duplicate = self.start()
        self.assertEqual((duplicate['id'], duplicate['phase']), (original['id'], 'paused'))
        with self.assertRaises(Problem):
            self.store.cancel_level_job(dict(job_id='b' * 32))
        self.assertEqual(self.store.level_job['status'], 'running')

    def test_wall_clock_rewind_does_not_extend_recovery_window(self):
        self.start()
        self.on()
        self.advance(2)
        self.disconnect()
        self.wall -= 86400
        self.advance(1801, ping=False)
        self.assertEqual(self.store.level_job['status'], 'failed')
        self.assert_no_live_on()

    def test_drain_interruption_then_fill_uses_residual_water_upper_bound(self):
        self.start()
        self.on()
        self.advance(20)
        self.disconnect()
        self.advance(12, ping=False)
        self.resume(fault=True)
        result = self.complete()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['reason'], 'completed_with_uncertainty')
        recovery = result['recovery']
        self.assertGreater(recovery['possible_extra_seconds'], 5)
        self.assertLess(recovery['used_max']['fill'], 120)
        self.assertLessEqual(recovery['level_max'], 100)
        self.assertLess(recovery['level_min'], recovery['level_max'])
        self.assertTrue(self.store.simulation['uncertain'])
        self.assertFalse(self.store.db.execute("SELECT 1 FROM commands WHERE command IN ('FILL_TIMEOUT','DRAIN_TIMEOUT')").fetchone())

    def test_interrupted_fill_never_restarts_full_frozen_budget(self):
        self.store.configure_simulation(dict(level=0, fill_seconds=120, drain_seconds=320))
        self.start()
        self.on('fill')
        self.advance(50)
        self.disconnect()
        self.advance(12, ping=False)
        recovery = copy.deepcopy(self.store.level_job['recovery'])
        self.assertGreaterEqual(recovery['used_max']['fill'], 50)
        self.resume('fill', fault=True)
        result = self.complete()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['recovery']['budgets']['fill'], recovery['budgets']['fill'])
        self.assertLessEqual(result['recovery']['used_max']['fill'], 120.00001)
        self.assertTrue(self.store.simulation['uncertain'])

    def test_pause_after_confirmed_off_does_not_add_possible_runtime(self):
        self.start()
        self.on()
        self.advance(60)
        self.off()
        before = copy.deepcopy(self.store.level_job['recovery'])
        self.disconnect()
        after = self.store.level_job['recovery']
        self.assertEqual(after['used_max'], before['used_max'])
        self.assertEqual(after['possible_extra_seconds'], before['possible_extra_seconds'])

    def test_repeated_invalid_ping_does_not_prove_connection_stability(self):
        self.start()
        self.on()
        self.advance(2)
        self.disconnect()
        self.reconnect(COMM_FAULT)
        self.sequence += 1
        self.store.ws_ping(self.session, self.sequence)
        for _ in range(6):
            self.wall += 1
            self.mono += 1
            self.store.ws_ping(self.session, self.sequence)
        self.assertIsNone(self.store.ws_offer(self.session))
        self.assert_no_live_on()

    def test_terminal_historical_failure_is_not_resurrected(self):
        self.start()
        saved = copy.deepcopy(self.store.level_job)
        saved.update(status='failed', phase='done', reason='device_disconnected')
        self.store.db.execute('UPDATE level_job SET value=?', (json.dumps(saved),))
        self.store.db.commit()
        self.store.db.close()
        self.store = Store(self.path, lambda: self.wall, lambda: self.mono)
        self.reconnect()
        self.advance(10)
        self.assertEqual(self.store.level_job['status'], 'failed')
        self.assertIsNone(self.store.ws_offer(self.session))

    def test_same_session_communication_fault_counts_time_before_off_report(self):
        self.start()
        self.on()
        self.advance(20)
        self.wall += 3
        self.mono += 3
        self.store.ws_touch(self.session, COMM_FAULT)
        self.assertEqual(self.store.level_job['phase'], 'paused')
        r = self.store.level_job['recovery']
        self.assertGreater(r['used_max']['drain'], r['used_min']['drain'])
        self.assertGreater(r['possible_extra_seconds'], 0)
        self.assertLessEqual(r['possible_extra_seconds'], 3.00001)

    def test_reconnect_with_unknown_output_permanently_ends_recovery(self):
        self.start()
        self.on()
        self.advance(2)
        self.disconnect()
        with self.assertRaises(Problem):
            self.store.ws_open(dict(STATUS, outputs_known='0'))
        self.assertEqual(self.store.level_job['status'], 'failed')
        self.reconnect()
        self.advance(10)
        self.assertIsNone(self.store.ws_offer(self.session))

    def test_old_stability_pings_cannot_authorize_reset_after_a_new_gap(self):
        self.start()
        self.on()
        self.advance(2)
        self.disconnect()
        self.reconnect(COMM_FAULT)
        self.advance(2)
        self.wall += 6
        self.mono += 6
        self.store.control_tick(self.session)
        self.assertIsNone(self.store.ws_offer(self.session))
        self.assert_no_live_on()

    def test_recovery_attempt_limit_is_finite_without_clearing_a_real_fault(self):
        self.start()
        self.on()
        for number in range(5):
            self.advance(1)
            self.disconnect()
            self.assertEqual(self.store.level_job['recovery']['attempts'], number + 1)
            self.resume()
        self.advance(1)
        self.store.ws_close(self.session)
        self.assertEqual(self.store.level_job['status'], 'failed')
        self.assert_no_live_on()

    def test_clock_rewind_cannot_shrink_unknown_runtime_when_wall_delta_stays_positive(self):
        self.store.configure_simulation(dict(level=0, fill_seconds=120, drain_seconds=320))
        self.start()
        self.on('fill')
        self.advance(10)
        self.disconnect()
        self.mono += 6
        self.wall += 1  # clock went back five seconds during a six-second outage
        self.reconnect()
        r = self.store.level_job['recovery']
        self.assertGreaterEqual(r['used_max']['fill'], 16)
        self.assertGreaterEqual(r['possible_extra_seconds'], 6)

    def test_fault_during_recovery_wait_cannot_skip_reset(self):
        self.start()
        self.on()
        self.advance(2)
        self.disconnect()
        self.reconnect()
        self.stabilize()
        self.assertEqual(self.store.level_job['phase'], 'recovering')
        self.store.ws_touch(self.session, COMM_FAULT)
        self.advance(2, ping=False)
        self.assert_no_live_on()
        self.assertIsNone(self.store.ws_gateway)
        self.assertEqual(self.store.status['state'], 'FAULT')
        self.reconnect(COMM_FAULT)
        self.stabilize()
        self.assertEqual(self.store.ws_offer(self.session)['command'], 'RESET')

    def test_pending_cancel_survives_crash_before_stop_receipt(self):
        job = self.start()
        self.on()
        self.advance(2)
        self.store.cancel_level_job(dict(job_id=job['id']))
        self.assertEqual(self.store.level_job['phase'], 'stopping')
        self.store.db.close()
        self.store = Store(self.path, lambda: self.wall, lambda: self.mono)
        self.reconnect(COMM_FAULT)
        self.advance(10)
        self.assertEqual(self.store.level_job['status'], 'cancelled')
        self.assertIsNone(self.store.ws_offer(self.session))


if __name__ == '__main__':
    unittest.main()
