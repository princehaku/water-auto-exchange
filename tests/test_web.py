import copy
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.app import Problem, Server, Store, validate_status

spec = importlib.util.spec_from_file_location('gateway', str(ROOT / 'tools/water-gateway.py'))
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)

STATUS = dict(project='water_auto_exchange', version='0.3.0', state='IDLE', reason='ready',
              ready='1', fill='0', drain='0', outputs_known='1', need_fill='0', overflow='0',
              cycle='0', overflow_protection='0')
GATEWAY = 'a' * 32


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.store = Store(':memory:', lambda: self.now)

    def tearDown(self):
        self.store.db.close()

    def online(self, **kwargs):
        status = dict(STATUS, **kwargs)
        return self.store.poll(GATEWAY, status)

    def command(self, command='START', number=1):
        return self.store.enqueue(command, '{:032x}'.format(number))

    def test_offline_rejects_all_commands(self):
        for command in ('START', 'FILL', 'STOP', 'RESET'):
            with self.assertRaises(Problem):
                self.command(command)
        self.assertEqual(self.store.snapshot()['commands'], [])

    def test_unconfigured_cannot_start(self):
        self.online(state='UNCONFIGURED', ready='0', outputs_known='0')
        with self.assertRaises(Problem):
            self.command()
        self.assertEqual(self.command('STOP')['status'], 'queued')

    def test_needs_fresh_status(self):
        self.online()
        self.now += 11
        self.assertFalse(self.store.snapshot()['online'])
        with self.assertRaises(Problem):
            self.command()

    def test_manual_switches_allow_unknown_water_level_and_preserve_interlock(self):
        self.online(version='0.7.0', control_mode='manual', need_fill='unknown')
        with self.assertRaises(Problem):
            self.command('START')
        self.assertEqual(self.command('FILL')['command'], 'FILL')
        self.online(version='0.7.0', control_mode='manual', need_fill='unknown')
        active=dict(STATUS,version='0.7.0',control_mode='manual',need_fill='unknown',state='FILLING',fill='1')
        self.store.poll(GATEWAY,active,dict(id='{:032x}'.format(1),status='succeeded',result='OK FILL fill_started'))
        with self.assertRaises(Problem):
            self.command('DRAIN',2)
        self.assertEqual(self.command('STOP',3)['command'],'STOP')
        self.store.poll(GATEWAY,active)
        off=dict(active,state='IDLE',fill='0')
        self.store.poll(GATEWAY,off,dict(id='{:032x}'.format(3),status='succeeded',result='OK STOP stopped'))
        self.assertEqual(self.command('DRAIN',4)['command'],'DRAIN')

    def test_manual_mode_is_explicit_and_legacy_cannot_bypass_level_checks(self):
        for version in ('0.7.0','0.7.1','0.7.2','0.7.3'):
            for mode in (None,'typo',True):
                with self.assertRaises(Problem):
                    self.online(version=version,control_mode=mode)
        self.online(version='0.6.0',control_mode='manual',need_fill='unknown')
        with self.assertRaises(Problem):
            self.command('FILL')
        self.online(version='0.7.0',control_mode='manual',need_fill='unknown',state='UNCONFIGURED',ready='0')
        with self.assertRaises(Problem):
            self.command('FILL')

    def test_drain_requires_new_firmware_and_independent_delivery(self):
        self.online(version='0.5.3')
        with self.assertRaises(Problem) as error:
            self.command('DRAIN')
        self.assertEqual(error.exception.message, 'firmware_upgrade_required')
        self.online(version='0.6.0')
        c = self.command('DRAIN')
        delivered = self.online(version='0.6.0')['command']
        self.assertEqual(delivered['command'], 'DRAIN')
        self.assertEqual(delivered['id'], c['id'])
        status = dict(STATUS, version='0.6.0', state='DONE', need_fill='1', reason='drain_completed')
        self.store.poll(GATEWAY, status, dict(id=c['id'], status='succeeded', result='OK DRAIN drain_started'))
        self.assertEqual(self.store.snapshot()['commands'][0]['status'], 'succeeded')
        self.assertEqual(self.command('FILL', 2)['command'], 'FILL')

    def test_drain_guards_and_stop_priority(self):
        for patch in (dict(need_fill='1'), dict(ready='0'), dict(overflow='1'),
                      dict(outputs_known='0'), dict(state='FAULT'), dict(state='DRAINING')):
            self.online(version='0.6.0', **patch)
            with self.assertRaises(Problem):
                self.command('DRAIN')
        self.online(version='0.6.0')
        self.command('DRAIN'); self.command('STOP', 2)
        self.assertEqual(self.online(version='0.6.0')['command']['command'], 'STOP')
        self.assertEqual(self.store.snapshot()['commands'][1]['status'], 'cancelled')

    def test_delivery_once_and_ack(self):
        self.online()
        c = self.command()
        result = self.online()
        self.assertEqual(result['command']['id'], c['id'])
        self.assertIsNone(self.online()['command'])
        self.store.poll(GATEWAY, STATUS, dict(id=c['id'], status='succeeded', result='OK START started'))
        self.assertEqual(self.store.snapshot()['commands'][0]['status'], 'succeeded')

    def test_expired_command_never_delivered(self):
        self.online()
        self.command()
        self.now += 9
        self.assertIsNone(self.online()['command'])
        self.assertEqual(self.store.snapshot()['commands'][0]['status'], 'expired')

    def test_missing_ack_uncertain_no_retry(self):
        self.online()
        self.command()
        self.online()
        self.now += 9
        self.assertIsNone(self.online()['command'])
        self.assertEqual(self.store.snapshot()['commands'][0]['status'], 'uncertain')

    def test_idempotency(self):
        self.online()
        self.command()
        self.command()
        self.assertEqual(len(self.store.snapshot()['commands']), 1)
        with self.assertRaises(Problem):
            self.command('STOP')

    def test_stop_cancels_queued_start(self):
        self.online()
        self.command()
        self.command('STOP', 2)
        self.assertEqual(self.online()['command']['command'], 'STOP')
        self.assertIsNone(self.online()['command'])
        self.assertEqual(self.store.snapshot()['commands'][1]['status'], 'cancelled')

    def test_stop_allowed_with_inflight(self):
        self.online()
        self.command()
        self.online()
        with self.assertRaises(Problem):
            self.command('START', 2)
        self.command('STOP', 3)
        self.assertEqual(self.online()['command']['command'], 'STOP')

    def test_level_and_fault_guards(self):
        for patch in (dict(need_fill='1'), dict(ready='0'), dict(overflow='1'),
                      dict(outputs_known='0'), dict(state='FAULT'), dict(state='DRAINING')):
            self.online(**patch)
            with self.assertRaises(Problem):
                self.command()
        self.online(need_fill='1')
        self.assertEqual(self.command('FILL')['status'], 'queued')

    def test_gateway_takeover_invalidates_work(self):
        self.online()
        self.command()
        with self.assertRaises(Problem):
            self.store.poll('b' * 32, STATUS)
        self.now += 16
        self.assertIsNone(self.store.poll('b' * 32, STATUS)['command'])
        self.assertEqual(self.store.snapshot()['commands'][0]['status'], 'uncertain')

    def test_restart_does_not_replay_or_claim_online(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'state.db')
            first = Store(path)
            first.poll(GATEWAY, STATUS)
            first.enqueue('START', '1' * 32)
            first.db.close()
            second = Store(path)
            self.assertFalse(second.snapshot()['online'])
            self.assertEqual(second.snapshot()['commands'][0]['status'], 'uncertain')
            second.db.close()

    def test_old_firmware_cannot_report(self):
        with self.assertRaises(Problem):
            self.online(project='gk21_motor_test', version='0.2.8')
        self.assertFalse(self.store.snapshot()['online'])

    def test_invalid_status_rejected(self):
        for patch in (dict(fill=True), dict(need_fill='2'), dict(state='made-up'), dict(cycle='NaN')):
            with self.assertRaises(Problem):
                validate_status(dict(STATUS, **patch))

    def test_concurrent_starts_only_one_queued(self):
        self.online()
        outcomes = []
        def send(n):
            try:
                self.command(number=n)
                outcomes.append(True)
            except Problem:
                outcomes.append(False)
        threads = [threading.Thread(target=send, args=(n,)) for n in range(1, 10)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(outcomes.count(True), 1)

    def test_cellular_handshake_returns_unique_session(self):
        first = self.store.hello()['gateway']
        self.assertEqual(len(first), 32)
        with self.assertRaises(Problem):
            self.store.hello()
        self.store.poll(first, dict(STATUS, version='0.4.0'))
        self.command()
        self.now += 16
        second = self.store.hello()['gateway']
        self.assertNotEqual(first, second)
        self.assertFalse(self.store.snapshot()['online'])
        self.assertIsNone(self.store.poll(second, STATUS)['command'])
        with self.assertRaises(Problem):
            self.store.poll(first, STATUS)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store(':memory:')
        cls.server = Server(('127.0.0.1', 0), cls.store, 'admin' * 10, 'device' * 10, 'https://example.test')
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:{}/water/api/'.format(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.store.db.close()

    def request(self, path, data=None, headers=None):
        request = urllib.request.Request(self.url + path, None if data is None else json.dumps(data).encode(), headers or {})
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response), response.headers

    def login(self):
        code, data, headers = self.request('login', dict(key='admin' * 10), {'Origin': 'https://example.test'})
        self.assertEqual(code, 200)
        cookie = headers['Set-Cookie']
        for attribute in ('Secure', 'HttpOnly', 'SameSite=Strict'):
            self.assertIn(attribute, cookie)
        return cookie.split(';')[0]

    def test_health_public_status_private(self):
        self.assertEqual(self.request('health')[0], 200)
        self.assertEqual(self.request('status')[0], 401)

    def test_session_login_logout(self):
        cookie = self.login()
        self.assertEqual(self.request('status', headers={'Cookie': cookie})[0], 200)
        headers = {'Cookie': cookie, 'Origin': 'https://example.test'}
        self.assertEqual(self.request('logout', {}, headers)[0], 200)
        self.assertEqual(self.request('status', headers={'Cookie': cookie})[0], 401)

    def test_csrf_rejected(self):
        cookie = self.login()
        self.assertEqual(self.request('commands', dict(command='START', id='f' * 32), {'Cookie': cookie, 'Origin': 'https://evil.test'})[0], 403)

    def test_device_and_admin_credentials_separate(self):
        data = dict(gateway=GATEWAY, status=STATUS)
        self.assertEqual(self.request('device/poll', data, {'Authorization': 'Bearer ' + 'admin' * 10})[0], 401)
        self.assertEqual(self.request('device/poll', data, {'Authorization': 'Bearer ' + 'device' * 10})[0], 200)

    def test_real_http_command_roundtrip(self):
        device_headers = {'Authorization': 'Bearer ' + 'device' * 10}
        self.request('device/poll', dict(gateway=GATEWAY, status=STATUS), device_headers)
        cookie = self.login()
        headers = {'Cookie': cookie, 'Origin': 'https://example.test'}
        self.assertEqual(self.request('commands', dict(command='START', id='e' * 32), headers)[0], 202)
        code, delivery, _ = self.request('device/poll', dict(gateway=GATEWAY, status=STATUS), device_headers)
        self.assertEqual(delivery['command']['command'], 'START')
        ack = dict(id='e' * 32, status='rejected', result='ERROR START inputs_not_stable')
        self.request('device/poll', dict(gateway=GATEWAY, status=STATUS, ack=ack), device_headers)
        self.assertEqual(self.request('status', headers=headers)[1]['commands'][0]['status'], 'rejected')


