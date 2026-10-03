"""HTTP intent and real WS receipts distinguish automatic OFF from manual STOP."""
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

import websocket

from server.app import Server, Store


STATUS = dict(project='water_auto_exchange', version='0.8.2', control_mode='manual',
              state='IDLE', reason='ready', ready='1', fill='0', drain='0',
              outputs_known='1', need_fill='unknown', overflow='0', cycle='0',
              overflow_protection='0')


class StopTransportTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.store = Store(':memory:', lambda: self.now)
        self.server = Server(('127.0.0.1', 0), self.store, 'a' * 32, 'b' * 32,
                             'https://example.test')
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.token = self.store.create_admin_session('a' * 32)
        self.client = websocket.create_connection(
            'ws://127.0.0.1:%d/water/api/device/ws' % self.server.server_port, timeout=2)
        self.pending, self.sequence = [], 0
        self.client.send(json.dumps(dict(type='auth', key='b' * 32, status=STATUS)))
        self.receive('ready')
        self.store.configure_simulation(dict(level=100, fill_seconds=4, drain_seconds=4))

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        for _ in range(100):
            if self.store.ws_gateway is None:
                break
            time.sleep(.01)
        self.store.db.close()

    def receive(self, kind):
        for index, item in enumerate(self.pending):
            if item['type'] == kind:
                return self.pending.pop(index)
        for _ in range(20):
            item = json.loads(self.client.recv())
            if item['type'] == kind:
                return item
            self.pending.append(item)
        self.fail('No message of type ' + kind)

    def request(self, route, body=None):
        headers = dict(Origin='https://example.test', Cookie='water_session=' + self.token)
        data = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(body).encode()
        url = 'http://127.0.0.1:%d/water/api/%s' % (self.server.server_port, route)
        with urllib.request.urlopen(urllib.request.Request(url, data, headers), timeout=2) as response:
            return json.load(response)

    def ping(self, seconds=1):
        self.now += seconds
        self.sequence += 1
        self.client.send(json.dumps(dict(type='ping', seq=self.sequence)))
        self.assertEqual(self.receive('pong')['seq'], self.sequence)

    def execute(self, command):
        offer = self.receive('offer')
        self.assertEqual(offer['command'], command)
        self.client.send(json.dumps(dict(type='claim', id=offer['id'])))
        self.assertEqual(self.receive('execute')['command'], command)
        status = dict(STATUS, reason='stopped')
        if command in ('FILL', 'DRAIN'):
            status.update(state='FILLING' if command == 'FILL' else 'DRAINING',
                          **{command.lower(): '1'})
        self.client.send(json.dumps(dict(type='ack', ack=dict(id=offer['id'],
            status='succeeded', result='OK ' + command), status=status)))
        self.receive('received')
        return offer['id']

    def test_automatic_drain_off_is_system_and_exchange_still_fills_to_completion(self):
        job = self.request('level-job', dict(mode='exchange', id='c' * 32))
        ids = [self.execute('DRAIN')]
        for _ in range(4):
            self.ping()
        ids.append(self.execute('DRAIN_OFF'))
        self.assertEqual(self.store.level_job['status'], 'running')
        self.ping(2)
        ids.append(self.execute('FILL'))
        for _ in range(4):
            self.ping()
        ids.append(self.execute('FILL_OFF'))
        state = self.request('status')
        self.assertEqual(state['level_job']['status'], 'completed')
        self.assertEqual(state['level_job']['id'], job['id'])
        rows = {row['id']: row for row in state['commands']}
        for command_id in ids:
            self.assertEqual(rows[command_id]['origin'], 'system')
            self.assertEqual(rows[command_id]['job_id'], job['id'])
        self.assertFalse(any(row['command'] == 'STOP' for row in rows.values()))

    def test_only_explicit_stop_intent_cancels_and_records_manual_source(self):
        job = self.request('level-job', dict(mode='exchange', id='d' * 32))
        self.execute('DRAIN')
        for payload in (dict(job_id=job['id'], intent=None),
                        dict(job_id=job['id'], intent='system_stop')):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request('level-job/cancel', payload)
            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(self.store.level_job['status'], 'running')
        self.request('level-job/cancel', dict(job_id=job['id'], intent='manual_stop',
            source='web_ui', event='click', button='exchange-stop-button'))
        stop_id = self.execute('STOP')
        state = self.request('status')
        self.assertEqual(state['level_job']['status'], 'cancelled')
        self.assertEqual(state['level_job']['stop_origin'], 'manual')
        stop = next(row for row in state['commands'] if row['id'] == stop_id)
        self.assertEqual((stop['origin'], stop['action'], stop['job_id']),
                         ('manual', 'manual_stop', job['id']))
        self.ping(2)
        self.assertEqual(self.store.level_job['status'], 'cancelled')
        self.assertFalse(any(row['command'] == 'FILL' for row in self.request('status')['commands']))

    def test_old_webpage_can_stop_without_inventing_a_manual_source(self):
        job = self.request('level-job', dict(mode='exchange', id='e' * 32))
        self.execute('DRAIN')
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('level-job/cancel', dict(job_id='f' * 32))
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(self.store.level_job['status'], 'running')
        self.request('level-job/cancel', dict(job_id=job['id']))
        stop_id = self.execute('STOP')
        state = self.request('status')
        self.assertEqual(state['level_job']['status'], 'cancelled')
        self.assertEqual(state['level_job']['stop_origin'], 'legacy_unknown')
        stop = next(row for row in state['commands'] if row['id'] == stop_id)
        self.assertEqual((stop['origin'], stop['action'], stop['job_id']),
                         ('legacy_unknown', 'legacy_stop_request', job['id']))
        self.ping(2)
        self.assertEqual(self.store.level_job['status'], 'cancelled')
        self.assertFalse(any(row['command'] == 'FILL' for row in self.request('status')['commands']))


if __name__ == '__main__':
    unittest.main()
