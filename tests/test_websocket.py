import json
import sys
import threading
import time
import unittest
from pathlib import Path
import websocket

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.app import Server, Store, Problem

STATUS = dict(project='water_auto_exchange', version='0.5.0', state='IDLE', reason='ready',
              ready='1', fill='0', drain='0', outputs_known='1', need_fill='0', overflow='0',
              cycle='0', overflow_protection='0')


class WebSocketTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.store = Store(':memory:', lambda: self.now)
        self.server = Server(('127.0.0.1', 0), self.store, 'a'*32, 'b'*32, 'https://example.test')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        # Owner thread releases the session before closing SQLite.
        for _ in range(100):
            if self.store.ws_gateway is None: break
            time.sleep(.01)
        self.store.db.close()

    def connect(self, auth=True):
        client = websocket.create_connection('ws://127.0.0.1:%d/water/api/device/ws' % self.server.server_port, timeout=2)
        self.clients.append(client)
        if auth:
            client.send(json.dumps(dict(type='auth', key='b'*32, status=STATUS)))
            self.assertEqual(json.loads(client.recv())['type'], 'ready')
        return client

    def test_authenticated_roundtrip_and_ack(self):
        c = self.connect()
        self.store.enqueue('START', '1'*32)
        offer = json.loads(c.recv())
        self.assertEqual(offer['type'], 'offer')
        c.send(json.dumps(dict(type='claim', id=offer['id'])))
        execute = json.loads(c.recv())
        self.assertEqual(execute['type'], 'execute')
        self.assertTrue(0 < execute['ttl_ms'] <= 8000)
        c.send(json.dumps(dict(type='ack', ack=dict(id=offer['id'], status='succeeded', result='OK START started'), status=dict(STATUS, state='DRAINING', drain='1'))))
        self.assertEqual(json.loads(c.recv())['type'], 'received')
        self.assertEqual(self.store.snapshot()['commands'][0]['status'], 'succeeded')
        self.assertEqual(self.store.snapshot()['device']['state'], 'DRAINING')

    def test_wrong_key_never_registers(self):
        c = self.connect(False)
        c.send(json.dumps(dict(type='auth', key='a'*32, status=STATUS)))
        self.assertEqual(c.recv(), '')
        self.assertFalse(self.store.snapshot()['online'])

    def test_probe_does_not_create_device_or_commands(self):
        c = self.connect(False)
        c.send(json.dumps(dict(type='probe', key='b'*32)))
        self.assertEqual(json.loads(c.recv())['type'], 'probe_ok')
        self.assertIsNone(self.store.snapshot()['device'])

    def test_idle_heartbeat_and_immediate_disconnect(self):
        c = self.connect()
        self.now += 31
        self.assertTrue(self.store.snapshot()['online'])
        c.send(json.dumps(dict(type='ping', seq=42)))
        self.assertEqual(json.loads(c.recv()), dict(type='pong', seq=42))
        c.close()
        for _ in range(100):
            if self.store.ws_gateway is None: break
            time.sleep(.01)
        self.assertFalse(self.store.snapshot()['online'])

    def test_expired_offer_cannot_execute(self):
        c = self.connect()
        self.store.enqueue('START', '2'*32)
        offer = json.loads(c.recv())
        self.now += 9
        c.send(json.dumps(dict(type='claim', id=offer['id'])))
        self.assertEqual(json.loads(c.recv())['type'], 'expired')

    def test_disconnect_does_not_replay(self):
        c = self.connect()
        self.store.enqueue('START', '3'*32)
        c.recv()
        c.close()
        for _ in range(100):
            if self.store.ws_gateway is None: break
            time.sleep(.01)
        self.assertEqual(self.store.snapshot()['commands'][0]['status'], 'uncertain')
        c = self.connect()
        c.send(json.dumps(dict(type='ping', seq=1)))
        self.assertEqual(json.loads(c.recv())['type'], 'pong')

    def test_http_gateway_cannot_take_over_ws(self):
        self.connect()
        self.now += 31
        with self.assertRaises(Problem):
            self.store.poll('f'*32, STATUS)
        with self.assertRaises(Problem):
            self.store.hello()

    def test_second_ws_cannot_take_over(self):
        self.connect()
        session = self.store.ws_gateway
        c = self.connect(False)
        c.send(json.dumps(dict(type='auth', key='b'*32, status=STATUS)))
        self.assertEqual(c.recv(), '')
        self.assertEqual(self.store.ws_gateway, session)

    def test_fragmented_auth_and_coalesced_messages(self):
        c = self.connect(False)
        data = json.dumps(dict(type='auth', key='b'*32, status=STATUS))
        c.send_frame(websocket.ABNF.create_frame(data[:20], websocket.ABNF.OPCODE_TEXT, fin=0))
        c.send_frame(websocket.ABNF.create_frame(data[20:], websocket.ABNF.OPCODE_CONT, fin=1))
        self.assertEqual(json.loads(c.recv())['type'], 'ready')
        c.send(json.dumps(dict(type='ping', seq=1)))
        c.send(json.dumps(dict(type='ping', seq=2)))
        self.assertEqual(json.loads(c.recv())['seq'], 1)
        self.assertEqual(json.loads(c.recv())['seq'], 2)

    def test_oversized_message_closed(self):
        c = self.connect(False)
        c.send('x'*9000)
        self.assertEqual(c.recv(), '')
        self.assertFalse(self.store.snapshot()['online'])

    def test_duplicate_claim_no_second_execute(self):
        c = self.connect()
        self.store.enqueue('START', '4'*32)
        c.recv()
        claim = json.dumps(dict(type='claim', id='4'*32))
        c.send(claim)
        self.assertEqual(json.loads(c.recv())['type'], 'execute')
        c.send(claim)
        c.send(json.dumps(dict(type='ping', seq=2)))
        self.assertEqual(json.loads(c.recv())['type'], 'pong')


if __name__ == '__main__':
    unittest.main()
