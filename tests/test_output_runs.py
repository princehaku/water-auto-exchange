"""Calibrated-duration jobs are finite, independently stoppable and replay-safe."""
import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from server.app import Store, Server, Problem

STATUS = dict(project='water_auto_exchange', version='0.8.2', control_mode='manual',
              state='IDLE', reason='ready', ready='1', fill='0', drain='0', outputs_known='1',
              need_fill='unknown', overflow='0', cycle='0', overflow_protection='0')


class OutputRunTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'water.db')
        self.store = Store(self.path, lambda: self.now)
        self.session = self.store.ws_open(STATUS)
        self.status = dict(STATUS)
        self.sequence = 0
        self.commands = []
        self.calibrate()

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def calibrate(self, fill=200, drain=400, level=40):
        self.store.configure_simulation(dict(level=level, fill_seconds=fill, drain_seconds=drain, capacity_liters=60))

    def start(self, direction, number=1):
        return self.store.output_runner.start(dict(direction=direction, id='{:032x}'.format(number)))

    def ack(self, command_id, command, result='succeeded'):
        if result == 'succeeded':
            if command == 'STOP':
                self.status.update(fill='0', drain='0')
            elif command in ('FILL', 'DRAIN'):
                self.status[command.lower()] = '1'
            elif command in ('FILL_OFF', 'DRAIN_OFF'):
                self.status[command[:-4].lower()] = '0'
        self.status['state'] = ('EXCHANGING' if self.status['fill'] == self.status['drain'] == '1' else
                                'FILLING' if self.status['fill'] == '1' else
                                'DRAINING' if self.status['drain'] == '1' else 'IDLE')
        self.status['reason'] = 'stopped' if command.endswith('OFF') or command == 'STOP' else 'started'
        return self.store.ws_touch(self.session, self.status, dict(id=command_id, status=result, result='OK'))

    def process(self):
        while True:
            offer = self.store.ws_offer(self.session)
            if offer is None:
                return
            execute = self.store.ws_claim(self.session, offer['id'])
            self.assertEqual(execute['type'], 'execute')
            self.commands.append((self.now, offer['command']))
            self.ack(offer['id'], offer['command'])

    def pulse(self, duration, process=True):
        until = self.now + duration
        while self.now < until:
            self.now = min(until, self.now + 1)
            self.sequence += 1
            self.store.ws_ping(self.session, self.sequence)
            if process:
                self.process()

    def test_both_full_durations_continue_independently_of_starting_level(self):
        self.start('fill', 1)
        self.start('drain', 2)
        self.process()
        self.assertEqual(self.status['state'], 'EXCHANGING')
        self.pulse(202)
        runs = self.store.output_runner.snapshot()
        self.assertEqual(runs['fill']['status'], 'completed')
        self.assertEqual(runs['fill']['elapsed_seconds'], 200)
        self.assertEqual(runs['fill']['estimated_liters'], 60)
        self.assertEqual(runs['drain']['status'], 'running')
        self.assertEqual(self.status['drain'], '1')
        self.pulse(200)
        runs = self.store.output_runner.snapshot()
        self.assertEqual(runs['drain']['status'], 'completed')
        self.assertEqual(runs['drain']['elapsed_seconds'], 400)
        self.assertEqual(runs['drain']['round'], 2)
        self.assertEqual(runs['drain']['estimated_liters'], 60)
        self.assertEqual([name for _, name in self.commands].count('FILL_OFF'), 2)
        self.assertEqual([name for _, name in self.commands].count('DRAIN_OFF'), 2)
        self.assertNotIn('STOP', [name for _, name in self.commands])
        self.assertEqual(self.store.simulation['level'], 40)

    def test_current_full_or_empty_level_does_not_shorten_calibrated_duration(self):
        for number, direction, level in ((1, 'fill', 100), (2, 'drain', 0)):
            with self.subTest(direction=direction):
                self.calibrate(fill=10, drain=10, level=level)
                self.start(direction, number)
                self.process()
                self.pulse(10)
                run = self.store.output_runner.snapshot()[direction]
                self.assertEqual(run['status'], 'completed')
                self.assertEqual(run['elapsed_seconds'], 10)
                self.assertEqual(run['estimated_liters'], 60)
                self.assertEqual(self.store.simulation['level'], level)

    def test_three_rounds_for_each_direction_and_short_final_round(self):
        self.calibrate(fill=400, drain=650)
        self.start('fill', 1)
        self.start('drain', 2)
        self.process()
        self.pulse(654)
        runs = self.store.output_runner.snapshot()
        for key, duration in (('fill', 400), ('drain', 650)):
            self.assertEqual(runs[key]['status'], 'completed')
            self.assertEqual(runs[key]['round'], 3)
            self.assertEqual(runs[key]['elapsed_seconds'], duration)
            self.assertEqual(runs[key]['estimated_liters'], 60)

    def test_same_deadline_off_receipts_are_serialized(self):
        self.calibrate(fill=10, drain=10)
        self.start('fill', 1)
        self.start('drain', 2)
        first = self.store.ws_offer(self.session)
        self.store.ws_claim(self.session, first['id'])
        self.assertIsNone(self.store.ws_offer(self.session))
        self.ack(first['id'], first['command'])
        self.process()
        self.pulse(10, process=False)
        first_off = self.store.ws_offer(self.session)
        self.store.ws_claim(self.session, first_off['id'])
        self.assertIsNone(self.store.ws_offer(self.session))
        self.ack(first_off['id'], first_off['command'])
        self.process()
        self.assertTrue(all(run['status'] == 'completed' for run in self.store.output_runner.snapshot().values()))

    def test_uncertain_level_preserves_rate_calibration_and_does_not_freeze_duration(self):
        self.store.freeze_estimate()
        self.start('drain')
        self.process()
        self.pulse(402)
        self.assertEqual(self.store.output_runner.snapshot()['drain']['status'], 'completed')
        self.assertEqual(self.store.simulation['level'], 40)
        self.assertTrue(self.store.simulation['uncertain'])

    def test_cancel_waiting_and_manual_off_only_stop_their_own_direction(self):
        fill = self.start('fill', 1)
        self.start('drain', 2)
        self.process()
        self.pulse(170)
        self.assertEqual(self.store.output_runner.runs['fill']['phase'], 'waiting')
        self.store.output_runner.cancel(dict(direction='fill', run_id=fill['id']))
        self.process()
        self.assertEqual(self.store.output_runner.runs['fill']['status'], 'cancelled')
        self.assertEqual(self.status['drain'], '1')
        self.store.enqueue('DRAIN_OFF', 'f' * 32)
        self.assertEqual(self.store.output_runner.runs['drain']['phase'], 'stopping')
        self.process()
        self.assertEqual(self.store.output_runner.runs['drain']['status'], 'cancelled')
        self.pulse(3)
        self.assertEqual([name for _, name in self.commands].count('FILL'), 1)

    def test_stop_all_preempts_grant_and_never_resumes(self):
        self.start('fill', 1)
        self.start('drain', 2)
        offer = self.store.ws_offer(self.session)
        self.store.ws_claim(self.session, offer['id'])
        self.store.enqueue('STOP', 'f' * 32)
        self.process()
        self.assertTrue(all(run['status'] == 'cancelled' for run in self.store.output_runner.snapshot().values()))
        self.assertFalse(self.store.ws_touch(self.session, dict(STATUS, fill='1', state='FILLING'), dict(id=offer['id'], status='succeeded', result='late')))
        self.pulse(3)
        self.assertEqual(self.status['fill'], '0')

    def test_cancel_before_on_ack_counts_only_confirmed_on_interval(self):
        run = self.start('fill')
        offer = self.store.ws_offer(self.session)
        self.store.ws_claim(self.session, offer['id'])
        self.store.output_runner.cancel(dict(direction='fill', run_id=run['id']))
        self.assertIsNone(self.store.ws_offer(self.session))
        self.ack(offer['id'], 'FILL')
        self.pulse(2, process=False)
        self.process()
        run = self.store.output_runner.snapshot()['fill']
        self.assertEqual(run['status'], 'cancelled')
        self.assertEqual(run['elapsed_seconds'], 2)
        self.assertEqual([name for _, name in self.commands], ['FILL_OFF'])

    def test_off_refused_or_unconfirmed_closes_both_runs_without_restart(self):
        self.start('fill', 1)
        self.start('drain', 2)
        self.process()
        self.pulse(170, process=False)
        offer = self.store.ws_offer(self.session)
        self.store.ws_claim(self.session, offer['id'])
        with self.assertRaises(Problem):
            self.ack(offer['id'], offer['command'], result='rejected')
        self.assertIsNone(self.store.ws_gateway)
        self.assertTrue(all(run['status'] == 'failed' for run in self.store.output_runner.snapshot().values()))
        self.session = self.store.ws_open(STATUS)
        self.assertIsNone(self.store.ws_offer(self.session))

    def test_cancellation_during_off_confirmation_does_not_extend_five_seconds(self):
        run = self.start('fill')
        self.start('drain', 2)
        self.process()
        self.pulse(170, process=False)
        offer = self.store.ws_offer(self.session)
        self.store.ws_claim(self.session, offer['id'])
        self.status.update(fill='0', state='DRAINING')
        self.store.ws_touch(self.session, self.status)
        self.pulse(4, process=False)
        self.store.output_runner.cancel(dict(direction='fill', run_id=run['id']))
        self.assertEqual(self.store.output_runner.runtime['fill']['off_id'], offer['id'])
        self.assertEqual(self.store.output_runner.snapshot()['fill']['elapsed_seconds'], 170)
        with self.assertRaises(Problem):
            self.pulse(1, process=False)
        self.assertIsNone(self.store.ws_gateway)
        self.assertEqual(self.store.output_runner.runs['fill']['reason'], 'stop_unconfirmed')
        self.assertEqual(self.store.output_runner.runs['drain']['status'], 'failed')

    def test_repeated_start_keeps_frozen_duration_and_current_round_deadline(self):
        self.start('fill')
        self.process()
        self.pulse(20)
        stop_at = self.store.output_runner.runtime['fill']['stop_at']
        repeated = self.start('fill')
        self.assertEqual(repeated['elapsed_seconds'], 20)
        self.assertEqual(repeated['total_seconds'], 200)
        self.assertEqual(self.store.output_runner.runtime['fill']['stop_at'], stop_at)
        self.assertEqual([name for _, name in self.commands].count('FILL'), 1)

    def test_lost_new_pings_fail_even_when_old_water_estimate_was_uncertain(self):
        self.store.freeze_estimate()
        self.start('fill')
        self.process()
        self.pulse(2)
        activity = self.store.ws_activity_tick
        self.now += 4
        self.assertFalse(self.store.ws_ping(self.session, self.sequence))
        self.assertEqual(self.store.ws_activity_tick, activity)
        self.now += 2
        with self.assertRaises(Problem):
            self.store.ws_ping(self.session, self.sequence + 1)
        self.assertIsNone(self.store.ws_gateway)

    def test_duplicate_ids_are_persistent_and_never_start_another_run(self):
        self.calibrate(fill=1)
        first = self.start('fill')
        self.process()
        self.pulse(1)
        self.assertEqual(self.start('fill')['status'], 'completed')
        self.assertIsNone(self.store.ws_offer(self.session))
        self.start('fill', 2)
        self.assertEqual(self.start('fill')['id'], first['id'])
        self.assertEqual(self.store.output_runner.runs['fill']['id'], '{:032x}'.format(2))
        self.store.db.close()
        self.store = Store(self.path, lambda: self.now)
        self.assertEqual(self.start('fill')['status'], 'completed')
        self.assertEqual(self.store.output_runner.runs['fill']['reason'], 'server_restarted')
        with self.assertRaises(Problem) as caught:
            self.start('drain')
        self.assertEqual(caught.exception.message, 'request_id_conflict')

    def test_same_direction_on_calibration_and_target_cannot_replace_running_work(self):
        run = self.start('fill')
        before = copy.deepcopy(self.store.simulation)
        for call in (lambda: self.store.enqueue('FILL', 'f' * 32), lambda: self.calibrate(),
                     lambda: self.store.start_level_job(dict(target_level=10)), lambda: self.start('fill', 2)):
            with self.assertRaises(Problem):
                call()
            self.assertEqual(self.store.output_runner.runs['fill']['id'], run['id'])
            self.assertEqual(self.store.output_runner.runs['fill']['status'], 'running')
        self.assertEqual(self.store.simulation, before)

    def test_delayed_on_receipt_shortens_round_and_updates_round_estimate(self):
        self.calibrate(fill=170)
        self.start('fill')
        offer = self.store.ws_offer(self.session)
        self.store.ws_claim(self.session, offer['id'])
        self.pulse(1, process=False)
        self.ack(offer['id'], 'FILL')
        self.assertEqual(self.store.output_runner.snapshot()['fill']['estimated_rounds'], 2)
        self.pulse(172)
        run = self.store.output_runner.snapshot()['fill']
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(run['round'], 2)
        self.assertEqual(run['elapsed_seconds'], 170)

    def test_bad_api_fields_return_problem_without_any_queued_commands(self):
        for direction in ([], {}, None, 'other'):
            with self.subTest(direction=direction):
                for value in (dict(direction=direction, id='a' * 32), dict(direction=direction, run_id='a' * 32)):
                    with self.assertRaises(Problem) as caught:
                        (self.store.output_runner.start if 'id' in value else self.store.output_runner.cancel)(value)
                    self.assertEqual(caught.exception.code, 400)
        self.assertIsNone(self.store.ws_offer(self.session))


