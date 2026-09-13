"""Estimated drain-to-zero/fill-to-full jobs use confirmed, isolated stages."""
import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from server.app import Problem, Server, Store


STATUS = dict(project='water_auto_exchange', version='0.8.2', control_mode='manual',
              state='IDLE', reason='ready', ready='1', fill='0', drain='0', outputs_known='1',
              need_fill='unknown', overflow='0', cycle='0', overflow_protection='0')


class ExchangeJobTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'water.db')
        self.store = Store(self.path, lambda: self.now)
        self.session = self.store.ws_open(STATUS)
        self.status, self.sequence = dict(STATUS), 0
        self.configure()

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def configure(self, level=50, fill=200, drain=800):
        self.store.configure_simulation(dict(level=level, fill_seconds=fill, drain_seconds=drain, capacity_liters=60))

    def start(self, request_id='a' * 32):
        return self.store.start_level_job(dict(mode='exchange', id=request_id))

    def pulse(self, seconds):
        until = self.now + seconds
        while self.now < until:
            self.now = min(until, self.now + 1)
            self.sequence += 1
            self.store.ws_ping(self.session, self.sequence)

    def offer(self, expected):
        command = self.store.ws_offer(self.session)
        self.assertEqual(command['command'], expected)
        self.assertEqual(self.store.ws_claim(self.session, command['id'])['command'], expected)
        return command['id']

    def ack(self, command_id, **changes):
        self.status.update(changes)
        self.store.ws_touch(self.session, self.status, dict(id=command_id, status='succeeded', result='OK'))

    def on(self, direction):
        command_id = self.offer(direction.upper())
        self.ack(command_id, **{direction: '1', 'state': 'DRAINING' if direction == 'drain' else 'FILLING'})
        return command_id

    def off(self, direction):
        command_id = self.offer(direction.upper() + '_OFF')
        self.ack(command_id, fill='0', drain='0', state='IDLE')
        return command_id

    def short_drain_finished(self):
        self.configure(fill=4, drain=4)
        job = self.start()
        self.on('drain')
        self.pulse(2)
        self.off('drain')
        self.assertEqual(self.store.level_job['reason'], 'between_stages')
        return job

    def assert_no_fill_command(self):
        self.assertFalse(self.store.db.execute("SELECT 1 FROM commands WHERE command='FILL'").fetchone())

    def test_four_round_exchange_counts_total_progress_and_volume_across_both_stages(self):
        job = self.start()
        self.assertEqual((job['mode'], job['stage'], job['target_level']), ('exchange', 'drain', 0))
        self.assertEqual((job['total_seconds'], job['remaining_seconds'], job['estimated_rounds']), (600, 600, 4))
        observed_progress = []
        for number, (direction, duration) in enumerate((('drain', 290), ('drain', 110), ('fill', 170), ('fill', 30)), 1):
            self.on(direction)
            self.pulse(duration)
            self.assertEqual(self.store.level_job['phase'], 'stopping')
            self.off(direction)
            job = self.store.snapshot()['level_job']
            self.assertEqual(job['round'], number)
            observed_progress.append(job['progress'])
            if number == 2:
                self.assertEqual(job['reason'], 'between_stages')
                self.assertAlmostEqual(job['current_level'], 0)
                self.assertAlmostEqual(job['remaining_seconds'], 200)
                self.assertAlmostEqual(job['elapsed_seconds'], 400)
                self.assertAlmostEqual(job['estimated_liters'], 30)
            if number < 4:
                self.assertEqual(job['phase'], 'waiting')
                self.assertIsNone(self.store.ws_offer(self.session))
                self.pulse(1.9)
                self.assertIsNone(self.store.ws_offer(self.session))
                self.pulse(.1)
        self.assertEqual(job['status'], 'completed')
        self.assertEqual((job['stage'], job['direction'], job['target_level']), ('fill', 'fill', 100))
        self.assertAlmostEqual(job['current_level'], 100)
        self.assertAlmostEqual(job['elapsed_seconds'], 600)
        self.assertAlmostEqual(job['remaining_seconds'], 0)
        self.assertAlmostEqual(job['estimated_liters'], 90)
        self.assertEqual(observed_progress, sorted(observed_progress))
        self.assertAlmostEqual(job['progress'], 100)
        self.assertFalse(self.store.simulation['uncertain'])
        self.assertIsNone(self.store.ws_offer(self.session))
        self.assertFalse(self.store.db.execute("SELECT 1 FROM commands WHERE command IN ('RESET','STOP','FILL_TIMEOUT','DRAIN_TIMEOUT')").fetchone())

    def test_zero_start_skips_drain_and_uses_full_fill_calibration(self):
        self.configure(level=0, fill=4)
        job = self.start()
        self.assertEqual((job['stage'], job['target_level'], job['total_seconds']), ('fill', 100, 4))
        self.on('fill')
        self.pulse(4)
        self.off('fill')
        self.assertEqual(self.store.level_job['status'], 'completed')
        self.assertAlmostEqual(self.store.level_job['estimated_liters'], 60)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM commands WHERE command LIKE 'DRAIN%'").fetchone())

    def test_off_report_without_current_successful_receipt_never_changes_stage(self):
        self.configure(fill=4, drain=4)
        self.start()
        self.on('drain')
        self.pulse(2)
        off_id = self.offer('DRAIN_OFF')
        self.status.update(drain='0', state='IDLE')
        self.store.ws_touch(self.session, self.status)
        self.pulse(3)
        self.assertEqual((self.store.level_job['stage'], self.store.level_job['phase']), ('drain', 'stopping'))
        self.assertAlmostEqual(self.store.level_job['elapsed_seconds'], 2)
        self.assert_no_fill_command()
        self.ack(off_id)
        self.pulse(1.9)
        self.assert_no_fill_command()
        self.pulse(.1)
        self.assertEqual(self.store.level_job['stage'], 'fill')

    def test_previous_round_off_receipt_cannot_release_stage_barrier(self):
        self.start()
        self.on('drain')
        self.pulse(290)
        previous_off = self.off('drain')
        self.pulse(2)
        self.on('drain')
        self.pulse(110)
        self.offer('DRAIN_OFF')
        self.ack(previous_off, drain='0', state='IDLE')
        self.assertEqual(self.store.level_job['phase'], 'stopping')
        self.pulse(4)
        self.assert_no_fill_command()
        with self.assertRaises(Problem):
            self.pulse(1)
        self.assertEqual(self.store.level_job['reason'], 'stop_unconfirmed')

    def test_rejected_drain_off_and_late_ack_cannot_start_fill(self):
        self.configure(fill=4, drain=4)
        self.start()
        self.on('drain')
        self.pulse(2)
        off_id = self.offer('DRAIN_OFF')
        with self.assertRaises(Problem):
            self.store.ws_touch(self.session, self.status, dict(id=off_id, status='rejected', result='failed'))
        self.assertEqual(self.store.level_job['status'], 'failed')
        self.session = self.store.ws_open(STATUS)
        with self.assertRaises(Problem):
            self.store.ws_touch(self.session, STATUS, dict(id=off_id, status='succeeded', result='late'))
        self.pulse(3)
        self.assert_no_fill_command()

    def test_cancel_between_stages_prevents_fill_even_at_wait_deadline(self):
        job = self.short_drain_finished()
        self.pulse(1.9)
        self.store.cancel_level_job(dict(job_id=job['id']))
        self.pulse(.1)
        stop_id = self.offer('STOP')
        self.ack(stop_id, fill='0', drain='0', state='IDLE')
        self.pulse(3)
        self.assertEqual(self.store.level_job['status'], 'cancelled')
        self.assert_no_fill_command()

    def test_cancel_revokes_fill_already_queued_after_stage_wait(self):
        job = self.short_drain_finished()
        self.pulse(2)
        fill_id = self.store._job_runtime['on_id']
        self.store.cancel_level_job(dict(job_id=job['id']))
        self.assertEqual(self.store.db.execute('SELECT status FROM commands WHERE id=?', (fill_id,)).fetchone()['status'], 'cancelled')
        self.ack(self.offer('STOP'), fill='0', drain='0', state='IDLE')
        self.pulse(3)
        self.assertEqual(self.store.level_job['status'], 'cancelled')
        self.assertIsNone(self.store.ws_offer(self.session))

    def test_cancel_during_stop_preserves_off_receipt_and_original_deadline(self):
        self.configure(fill=4, drain=4)
        job = self.start()
        self.on('drain')
        self.pulse(2)
        off_id = self.offer('DRAIN_OFF')
        deadline = self.store._job_runtime['stop_until']
        self.pulse(4)
        for _ in range(3):
            self.store.cancel_level_job(dict(job_id=job['id']))
        self.assertEqual(self.store._job_runtime['off_id'], off_id)
        self.assertEqual(self.store._job_runtime['stop_until'], deadline)
        with self.assertRaises(Problem):
            self.pulse(1)
        self.assertEqual(self.store.level_job['reason'], 'stop_unconfirmed')
        self.assert_no_fill_command()

    def test_cancel_during_stop_accepts_existing_off_then_finishes_cancelled(self):
        self.configure(fill=4, drain=4)
        job = self.start()
        self.on('drain')
        self.pulse(2)
        off_id = self.offer('DRAIN_OFF')
        self.store.cancel_level_job(dict(job_id=job['id']))
        self.ack(off_id, drain='0', state='IDLE')
        self.assertEqual(self.store.level_job['status'], 'cancelled')
        self.pulse(3)
        self.assert_no_fill_command()

    def test_duplicate_request_stays_same_during_fill_and_after_completion_and_new_job(self):
        self.configure(fill=4, drain=4)
        original = self.start()
        self.assertEqual(self.start()['id'], original['id'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM commands').fetchone()[0], 1)
        self.on('drain')
        self.pulse(2)
        self.off('drain')
        self.pulse(2)
        self.assertEqual(self.start()['stage'], 'fill')
        self.on('fill')
        self.pulse(4)
        self.off('fill')
        self.assertEqual(self.start()['status'], 'completed')
        newer = self.start('b' * 32)
        count = self.store.db.execute('SELECT COUNT(*) FROM commands').fetchone()[0]
        self.assertEqual(self.start()['status'], 'completed')
        self.assertEqual(self.store.level_job['id'], newer['id'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM commands').fetchone()[0], count)
        with self.assertRaises(Problem) as caught:
            self.store.cancel_level_job(dict(job_id=original['id']))
        self.assertEqual(caught.exception.message, 'level_job_changed')
        self.assertEqual(self.store.level_job['phase'], 'starting')

    def test_conflicting_id_is_rejected_but_legacy_target_requests_still_work(self):
        job = self.store.start_level_job(dict(target_level=40, id='a' * 32))
        self.assertEqual(self.store.start_level_job(dict(target_level=40.0, id='a' * 32))['id'], job['id'])
        for request in (dict(mode='exchange', id='a' * 32), dict(target_level=30, id='a' * 32)):
            with self.assertRaises(Problem) as caught:
                self.store.start_level_job(request)
            self.assertEqual(caught.exception.message, 'request_id_conflict')
        self.store.cancel_level_job()
        self.ack(self.offer('STOP'), fill='0', drain='0', state='IDLE')
        self.assertEqual(self.store.start_level_job(dict(target_level=40))['mode'], 'target')

    def test_restart_keeps_failed_job_and_request_id_cannot_restart_it(self):
        job = self.short_drain_finished()
        self.store.db.close()
        self.store = Store(self.path, lambda: self.now)
        self.session = self.store.ws_open(STATUS)
        self.assertEqual(self.start()['id'], job['id'])
        self.assertEqual(self.start()['reason'], 'server_restarted')
        self.pulse(3)
        self.assert_no_fill_command()
        self.assertIsNone(self.store.ws_offer(self.session))

    def test_disconnect_between_stages_never_resumes_on_new_connection(self):
        self.short_drain_finished()
        self.store.ws_close(self.session)
        self.session = self.store.ws_open(STATUS)
        self.pulse(3)
        self.assertEqual(self.store.level_job['status'], 'failed')
        self.assert_no_fill_command()

    def test_calibration_and_other_jobs_are_blocked_during_stage_wait(self):
        self.short_drain_finished()
        calibration = copy.deepcopy(self.store.simulation)
        for operation in (
                lambda: self.store.configure_simulation(dict(level=80, fill_seconds=10, drain_seconds=10)),
                lambda: self.store.output_runner.start(dict(direction='fill', id='b' * 32)),
                lambda: self.store.start_level_job(dict(target_level=90)),
                lambda: self.start('c' * 32)):
            with self.assertRaises(Problem):
                operation()
        self.assertEqual(self.store.simulation, calibration)
        self.assertEqual((self.store.level_job['fill_seconds'], self.store.level_job['drain_seconds']), (4, 4))
        self.assertEqual(self.store.level_job['status'], 'running')

    def test_unknown_estimate_and_running_output_cannot_start_exchange(self):
        self.store.simulation['uncertain'] = True
        with self.assertRaises(Problem) as caught:
            self.start()
        self.assertEqual(caught.exception.message, 'level_job_requires_calibration')
        self.store.simulation['uncertain'] = False
        self.store.output_runner.start(dict(direction='drain', id='b' * 32))
        with self.assertRaises(Problem) as caught:
            self.start()
        self.assertEqual(caught.exception.message, 'output_run_active')

    def test_manual_open_and_reset_cannot_bypass_exchange_sequencing(self):
        job = self.start()
        self.on('drain')
        self.pulse(1)
        for command in ('START', 'FILL', 'DRAIN', 'RESET'):
            with self.subTest(command=command), self.assertRaises(Problem) as caught:
                self.store.enqueue(command, 'b' * 32)
            self.assertEqual(caught.exception.message, 'level_job_active')
            self.assertEqual(self.store.level_job['id'], job['id'])
            self.assertEqual(self.store.level_job['phase'], 'active')
        self.assert_no_fill_command()

    def test_unrelated_manual_off_confirms_all_off_before_cancelling_exchange(self):
        self.start()
        self.on('drain')
        self.pulse(1)
        request = self.store.enqueue('FILL_OFF', 'b' * 32)
        self.assertEqual((request['status'], request['result']), ('cancelled', 'exchange_stop_requested'))
        self.assertEqual(self.store.level_job['status'], 'running')
        self.assertEqual(self.store.level_job['phase'], 'stopping')
        stop_id = self.offer('STOP')
        self.assertIsNone(self.store.ws_offer(self.session))
        self.assertEqual(self.store.enqueue('FILL_OFF', 'b' * 32), request)
        self.ack(stop_id, fill='0', drain='0', state='IDLE')
        self.assertEqual(self.store.level_job['status'], 'cancelled')
        self.assertEqual(self.store.level_job['reason'], 'manual_override')
        self.assertIsNone(self.store.control_runs['drain'])
        self.pulse(3)
        self.assert_no_fill_command()
        self.assertIsNone(self.store.ws_offer(self.session))

    def test_manual_stop_supersedes_pending_round_off_without_extending_deadline(self):
        self.configure(fill=4, drain=4)
        self.start()
        self.on('drain')
        self.pulse(2)
        old_off_id = self.store._job_runtime['off_id']
        deadline = self.store._job_runtime['stop_until']
        self.pulse(4)
        self.store.enqueue('STOP', 'b' * 32)
        self.assertEqual(self.store._job_runtime['stop_until'], deadline)
        self.assertEqual(self.store._job_runtime['off_id'], 'b' * 32)
        self.assertEqual(self.store.db.execute('SELECT status FROM commands WHERE id=?', (old_off_id,)).fetchone()['status'], 'cancelled')
        self.ack(self.offer('STOP'), fill='0', drain='0', state='IDLE')
        self.assertEqual(self.store.level_job['status'], 'cancelled')
        self.assert_no_fill_command()

    def test_exchange_cancel_requires_id_so_legacy_delayed_cancel_cannot_end_new_job(self):
        job = self.start()
        for value in (None, {}, dict(job_id=None)):
            with self.subTest(value=value), self.assertRaises(Problem) as caught:
                self.store.cancel_level_job(value)
            self.assertEqual(caught.exception.message, 'invalid_level_job')
            self.assertEqual(self.store.level_job['id'], job['id'])
            self.assertEqual(self.store.level_job['phase'], 'starting')

    def test_upgrade_of_legacy_persisted_job_ends_old_run_without_replaying(self):
        job = self.store.start_level_job(dict(target_level=40))
        for key in ('mode', 'stage', 'fill_seconds', 'drain_seconds', 'capacity_liters'):
            job.pop(key)
        self.store.db.execute('UPDATE level_job SET value=?', (json.dumps(job),))
        self.store.db.execute('DROP TABLE level_job_requests')
        self.store.db.commit()
        self.store.db.close()
        self.store = Store(self.path, lambda: self.now)
        self.session = self.store.ws_open(STATUS)
        saved = self.store.snapshot()['level_job']
        self.assertEqual((saved['status'], saved['reason']), ('failed', 'server_restarted'))
        self.assertIsNone(self.store.ws_offer(self.session))
        self.assertEqual(self.start()['stage'], 'drain')

    def test_overflow_during_stage_wait_fails_without_fill(self):
        self.short_drain_finished()
        self.store.ws_touch(self.session, dict(STATUS, overflow='1'))
        self.pulse(2)
        self.assertEqual(self.store.level_job['reason'], 'device_not_ready')
        self.assert_no_fill_command()

    def test_invalid_mode_id_and_cancel_payload_are_rejected(self):
        for value in (None, [], dict(mode='exchange'), dict(mode='other'),
                      dict(mode='exchange', id='short'), dict(mode='exchange', id='G' * 32),
                      dict(target_level=10, id=True)):
            with self.subTest(value=value), self.assertRaises(Problem) as caught:
                self.store.start_level_job(value)
            self.assertEqual(caught.exception.code, 400)
        for value in ([], dict(job_id='short')):
            with self.assertRaises(Problem) as caught:
                self.store.cancel_level_job(value)
            self.assertEqual(caught.exception.code, 400)


class ExchangeJobHTTPTests(unittest.TestCase):
    def test_authenticated_origin_checked_routes_start_deduplicate_and_cancel_exact_job(self):
        store = Store(':memory:')
        store.ws_open(STATUS)
        store.configure_simulation(dict(level=40, fill_seconds=200, drain_seconds=400))
        server = Server(('127.0.0.1', 0), store, 'a' * 32, 'b' * 32, 'https://example.test')
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        token = store.create_admin_session('a' * 32)

        def request(route, value, authorized=True, origin=True):
            headers = {'Content-Type': 'application/json'}
            if authorized:
                headers['Cookie'] = 'water_session=' + token
            if origin:
                headers['Origin'] = 'https://example.test'
            return urllib.request.urlopen(urllib.request.Request(
                'http://127.0.0.1:%d/water/api/%s' % (server.server_port, route), json.dumps(value).encode(), headers))

        try:
            for route in ('level-job', 'level-job/cancel'):
                for authorized, origin, expected in ((False, True, 401), (True, False, 403)):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        request(route, {}, authorized, origin)
                    self.assertEqual(caught.exception.code, expected)
            for _ in range(2):
                with request('level-job', dict(mode='exchange', id='c' * 32)) as response:
                    self.assertEqual(response.status, 202)
                    job = json.load(response)
                    self.assertEqual((job['id'], job['mode'], job['stage']), ('c' * 32, 'exchange', 'drain'))
                    self.assertAlmostEqual(job['total_seconds'], 360)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                request('level-job/cancel', dict(job_id='d' * 32))
            self.assertEqual(caught.exception.code, 409)
            with request('level-job/cancel', dict(job_id=job['id'])) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response)['reason'], 'cancelled_by_user')
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
            store.db.close()


if __name__ == '__main__':
    unittest.main()
