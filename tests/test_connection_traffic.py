import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.app import Store, Problem

STATUS = dict(project='water_auto_exchange', version='0.7.6', control_mode='manual',
              state='IDLE', reason='ready', ready='1', fill='0', drain='0', outputs_known='1',
              need_fill='unknown', overflow='0', cycle='0', overflow_protection='0')

class ConnectionTrafficTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name)/'water.db')
        self.store = Store(self.path, lambda:self.now)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def report(self, session, total=1024, meter='a'*32, interval=1024, seconds=60):
        self.store.traffic_report(session, dict(meter=meter, total_bytes=total, interval_bytes=interval, interval_seconds=seconds))

    def test_close_preserves_last_seen_and_records_each_transition_once(self):
        session=self.store.ws_open(STATUS)
        self.now+=4;self.store.ws_touch(session)
        self.now+=1;self.store.ws_close(session,'peer_closed')
        for _ in range(3):
            snapshot=self.store.snapshot()
            self.assertFalse(snapshot['online'])
            self.assertEqual(snapshot['last_seen'],1004)
            self.assertEqual([e['state'] for e in snapshot['connection']['events']],['offline','online'])
            self.assertEqual(snapshot['connection']['reason'],'peer_closed')
        self.now+=1
        self.store.ws_open(STATUS)
        self.assertEqual(len(self.store.snapshot()['connection']['events']),3)

    def test_crash_restart_preserves_history_and_data_without_claiming_online(self):
        session=self.store.ws_open(STATUS);self.report(session)
        self.store.db.close();self.now+=20
        self.store=Store(self.path,lambda:self.now)
        snapshot=self.store.snapshot()
        self.assertFalse(snapshot['online'])
        self.assertEqual(snapshot['device']['version'],'0.7.6')
        self.assertEqual(snapshot['connection']['reason'],'server_restarted')
        self.assertEqual(snapshot['traffic']['total_bytes'],1024)
        self.assertIsNone(self.store.ws_gateway)

    def test_timeout_boundary_and_old_close_cannot_disconnect_new_session(self):
        old=self.store.ws_open(STATUS)
        self.now+=75;self.assertTrue(self.store.snapshot()['online'])
        self.now+=.1;self.assertFalse(self.store.snapshot()['online'])
        self.assertEqual(self.store.snapshot()['connection']['reason'],'heartbeat_timeout')
        self.store.ws_close(old)
        new=self.store.ws_open(STATUS)
        self.store.ws_close(old)
        self.assertTrue(self.store.snapshot()['online'])
        self.assertEqual(self.store.ws_gateway,new)

    def test_replayed_cumulative_traffic_is_not_double_counted_across_reconnect_and_restart(self):
        session=self.store.ws_open(STATUS)
        self.report(session)
        self.report(session)
        self.report(session,512,interval=512)
        self.assertEqual(self.store.snapshot()['traffic']['total_bytes'],1024)
        self.store.ws_close(session)
        self.store.db.close();self.store=Store(self.path,lambda:self.now)
        session=self.store.ws_open(STATUS)
        self.report(session,2048)
        self.now+=60;self.report(session,256,meter='b'*32,interval=256)
        traffic=self.store.snapshot()['traffic']
        self.assertEqual(traffic['total_bytes'],2304)
        self.assertEqual(traffic['boot_bytes'],256)
        self.assertEqual(traffic['last_report_at'],self.now)

    def test_invalid_traffic_and_stale_session_never_change_counters(self):
        session=self.store.ws_open(STATUS)
        cases=[dict(total=-1),dict(total=True),dict(total=float('nan')),dict(total=float('inf')),
               dict(total=1024.5),dict(total=1,interval=2),dict(seconds=0),dict(meter='<script>')]
        for values in cases:
            with self.assertRaises(Problem):self.report(session,**values)
        with self.assertRaises(Problem):self.report('c'*32)
        self.assertFalse(self.store.snapshot()['traffic']['available'])
        self.report(session,0,interval=0)
        self.assertTrue(self.store.snapshot()['traffic']['available'])
        self.assertEqual(self.store.snapshot()['traffic']['total_bytes'],0)

    def test_connection_history_is_bounded_and_heartbeats_do_not_create_events(self):
        for _ in range(40):
            session=self.store.ws_open(STATUS)
            for _ in range(5):self.store.ws_touch(session)
            self.now+=1;self.store.ws_close(session)
        self.assertEqual(len(self.store.snapshot()['connection']['events']),60)

    def test_expired_owner_is_reclaimed_without_allowing_healthy_session_takeover(self):
        old=self.store.ws_open(STATUS)
        self.store.enqueue('FILL','f'*32)
        self.now+=75
        with self.assertRaises(Problem):self.store.ws_open(STATUS)
        self.now+=.1
        new=self.store.ws_open(STATUS)
        self.assertNotEqual(old,new)
        self.assertTrue(self.store.snapshot()['online'])
        self.assertEqual(self.store.snapshot()['commands'][0]['status'],'uncertain')
        self.store.ws_close(old)
        self.assertEqual(self.store.ws_gateway,new)
        self.assertIsNone(self.store.ws_offer(new))

    def test_only_exact_previous_session_can_replace_a_current_owner(self):
        old=self.store.ws_open(STATUS)
        for invalid in (None,'',old+'x','a'*32,{},True):
            with self.assertRaises(Problem):self.store.ws_open(STATUS,invalid)
        new=self.store.ws_open(STATUS,old)
        self.assertNotEqual(old,new)
        with self.assertRaises(Problem):self.store.ws_open(STATUS,old)
        self.store.ws_close(old)
        self.assertEqual(self.store.ws_gateway,new)
        self.assertEqual(self.store.snapshot()['connection']['events'][1]['reason'],'session_replaced')