class OutputRunHTTPTests(unittest.TestCase):
    def test_routes_require_session_and_origin_then_start_cancel(self):
        store = Store(':memory:')
        store.ws_open(STATUS)
        store.configure_simulation(dict(level=40, fill_seconds=200, drain_seconds=400))
        server = Server(('127.0.0.1', 0), store, 'a' * 32, 'b' * 32, 'https://example.test')
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        token = store.create_admin_session('a' * 32)
        try:
            for route in ('output-run', 'output-run/cancel'):
                for authorized, origin, expected in ((False, True, 401), (True, False, 403)):
                    headers = {'Content-Type': 'application/json'}
                    if authorized:
                        headers['Cookie'] = 'water_session=' + token
                    if origin:
                        headers['Origin'] = 'https://example.test'
                    request = urllib.request.Request('http://127.0.0.1:%d/water/api/%s' % (server.server_port, route), b'{}', headers)
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(request)
                    self.assertEqual(caught.exception.code, expected)
            for route, value, expected in (('output-run', dict(direction='fill', id='a' * 32), 202),
                                           ('output-run/cancel', dict(direction='fill', run_id='a' * 32), 200)):
                request = urllib.request.Request('http://127.0.0.1:%d/water/api/%s' % (server.server_port, route), json.dumps(value).encode(),
                                                 {'Content-Type': 'application/json', 'Origin': 'https://example.test', 'Cookie': 'water_session=' + token})
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, expected)
                    self.assertEqual(json.load(response)['id'], 'a' * 32)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
            store.db.close()


if __name__ == '__main__':
    unittest.main()
