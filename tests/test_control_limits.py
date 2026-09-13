"""Server-owned deadlines: no browser, calibration or ON telemetry required."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.app import Store, Problem, SOFT_LIMITS

STATUS = dict(project='water_auto_exchange', version='0.8.2', control_mode='manual',
              state='IDLE', reason='ready', ready='1', fill='0', drain='0', outputs_known='1',
              need_fill='unknown', overflow='0', cycle='0', overflow_protection='0')


class ControlLimitTests(unittest.TestCase):
    def setUp(self):
        self.initial = dict(STATUS)
        self.wall = self.mono = 1000
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'water.db')
        self.store = Store(self.path, lambda: self.wall, lambda: self.mono)
        self.session = self.store.ws_open(self.initial)
        self.status = dict(self.initial)
        self.number = 0

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds

    def grant(self, command):
        self.number += 1
        command_id = format(self.number, '032x')
        self.store.enqueue(command, command_id)
        offer = self.store.ws_offer(self.session)
        self.assertEqual(offer['command'], command)
        execute = self.store.ws_claim(self.session, command_id)
        self.assertEqual(execute['type'], 'execute')
        return command_id

    def ack(self, command_id, **changes):
        self.status.update(changes)
        self.store.ws_touch(self.session, self.status,
                            dict(id=command_id, status='succeeded', result='OK'))

    def start(self, key='fill'):
        command_id = self.grant(key.upper())
        self.ack(command_id, **{key: '1', 'state': 'FILLING' if key == 'fill' else 'DRAINING'})
        return command_id

    def until(self, target):
        # Device heartbeats keep the session alive. There is no browser poll.
        while self.mono < target:
            self.advance(min(1, target - self.mono))
            self.store.ws_touch(self.session)

    def test_authorization_without_ack_or_on_status_still_expires(self):
        self.grant('FILL')
        self.advance(1)
        self.store.ws_touch(self.session, self.initial)  # old in-flight OFF report
        self.assertEqual(self.store.control_runs['fill']['since'], 1000)
        self.until(1180)
        offer = self.store.ws_offer(self.session)
        self.assertEqual(offer['command'], 'STOP')
        self.assertEqual(self.store.control_limits_snapshot()['timeout_pending'], 'fill')

    def test_rejected_start_with_confirmed_off_clears_grant(self):
        command_id = self.grant('FILL')
        self.store.ws_touch(self.session, self.initial,
                            dict(id=command_id, status='rejected', result='not_ready'))
        self.assertIsNone(self.store.control_runs['fill'])
        self.until(1301)
        self.assertIsNone(self.store.ws_offer(self.session))

    def test_confirmed_stop_clears_unobserved_start(self):
        self.grant('FILL')
        command_id = self.grant('STOP')
        self.ack(command_id)
        self.assertIsNone(self.store.control_runs['fill'])

    def test_repeated_on_and_other_output_do_not_extend_first_deadline(self):
        self.start()
        self.until(1040)
        command_id = self.grant('FILL')
        self.ack(command_id)
        command_id = self.grant('DRAIN')
        self.ack(command_id, drain='1', state='EXCHANGING')
        limits = self.store.snapshot()['control_limits']
        self.assertEqual(limits['fill_deadline'], 1180)
        self.assertEqual(limits['drain_deadline'], 1340)
        self.until(1060)
        command_id = self.grant('DRAIN_OFF')
        self.ack(command_id, drain='0', state='FILLING')
        self.assertEqual(self.store.control_runs['fill']['since'], 1000)
        self.assertIsNone(self.store.control_runs['drain'])
        self.until(1180)
        self.assertEqual(self.store.ws_offer(self.session)['command'], 'STOP')

    def test_drain_gets_full_five_minutes_independent_of_browser(self):
        self.start('drain')
        self.until(1299.9)
        self.assertIsNone(self.store.ws_offer(self.session))
        self.until(1300)
        self.assertEqual(self.store.ws_offer(self.session)['command'], 'STOP')

    def test_water_calibration_uncertainty_does_not_pause_control(self):
        self.store.configure_simulation(dict(level=50, fill_seconds=200, drain_seconds=200))
        self.start()
        self.until(1013)
        self.assertTrue(self.store.snapshot()['simulation']['uncertain'])
        self.assertFalse(self.store.snapshot()['control_limits']['uncertain'])
        self.until(1180)
        self.assertEqual(self.store.ws_offer(self.session)['command'], 'STOP')

    def test_timeout_revokes_delivered_open_grants_and_cannot_be_publicly_queued(self):
        self.start()
        self.until(1179)
        self.store.enqueue('DRAIN', 'e' * 32)
        self.assertEqual(self.store.ws_offer(self.session)['command'], 'DRAIN')
        self.until(1180)
        self.assertEqual(self.store.ws_claim(self.session, 'e' * 32)['type'], 'expired')
        for command in ('FILL_TIMEOUT', 'DRAIN_TIMEOUT'):
            with self.assertRaises(Problem) as caught:
                self.store.enqueue(command, 'f' * 32)
            self.assertEqual(caught.exception.message, 'invalid_command')
        # A user STOP cannot cancel the higher-priority deadline STOP.
        self.store.enqueue('STOP', 'd' * 32)
        self.assertEqual(self.store.ws_offer(self.session)['command'], 'STOP')

    def test_unconfirmed_timeout_breaks_heartbeat_lease_and_requires_new_idle_session(self):
        self.start()
        self.until(1180)
        offer = self.store.ws_offer(self.session)
        self.assertEqual(self.store.ws_claim(self.session, offer['id'])['command'], 'STOP')
        self.advance(1)
        with self.assertRaises(Problem) as caught:
            self.store.ws_touch(self.session)
        self.assertEqual(caught.exception.message, 'control_stop_unconfirmed')
        self.assertIsNone(self.store.ws_gateway)
        with self.assertRaises(Problem):
            self.store.ws_open(self.status)
        self.session = self.store.ws_open(self.initial)
        self.assertIsNone(self.store.ws_offer(self.session))
        self.assertEqual(self.store.ws_claim(self.session, offer['id'])['type'], 'expired')

    def test_fault_confirmation_resolves_timeout_and_reset_starts_fresh_run(self):
        self.start()
        self.until(1180)
        offer = self.store.ws_offer(self.session)
        self.store.ws_claim(self.session, offer['id'])
        self.ack(offer['id'], fill='0', state='FAULT', reason='communication_timeout')
        self.assertIsNone(self.store.control_timeout)
        row = self.store.db.execute('SELECT status FROM commands WHERE id=?', (offer['id'],)).fetchone()
        self.assertEqual(row['status'], 'succeeded')
        reset = self.grant('RESET')
        self.ack(reset, state='IDLE', reason='ready')
        self.advance(5)
        self.start()
        self.assertEqual(self.store.control_runs['fill']['since'], 1185)
        self.assertEqual(self.store.ws_claim(self.session, offer['id'])['type'], 'expired')

    def test_unowned_active_or_unknown_reports_disconnect_immediately(self):
        for status in (dict(self.initial, fill='1', state='FILLING'), dict(self.initial, outputs_known='0')):
            with self.assertRaises(Problem):
                self.store.ws_touch(self.session, status)
            self.assertIsNone(self.store.ws_gateway)
            self.session = self.store.ws_open(self.initial)

    def test_reconnect_and_server_restart_never_grant_active_output_new_time(self):
        self.start()
        old_session = self.session
        self.store.ws_close(self.session)
        with self.assertRaises(Problem):
            self.store.ws_open(self.status, old_session)
        self.store.db.close()
        self.store = Store(self.path, lambda: self.wall, lambda: self.mono)
        self.assertFalse(self.store.snapshot()['online'])
        with self.assertRaises(Problem):
            self.store.ws_open(self.status)
        self.session = self.store.ws_open(self.initial)
        self.assertIsNone(self.store.ws_offer(self.session))
        self.assertIsNone(self.store.control_runs['fill'])

    def test_wall_clock_rollback_does_not_extend_control_deadline(self):
        self.start()
        self.advance(30)
        self.wall -= 3600
        limits = self.store.snapshot()['control_limits']
        self.assertEqual(limits['fill_deadline'] - self.wall, 150)
        self.assertEqual(self.wall - limits['fill_on_since'], 30)
        self.mono += 150
        self.store.control_tick(self.session)
        self.assertEqual(self.store.ws_offer(self.session)['command'], 'STOP')
        self.assertEqual(self.store.control_limits_snapshot()['fill_deadline'], self.wall)

    def test_old_firmware_keeps_firmware_owned_limits_and_new_http_gateway_is_rejected(self):
        self.store.ws_close(self.session)
        for version, fill, drain in (('0.8.0', 120, 120), ('0.8.1', 180, 300)):
            self.session = self.store.ws_open(dict(self.initial, version=version))
            limits = self.store.snapshot()['control_limits']
            self.assertEqual((limits['source'], limits['fill_seconds'], limits['drain_seconds']),
                             ('firmware', fill, drain))
            self.store.ws_close(self.session)
        with self.assertRaises(Problem) as caught:
            self.store.poll('a' * 32, self.initial)
        self.assertEqual(caught.exception.message, 'firmware_requires_wss')
        self.assertEqual(SOFT_LIMITS['watchdog_ms'], 5000)

    def test_switching_to_old_http_gateway_drops_web_control_metadata(self):
        self.store.ws_close(self.session)
        self.store.poll('a' * 32, dict(self.initial, version='0.8.1'))
        self.assertEqual(self.store.snapshot()['control_limits']['source'], 'firmware')
        self.assertIsNone(self.store.control_timeout)

    def test_changing_firmware_protocol_requires_a_new_capability_handshake(self):
        with self.assertRaises(Problem) as caught:
            self.store.ws_touch(self.session, dict(self.initial, version='0.8.1'))
        self.assertEqual(caught.exception.message, 'control_protocol_changed')
        self.assertIsNone(self.store.ws_gateway)

    def assert_old_off_ack_is_inert(self, confirm_new_on):
        self.start()
        off_id = self.grant('FILL_OFF')
        self.ack(off_id, fill='0', state='IDLE')
        new_id = self.grant('FILL')
        if confirm_new_on:
            self.ack(new_id, fill='1', state='FILLING')
        old_seen = self.store.seen
        previous_status = copy.deepcopy(self.store.status)
        previous_simulation = copy.deepcopy(self.store.simulation)
        self.advance(1)
        accepted = self.store.ws_touch(self.session, self.initial,
                                       dict(id=off_id, status='succeeded', result='OK FILL_OFF'))
        self.assertFalse(accepted)
        self.assertEqual(self.store.control_runs['fill']['command_id'], new_id)
        self.assertEqual(self.store.status, previous_status)
        self.assertEqual(self.store.simulation, previous_simulation)
        self.assertEqual(self.store.seen, old_seen)
        self.until(1180)
        self.assertEqual(self.store.ws_offer(self.session)['command'], 'STOP')

    def test_replayed_successful_off_ack_cannot_erase_new_unconfirmed_grant(self):
        self.assert_old_off_ack_is_inert(False)

    def test_replayed_successful_off_ack_cannot_erase_new_confirmed_on(self):
        self.assert_old_off_ack_is_inert(True)

    def test_late_first_off_ack_uses_execute_order_not_creation_time(self):
        # Normal dispatch now serializes offers. Retain the lower-level defense
        # against previously delivered frames claiming out of order: creation
        # timestamps/row ids do not prove whether an OFF closes a later grant.
        fill_id, off_id = 'a' * 32, 'b' * 32
        self.store.enqueue('FILL', fill_id)
        self.assertEqual(self.store.ws_offer(self.session)['id'], fill_id)
        self.store.enqueue('FILL_OFF', off_id)
        self.assertIsNone(self.store.ws_offer(self.session))
        self.store.db.execute("UPDATE commands SET status='delivered' WHERE id=?", (off_id,))
        self.store.db.commit()
        self.store.ws_claim(self.session, off_id)
        self.store.ws_claim(self.session, fill_id)
        self.store.ws_touch(self.session, dict(self.initial, fill='1', state='FILLING'))
        previous_simulation = copy.deepcopy(self.store.simulation)
        activity_tick = self.store.ws_activity_tick
        self.advance(1)
        self.store.ws_touch(self.session, self.initial,
                            dict(id=off_id, status='succeeded', result='OK FILL_OFF'))
        self.assertEqual(self.store.control_runs['fill']['command_id'], fill_id)
        self.assertEqual(self.store.status['fill'], '1')
        self.assertEqual(self.store.simulation, previous_simulation)
        self.assertEqual(self.store.ws_activity_tick, activity_tick)
        self.assertEqual(self.store.db.execute('SELECT status FROM commands WHERE id=?', (off_id,)).fetchone()['status'], 'succeeded')
        self.until(1180)
        self.assertEqual(self.store.ws_offer(self.session)['command'], 'STOP')

    def test_expired_ack_cannot_replace_telemetry_or_erase_pending_deadline(self):
        command_id = self.grant('FILL')
        previous_simulation = copy.deepcopy(self.store.simulation)
        self.advance(9)
        self.assertFalse(self.store.ws_touch(self.session, dict(self.initial, fill='1', state='FILLING'),
                                             dict(id=command_id, status='succeeded', result='OK FILL')))
        self.assertEqual(self.store.status, self.initial)
        self.assertEqual(self.store.simulation, previous_simulation)
        self.assertEqual(self.store.control_runs['fill']['command_id'], command_id)

    def test_previous_session_ack_is_not_an_authorization_in_new_session(self):
        command_id = self.start()
        self.store.ws_close(self.session)
        self.session = self.store.ws_open(self.initial)
        with self.assertRaises(Problem) as caught:
            self.store.ws_touch(self.session, self.initial, dict(id=command_id, status='succeeded', result='OK'))
        self.assertEqual(caught.exception.message, 'unclaimed_ack')
        self.assertEqual(self.store.status, self.initial)

    def timeout_offer(self, key='fill'):
        self.start(key)
        self.until(1180 if key == 'fill' else 1300)
        offer = self.store.ws_offer(self.session)
        self.assertEqual(offer['command'], 'STOP')
        return offer

    def test_normal_limit_stops_both_outputs_and_allows_a_new_manual_run_without_reset(self):
        self.start()
        other_id = self.grant('DRAIN')
        self.ack(other_id, drain='1', state='EXCHANGING')
        self.until(1180)
        offer = self.store.ws_offer(self.session)
        self.store.ws_claim(self.session, offer['id'])
        self.ack(offer['id'], fill='0', drain='0', state='IDLE', reason='stopped')
        self.assertIsNone(self.store.control_timeout)
        self.assertEqual(self.store.control_runs, dict(fill=None, drain=None))
        self.assertIsNone(self.store.ws_offer(self.session))  # never auto-reopen
        self.advance(.25)
        self.start()
        self.assertEqual(self.store.control_runs['fill']['since'], 1180.25)
        self.assertEqual(self.store.control_runs['fill']['until'], 1360.25)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM commands WHERE command='RESET'").fetchone())

    def test_drain_stop_receipt_confirms_idle_without_fault(self):
        offer = self.timeout_offer('drain')
        self.store.ws_claim(self.session, offer['id'])
        self.ack(offer['id'], drain='0', state='IDLE', reason='stopped')
        self.assertIsNone(self.store.control_timeout)
        self.assertEqual(self.store.status['state'], 'IDLE')
        self.start()

    def test_stop_requires_receipt_and_replayed_receipt_cannot_fault_next_run(self):
        offer = self.timeout_offer()
        self.store.ws_claim(self.session, offer['id'])
        idle = dict(self.initial, reason='stopped')
        self.store.ws_touch(self.session, idle)
        self.status = idle
        self.assertIsNotNone(self.store.control_timeout)
        self.ack(offer['id'])
        self.assertIsNone(self.store.control_timeout)
        row = self.store.db.execute('SELECT status,result FROM commands WHERE id=?', (offer['id'],)).fetchone()
        self.assertEqual((row['status'], row['result']), ('succeeded', 'OK'))
        self.start()
        self.assertFalse(self.store.ws_touch(self.session, dict(idle, state='FAULT'),
                                             dict(id=offer['id'], status='succeeded', result='OK STOP')))
        self.assertEqual(self.store.status['state'], 'FILLING')
        self.assertIsNotNone(self.store.control_runs['fill'])

    def test_plain_off_status_without_receipt_cannot_confirm_timeout(self):
        offer = self.timeout_offer()
        for reason in ('ready', 'stopped'):
            self.store.ws_touch(self.session, dict(self.initial, reason=reason))
            self.assertIsNotNone(self.store.control_timeout)
        self.store.ws_claim(self.session, offer['id'])
        for reason in ('ready', 'stopped'):
            self.store.ws_touch(self.session, dict(self.initial, reason=reason))
            self.assertIsNotNone(self.store.control_timeout)
        self.advance(1)
        with self.assertRaises(Problem) as caught:
            self.store.ws_touch(self.session)
        self.assertEqual(caught.exception.message, 'control_stop_unconfirmed')

    def test_fresh_stop_ack_after_deadline_can_confirm_before_timeout_claim(self):
        offer = self.timeout_offer()
        stop_id = self.grant('STOP')
        self.ack(stop_id, fill='0', state='IDLE', reason='stopped')
        self.assertIsNone(self.store.control_timeout)
        self.assertEqual(self.store.ws_claim(self.session, offer['id'])['type'], 'expired')
        self.start()

    def test_output_off_waits_for_timeout_receipt_before_execution(self):
        offer = self.timeout_offer('drain')
        self.number += 1
        off_id = format(self.number, '032x')
        self.store.enqueue('DRAIN_OFF', off_id)
        self.assertIsNone(self.store.ws_offer(self.session))
        self.store.ws_claim(self.session, offer['id'])
        self.ack(offer['id'], drain='0', state='IDLE', reason='stopped')
        self.assertEqual(self.store.ws_offer(self.session)['id'], off_id)
        self.assertEqual(self.store.ws_claim(self.session, off_id)['type'], 'execute')
        self.ack(off_id, drain='0', state='IDLE', reason='drain_stopped')
        self.assertIsNone(self.store.control_timeout)
        self.assertEqual(self.store.ws_claim(self.session, offer['id'])['type'], 'expired')

    def test_duplicate_off_ack_during_timeout_does_not_confirm_close(self):
        self.start()
        off_id = self.grant('FILL_OFF')
        self.ack(off_id, fill='0', state='IDLE')
        self.start()
        self.until(1180)
        self.store.ws_touch(self.session, self.initial,
                            dict(id=off_id, status='succeeded', result='OK FILL_OFF'))
        self.assertIsNotNone(self.store.control_timeout)


if __name__ == '__main__':
    unittest.main()
