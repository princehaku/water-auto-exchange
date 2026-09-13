"""Multi-round estimates never recover lost authority or skip OFF receipts."""
import copy
import tempfile
import unittest
from pathlib import Path
from server.app import Store, Problem

STATUS = dict(project='water_auto_exchange', version='0.8.2', control_mode='manual',
              state='IDLE', reason='ready', ready='1', fill='0', drain='0', outputs_known='1',
              need_fill='unknown', overflow='0', cycle='0', overflow_protection='0')


class LevelJobTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'water.db')
        self.store = Store(self.path, lambda: self.now)
        self.session = self.store.ws_open(STATUS)
        self.status = dict(STATUS)
        self.sequence = 0
        self.store.configure_simulation(dict(level=100, fill_seconds=1000, drain_seconds=3000, capacity_liters=60))

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def pulse(self, seconds):
        until = self.now + seconds
        while self.now < until:
            self.now = min(until, self.now + 1)
            self.sequence += 1
            self.store.ws_ping(self.session, self.sequence)

    def offer(self, expected):
        offer = self.store.ws_offer(self.session)
        self.assertEqual(offer['command'], expected)
        self.assertEqual(self.store.ws_claim(self.session, offer['id'])['command'], expected)
        return offer['id']

    def ack(self, command_id, **changes):
        self.status.update(changes)
        self.store.ws_touch(self.session, self.status, dict(id=command_id, status='succeeded', result='OK'))

    def on(self, direction='drain'):
        command_id = self.offer(direction.upper())
        self.ack(command_id, **{direction: '1', 'state': 'DRAINING' if direction == 'drain' else 'FILLING'})
        return command_id

    def off(self, direction='drain'):
        command_id = self.offer(direction.upper() + '_OFF')
        self.ack(command_id, fill='0', drain='0', state='IDLE', reason='stopped')
        return command_id

    def test_thousand_seconds_in_four_rounds_keeps_total_volume(self):
        self.store.start_level_job(dict(target_level=100 - 100 / 3))
        for number, duration in enumerate((290, 290, 290, 130), 1):
            self.on()
            original_observed = self.store.simulation['observed_at']
            self.pulse(duration)
            job = self.store.snapshot()['level_job']
            self.assertEqual(job['phase'], 'stopping')
            self.assertEqual(job['round'], number)
            self.assertEqual(self.store.simulation['observed_at'], original_observed)
            self.off()
            if number < 4:
                self.assertEqual(self.store.level_job['phase'], 'waiting')
                self.assertIsNone(self.store.ws_offer(self.session))
                self.pulse(1.9)
                self.assertEqual(self.store.level_job['phase'], 'waiting')
                self.pulse(.1)
        job = self.store.snapshot()['level_job']
        self.assertEqual(job['status'], 'completed')
        self.assertAlmostEqual(job['elapsed_seconds'], 1000)
        self.assertAlmostEqual(job['estimated_liters'], 20)
        self.assertAlmostEqual(job['current_level'], 100 - 100 / 3)
        self.assertIsNone(self.store.ws_offer(self.session))
        self.assertIsNone(self.store.control_timeout)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM commands WHERE command IN ('RESET','DRAIN_TIMEOUT')").fetchone())

    def test_fill_uses_170_second_round_and_short_last_round(self):
        self.store.configure_simulation(dict(level=0, fill_seconds=1000, drain_seconds=3000))
        self.store.start_level_job(dict(target_level=20))
        for duration in (170, 30):
            self.on('fill')
            self.pulse(duration)
            self.off('fill')
            if duration == 170:
                self.pulse(2)
        self.assertEqual(self.store.level_job['status'], 'completed')
        self.assertAlmostEqual(self.store.level_job['elapsed_seconds'], 200)

    def test_off_status_before_ack_stops_volume_clock_but_does_not_continue(self):
        self.store.start_level_job(dict(target_level=50))
        self.on()
        self.pulse(290)
        off_id = self.offer('DRAIN_OFF')
        self.status.update(drain='0', state='IDLE')
        self.store.ws_touch(self.session, self.status)
        self.pulse(3)
        job = self.store.snapshot()['level_job']
        self.assertEqual(job['phase'], 'stopping')
        self.assertAlmostEqual(job['elapsed_seconds'], 290)
        self.assertAlmostEqual(job['estimated_liters'], 5.8)
        self.assertIsNone(self.store.ws_offer(self.session))
        self.ack(off_id)
        self.assertEqual(self.store.level_job['phase'], 'waiting')
        self.assertAlmostEqual(self.store.level_job['elapsed_seconds'], 290)

    def test_off_rejection_and_late_receipt_never_start_next_round(self):
        self.store.start_level_job(dict(target_level=50))
        self.on()
        self.pulse(290)
        off_id = self.offer('DRAIN_OFF')
        with self.assertRaises(Problem):
            self.store.ws_touch(self.session, self.status, dict(id=off_id, status='rejected', result='failed'))
        self.assertEqual(self.store.level_job['status'], 'failed')
        self.assertIsNone(self.store.ws_gateway)
        self.session = self.store.ws_open(STATUS)
        with self.assertRaises(Problem):
            self.store.ws_touch(self.session, STATUS, dict(id=off_id, status='succeeded', result='late'))
        self.assertIsNone(self.store.ws_offer(self.session))

    def test_missing_off_receipt_closes_after_five_seconds_even_if_output_looks_off(self):
        self.store.start_level_job(dict(target_level=50))
        self.on()
        self.pulse(290)
        self.offer('DRAIN_OFF')
        self.status.update(drain='0', state='IDLE')
        self.store.ws_touch(self.session, self.status)
        with self.assertRaises(Problem):
            self.pulse(5)
        self.assertEqual(self.store.level_job['reason'], 'stop_unconfirmed')
        self.assertIsNone(self.store.ws_gateway)

    def test_cancel_before_on_ack_requires_stop_receipt(self):
        self.store.start_level_job(dict(target_level=50))
        self.offer('DRAIN')
        self.store.cancel_level_job()
        self.assertEqual(self.store.level_job['status'], 'running')
        stop_id = self.offer('STOP')
        self.ack(stop_id, fill='0', drain='0', state='IDLE')
        self.assertEqual(self.store.level_job['status'], 'cancelled')
        self.assertIsNone(self.store.control_runs['drain'])
        self.pulse(3)
        self.assertIsNone(self.store.ws_offer(self.session))

    def test_rejected_manual_requests_leave_job_running(self):
        self.store.start_level_job(dict(target_level=50))
        self.on()
        for command in ('START', 'RESET'):
            with self.assertRaises(Problem):
                self.store.enqueue(command, 'f' * 32)
            self.assertEqual(self.store.level_job['status'], 'running')
        self.store.enqueue('FILL_OFF', 'f' * 32)
        self.assertEqual(self.store.level_job['reason'], 'manual_override')

    def test_recalibration_exclusion_and_restart_cannot_resume(self):
        self.store.start_level_job(dict(target_level=50))
        with self.assertRaises(Problem) as caught:
            self.store.start_level_job(dict(target_level=40))
        self.assertEqual(caught.exception.message, 'level_job_active')
        self.on()
        self.pulse(290)
        self.off()
        self.store.configure_simulation(dict(level=90, fill_seconds=1000, drain_seconds=3000))
        self.assertEqual(self.store.level_job['reason'], 'calibration_changed')
        self.store.start_level_job(dict(target_level=40))
        self.store.db.close()
        self.store = Store(self.path, lambda: self.now)
        self.assertEqual(self.store.level_job['reason'], 'server_restarted')
        self.session = self.store.ws_open(STATUS)
        self.assertIsNone(self.store.ws_offer(self.session))

    def test_calibration_cannot_cancel_queued_or_authorized_job_start(self):
        job = self.store.start_level_job(dict(target_level=50))
        calibration = dict(level=90, fill_seconds=1000, drain_seconds=3000)
        for stage in ('queued', 'granted', 'expired_grant'):
            with self.subTest(stage=stage):
                if stage == 'granted':
                    self.offer('DRAIN')
                elif stage == 'expired_grant':
                    # Keep the command's execute authority while the DB row expires.
                    self.store.job_finish('cancelled', 'manual_override')
                    self.pulse(9)
                previous = copy.deepcopy(self.store.simulation)
                with self.assertRaises(Problem) as caught:
                    self.store.configure_simulation(calibration)
                self.assertEqual(caught.exception.message, 'simulation_requires_idle')
                self.assertEqual(self.store.simulation, previous)
                self.assertEqual(self.store.level_job['id'], job['id'])
                if stage != 'expired_grant':
                    self.assertEqual(self.store.level_job['status'], 'running')
                    self.assertEqual(self.store.level_job['phase'], 'starting')

    def test_calibration_rejects_pending_manual_start_without_side_effects(self):
        for command in ('FILL', 'DRAIN'):
            with self.subTest(command=command):
                command_id = ('a' if command == 'FILL' else 'b') * 32
                self.store.enqueue(command, command_id)
                previous = copy.deepcopy(self.store.simulation)
                with self.assertRaises(Problem) as caught:
                    self.store.configure_simulation(dict(level=90, fill_seconds=1000, drain_seconds=3000))
                self.assertEqual(caught.exception.message, 'simulation_requires_idle')
                self.assertEqual(self.store.simulation, previous)
                self.assertEqual(self.store.db.execute('SELECT status FROM commands WHERE id=?', (command_id,)).fetchone()['status'], 'queued')
                self.store.db.execute("UPDATE commands SET status='cancelled' WHERE id=?", (command_id,))
                self.store.db.commit()

    def test_job_start_rejects_not_ready_or_overflow(self):
        for changes in (dict(ready='0'), dict(overflow='1')):
            with self.subTest(changes=changes):
                self.store.ws_touch(self.session, dict(STATUS, **changes))
                with self.assertRaises(Problem) as caught:
                    self.store.start_level_job(dict(target_level=50))
                self.assertEqual(caught.exception.message, 'level_job_requires_idle')
                self.assertIsNone(self.store.level_job)
                self.assertIsNone(self.store.ws_offer(self.session))

    def test_waiting_round_does_not_restart_after_not_ready_or_overflow(self):
        for changes in (dict(ready='0'), dict(overflow='1')):
            with self.subTest(changes=changes):
                self.store.ws_touch(self.session, STATUS)
                self.store.start_level_job(dict(target_level=50))
                self.on()
                self.pulse(290)
                self.off()
                self.store.ws_touch(self.session, dict(STATUS, **changes))
                self.assertEqual(self.store.level_job['status'], 'failed')
                self.assertEqual(self.store.level_job['reason'], 'device_not_ready')
                self.pulse(2)
                self.assertIsNone(self.store.ws_offer(self.session))

    def test_expired_unconfirmed_on_authority_blocks_new_job(self):
        self.store.enqueue('DRAIN', 'a' * 32)
        self.offer('DRAIN')
        self.pulse(9)
        with self.assertRaises(Problem) as caught:
            self.store.start_level_job(dict(target_level=50))
        self.assertEqual(caught.exception.message, 'level_job_requires_idle')

    def test_gap_freezes_estimate_and_stale_close_is_not_recursive(self):
        self.store.start_level_job(dict(target_level=50))
        self.on()
        self.pulse(2)
        previous = self.store.simulation['level']
        self.now += 6
        with self.assertRaises(Problem):
            self.store.ws_ping(self.session, self.sequence + 1)
        self.assertTrue(self.store.simulation['uncertain'])
        self.assertEqual(self.store.simulation['level'], previous)
        self.assertEqual(self.store.level_job['status'], 'failed')
        self.store.ws_close(self.session)
        self.assertIsNone(self.store.ws_gateway)

    def test_duplicate_ping_and_unobserved_on_never_count_estimated_water(self):
        self.store.enqueue('DRAIN', 'a' * 32)
        command_id = self.offer('DRAIN')
        self.pulse(3)
        self.assertEqual(self.store.simulation['level'], 100)
        self.ack(command_id, drain='1', state='DRAINING')
        observed_at = self.store.simulation['observed_at']
        self.pulse(2)
        previous = copy.deepcopy(self.store.simulation)
        self.now += 1
        self.assertFalse(self.store.ws_ping(self.session, self.sequence))
        self.assertEqual(self.store.simulation, previous)
        self.assertEqual(self.store.simulation['observed_at'], observed_at)


if __name__ == '__main__':
    unittest.main()
