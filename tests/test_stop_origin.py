"""Manual intent is distinct from automatic round/protection shutdown."""
import copy
import tempfile
import unittest
from pathlib import Path

from server.app import Problem, Store

STATUS = dict(project='water_auto_exchange', version='0.8.2', control_mode='manual',
              state='IDLE', reason='ready', ready='1', fill='0', drain='0', outputs_known='1',
              need_fill='unknown', overflow='0', cycle='0', overflow_protection='0')


class StopOriginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'water.db')
        self.now = 1000.0
        self.store = Store(self.path, lambda: self.now)
        self.session = self.store.ws_open(STATUS)
        self.status, self.sequence = dict(STATUS), 0
        self.store.configure_simulation(dict(level=100, fill_seconds=120, drain_seconds=320))

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def start(self, mode='exchange'):
        return self.store.start_level_job(dict(mode=mode, id='a' * 32, **({'target_level': 50} if mode == 'target' else {})))

    def command(self, expected):
        offer = self.store.ws_offer(self.session)
        self.assertEqual(offer['command'], expected)
        self.store.ws_claim(self.session, offer['id'])
        return offer['id']

    def ack(self, command_id, **changes):
        self.status.update(changes)
        self.store.ws_touch(self.session, self.status, dict(id=command_id, status='succeeded', result='OK'))

    def on(self, direction='drain'):
        command_id = self.command(direction.upper())
        self.ack(command_id, **{direction: '1'}, state='DRAINING' if direction == 'drain' else 'FILLING')
        return command_id

    def pulse(self, seconds):
        until = self.now + seconds
        while self.now < until:
            self.now = min(until, self.now + 1)
            self.sequence += 1
            self.store.ws_ping(self.session, self.sequence)

    def origin(self, command_id):
        row = self.store.db.execute('SELECT * FROM commands WHERE id=?', (command_id,)).fetchone()
        return self.store.command_record(row)

    def intent(self, job_id=None):
        return dict(job_id=job_id or self.store.level_job['id'], intent='manual_stop', source='web_ui',
                    event='click', button='exchange-stop-button')

    def test_internal_system_stop_confirms_then_continues_both_automatic_modes(self):
        for mode in ('exchange', 'target'):
            with self.subTest(mode=mode):
                if mode == 'target':
                    self.tearDown()
                    self.setUp()
                job = self.start(mode)
                self.on()
                self.pulse(10)
                request = self.store.enqueue('STOP', 'b' * 32, origin='system', reason='protective_shutdown', action='protection_stop')
                self.assertEqual(request['origin'], 'system')
                self.assertEqual(request['job_id'], job['id'])
                self.assertNotIn('cancel_reason', self.store.level_job['recovery'])
                self.ack(self.command('STOP'), fill='0', drain='0', state='IDLE')
                self.assertEqual((self.store.level_job['status'], self.store.level_job['phase']), ('running', 'waiting'))
                self.pulse(2)
                self.assertEqual(self.store.level_job['phase'], 'starting')
                new_on = self.command('DRAIN')
                self.assertEqual(self.origin(new_on)['origin'], 'system')

    def test_generic_route_off_does_not_cancel_automatic_target(self):
        self.start('target')
        self.on()
        self.pulse(2)
        request = self.store.enqueue('DRAIN_OFF', 'b' * 32)
        self.assertEqual((request['origin'], request['action']), ('manual', 'manual_output_off'))
        self.ack(self.command('DRAIN_OFF'), drain='0', fill='0', state='IDLE')
        self.assertEqual(self.store.level_job['status'], 'running')
        self.pulse(2)
        self.assertEqual(self.store.level_job['phase'], 'starting')
        self.command('DRAIN')

    def test_unrelated_off_does_not_cancel_or_stop_drain(self):
        self.start()
        self.on()
        self.pulse(2)
        self.store.enqueue('FILL_OFF', 'b' * 32)
        self.ack(self.command('FILL_OFF'))
        self.assertEqual((self.store.level_job['status'], self.store.level_job['phase']), ('running', 'active'))
        self.assertNotIn('cancel_reason', self.store.level_job['recovery'])

    def test_strict_cancel_requires_manual_intent_and_exact_captured_job(self):
        job = self.start()
        self.on()
        for invalid in (dict(self.intent(), intent=None), dict(self.intent(), intent='system_stop'), dict(self.intent(), source='timer')):
            with self.subTest(invalid=invalid), self.assertRaises(Problem) as caught:
                self.store.cancel_level_job(invalid, require_manual_intent=True)
            self.assertEqual(caught.exception.message, 'manual_stop_intent_required')
        with self.assertRaises(Problem) as caught:
            self.store.cancel_level_job(self.intent('f' * 32), require_manual_intent=True)
        self.assertEqual(caught.exception.message, 'level_job_changed')
        self.assertEqual(self.store.level_job['phase'], 'active')
        self.store.cancel_level_job(self.intent(), require_manual_intent=True)
        stop_id = self.command('STOP')
        record = self.origin(stop_id)
        self.assertEqual((record['origin'], record['action'], record['job_id']), ('manual', 'manual_stop', job['id']))
        self.ack(stop_id, fill='0', drain='0', state='IDLE')
        self.assertEqual(self.store.level_job['status'], 'cancelled')

    def test_http_stop_requires_intent_and_cannot_declare_system_origin(self):
        self.start()
        self.on()
        with self.assertRaises(Problem) as caught:
            self.store.enqueue('STOP', 'b' * 32, intent=dict(origin='system', intent='system_stop'), require_manual_intent=True)
        self.assertEqual(caught.exception.message, 'manual_stop_intent_required')
        self.assertEqual(self.store.level_job['phase'], 'active')

    def test_source_is_immutable_and_legacy_rows_are_not_backfilled(self):
        command_id = self.store.job_command('STOP', reason='test_protection', action='protection_stop')
        before = self.origin(command_id)
        again = self.store.enqueue('STOP', command_id)
        self.assertEqual(again, before)
        self.assertEqual(again['origin'], 'system')
        old_id = 'e' * 32
        self.store.db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?)',
                             (old_id, 'STOP', self.now, self.now, 'succeeded', 'cancelled_by_user', self.now))
        self.store.db.commit()
        legacy = self.store.enqueue('STOP', old_id)
        self.assertEqual(legacy['origin'], 'legacy_unknown')
        self.assertIsNone(self.store.db.execute('SELECT 1 FROM command_origins WHERE id=?', (old_id,)).fetchone())

    def test_unexpected_known_off_pauses_and_can_resume_without_clearing_faults(self):
        self.start()
        self.on()
        self.pulse(2)
        self.now += 1
        self.store.ws_touch(self.session, dict(STATUS, reason='stopped'))
        job = self.store.level_job
        self.assertEqual((job['status'], job['phase'], job['stop_origin']), ('running', 'paused', 'system'))
        self.assertEqual(job['recovery']['used_min']['drain'], 2)
        self.assertEqual(job['recovery']['used_max']['drain'], 3)
        self.session = self.store.ws_open(STATUS)
        self.status, self.sequence = dict(STATUS), 0
        self.pulse(7)
        self.assertEqual(self.store.level_job['phase'], 'starting')
        on_id = self.command('DRAIN')
        self.assertEqual(self.origin(on_id)['action'], 'recovery_resume')

    def test_manual_stop_source_survives_generic_off_and_restart(self):
        job = self.start()
        self.on()
        self.store.cancel_level_job(self.intent(), require_manual_intent=True)
        before = {key: self.store.level_job[key] for key in ('stop_origin', 'stop_action', 'stop_button', 'stop_requested_at')}
        self.store.enqueue('DRAIN_OFF', 'b' * 32)
        self.assertEqual({key: self.store.level_job[key] for key in before}, before)
        self.store.db.close()
        self.store = Store(self.path, lambda: self.now)
        self.session = self.store.ws_open(STATUS)
        self.assertEqual(self.store.level_job['status'], 'cancelled')
        self.assertEqual({key: self.store.level_job[key] for key in before}, before)
        self.assertIsNone(self.store.ws_offer(self.session))
        self.assertEqual(self.store.level_job['id'], job['id'])

    def test_target_round_wait_cannot_recalibrate_without_stopping(self):
        self.start('target')
        self.on()
        self.pulse(60)
        self.ack(self.command('DRAIN_OFF'), drain='0', state='IDLE')
        previous = copy.deepcopy(self.store.simulation)
        with self.assertRaises(Problem) as caught:
            self.store.configure_simulation(dict(level=90, fill_seconds=120, drain_seconds=320))
        self.assertEqual(caught.exception.message, 'simulation_requires_idle')
        self.assertEqual(previous, self.store.simulation)
        self.assertEqual(self.store.level_job['status'], 'running')

    def test_output_run_explicit_cancel_records_manual_run_context(self):
        run = self.store.output_runner.start(dict(direction='drain', id='c' * 32))
        self.on()
        self.store.output_runner.cancel(dict(direction='drain', run_id=run['id']))
        off = self.command('DRAIN_OFF')
        record = self.origin(off)
        self.assertEqual((record['origin'], record['action'], record['job_id']), ('manual', 'manual_stop', run['id']))

    def test_true_fault_still_ends_automatic_task(self):
        self.start()
        self.on()
        self.store.ws_touch(self.session, dict(STATUS, state='FAULT', reason='fill_timeout', ready='0'))
        self.assertEqual((self.store.level_job['status'], self.store.level_job['stop_origin']), ('failed', 'system'))

    def test_actual_soft_limit_stop_records_system_origin_and_keeps_job(self):
        job = self.start()
        self.on()
        self.pulse(2)
        self.store.control_runs['drain']['until'] = self.now
        self.store.control_tick(self.session)
        stop = self.command('STOP')
        record = self.origin(stop)
        self.assertEqual((record['origin'], record['origin_reason'], record['action'], record['job_id']),
                         ('system', 'drain_soft_limit', 'protection_stop', job['id']))
        self.ack(stop, fill='0', drain='0', state='IDLE')
        self.assertEqual(self.store.level_job['phase'], 'waiting')
        self.assertNotIn('cancel_reason', self.store.level_job['recovery'])
        self.pulse(2)
        self.command('DRAIN')

    def test_real_fault_after_manual_stop_cannot_erase_first_stop_source(self):
        self.start()
        self.on()
        self.store.cancel_level_job(self.intent(), require_manual_intent=True)
        before = {key: self.store.level_job[key] for key in ('stop_origin', 'stop_action', 'stop_button', 'stop_requested_at')}
        self.store.ws_touch(self.session, dict(STATUS, state='FAULT', reason='io_error', ready='0'))
        self.assertEqual(self.store.level_job['status'], 'failed')
        self.assertEqual({key: self.store.level_job[key] for key in before}, before)

    def test_legacy_cancel_stops_both_modes_with_unknown_source(self):
        for mode in ('exchange', 'target'):
            with self.subTest(mode=mode):
                if mode == 'target':
                    self.tearDown()
                    self.setUp()
                job = self.start(mode)
                self.on()
                self.store.cancel_level_job(dict(job_id=job['id']), require_manual_intent=True)
                stop = self.command('STOP')
                record = self.origin(stop)
                self.assertEqual((record['origin'], record['action'], record['job_id']),
                                 ('legacy_unknown', 'legacy_stop_request', job['id']))
                self.assertIsNone(record['event'])
                self.assertIsNone(record['button'])
                self.ack(stop, fill='0', drain='0', state='IDLE')
                self.assertEqual((self.store.level_job['status'], self.store.level_job['stop_origin']),
                                 ('cancelled', 'legacy_unknown'))
                self.pulse(3)
                self.assertIsNone(self.store.ws_offer(self.session))

    def test_legacy_cancel_wrong_job_id_is_rejected_without_stopping(self):
        self.start()
        self.on()
        with self.assertRaises(Problem) as caught:
            self.store.cancel_level_job(dict(job_id='f' * 32), require_manual_intent=True)
        self.assertEqual(caught.exception.message, 'level_job_changed')
        self.assertEqual(self.store.level_job['phase'], 'active')

    def test_legacy_commands_stop_without_job_id_keeps_stop_ability_and_unknown_origin(self):
        job = self.start()
        self.on()
        request = self.store.enqueue('STOP', 'b' * 32, intent=dict(command='STOP', origin='system'), require_manual_intent=True)
        self.assertEqual((request['origin'], request['action'], request['job_id']),
                         ('legacy_unknown', 'legacy_stop_request', job['id']))
        self.ack(self.command('STOP'), fill='0', drain='0', state='IDLE')
        self.assertEqual(self.store.level_job['status'], 'cancelled')

    def test_legacy_commands_stop_with_stale_job_id_cannot_stop_current_task(self):
        self.start()
        self.on()
        with self.assertRaises(Problem) as caught:
            self.store.enqueue('STOP', 'b' * 32, intent=dict(job_id='f' * 32), require_manual_intent=True)
        self.assertEqual(caught.exception.message, 'level_job_changed')
        self.assertEqual(self.store.level_job['phase'], 'active')

    def test_legacy_stop_source_is_latched_across_later_off_fault_and_restart(self):
        self.start()
        self.on()
        self.store.cancel_level_job(dict(job_id=self.store.level_job['id']), require_manual_intent=True)
        before = {key: self.store.level_job[key] for key in ('stop_origin', 'stop_action', 'stop_requested_at')}
        self.store.enqueue('DRAIN_OFF', 'b' * 32)
        self.assertEqual({key: self.store.level_job[key] for key in before}, before)
        self.store.db.close()
        self.store = Store(self.path, lambda: self.now)
        self.session = self.store.ws_open(STATUS)
        self.assertEqual(self.store.level_job['status'], 'cancelled')
        self.assertEqual({key: self.store.level_job[key] for key in before}, before)
        self.assertIsNone(self.store.ws_offer(self.session))

    def test_legacy_cancel_works_offline_without_inventing_device_receipt(self):
        job = self.start()
        self.on()
        self.store.ws_close(self.session)
        result = self.store.cancel_level_job(dict(job_id=job['id']), require_manual_intent=True)
        self.assertEqual((result['status'], result['stop_origin'], result['stop_action']),
                         ('cancelled', 'legacy_unknown', 'legacy_stop_request'))
        self.assertFalse(self.store.db.execute("SELECT 1 FROM commands WHERE command='STOP'").fetchone())


if __name__ == '__main__':
    unittest.main()