class FakeController:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def status(self):
        self.calls.append('STATUS')
        if self.fail:
            raise ValueError('old_firmware')
        return STATUS

    def command(self, command):
        self.calls.append(command)
        return 'OK ' + command + ' accepted'


class GatewayTests(unittest.TestCase):
    def test_drain_reidentifies_and_rejects_unsupported_firmware(self):
        controller = FakeController()
        result = gateway.execute(controller, dict(id='x', command='DRAIN'), 8000, 0)
        self.assertEqual(result['result'], 'firmware_upgrade_required')
        self.assertEqual(controller.calls, ['STATUS'])
        controller.status = lambda: dict(STATUS, version='0.6.0')
        result = gateway.execute(controller, dict(id='y', command='DRAIN'), 8000, 0)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(controller.calls[-1], 'DRAIN')

    def test_parse_old_firmware_rejected(self):
        with self.assertRaises(ValueError):
            gateway.parse_status('OK STATUS project=gk21_motor_test version=0.2.8')

    def test_parse_current(self):
        line = 'OK STATUS ' + ' '.join(k+'='+v for k,v in STATUS.items())
        self.assertEqual(gateway.parse_status(line), STATUS)

    def test_expired_never_touches_serial(self):
        controller = FakeController()
        result = gateway.execute(controller, dict(id='x', command='START'), 100, 0.2)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(controller.calls, [])

    def test_reidentify_before_mutation(self):
        controller = FakeController()
        result = gateway.execute(controller, dict(id='x', command='START'), 8000, 0)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(controller.calls, ['STATUS', 'START'])

    def test_unknown_command_rejected(self):
        controller = FakeController()
        gateway.execute(controller, dict(id='x', command='PROBE LOOP'), 8000, 0)
        self.assertEqual(controller.calls, [])

    def test_mismatched_firmware_never_mutated(self):
        controller = FakeController(True)
        result = gateway.execute(controller, dict(id='x', command='START'), 8000, 0)
        self.assertEqual(result['status'], 'uncertain')
        self.assertNotIn('START', controller.calls)
        self.assertNotIn('STOP', controller.calls)

    def test_lost_response_no_start_retry(self):
        controller = FakeController()
        def command(value):
            controller.calls.append(value)
            if value == 'START': raise TimeoutError()
            return 'OK STOP stopped'
        controller.command = command
        result = gateway.execute(controller, dict(id='x', command='START'), 8000, 0)
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(controller.calls.count('START'), 1)
        self.assertEqual(controller.calls[-1], 'STOP')


if __name__ == '__main__':
    unittest.main()
