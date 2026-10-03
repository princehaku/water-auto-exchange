"""Timing bounds include execute authority even before its first ON receipt."""
import tempfile
import unittest
from pathlib import Path

from server.app import Problem, Store

STATUS = dict(project='water_auto_exchange', version='0.8.2', control_mode='manual',
              state='IDLE', reason='ready', ready='1', fill='0', drain='0', outputs_known='1',
              need_fill='unknown', overflow='0', cycle='0', overflow_protection='0')


class RecoveryBudgetTests(unittest.TestCase):
    def run_missing_receipt(self, rewind=False, restart=False):
        with tempfile.TemporaryDirectory() as directory:
            wall, mono = [1000.0], [1000.0]
            path = str(Path(directory) / 'water.db')
            store = Store(path, lambda: wall[0], lambda: mono[0])
            try:
                session = store.ws_open(STATUS)
                store.configure_simulation(dict(level=0, fill_seconds=120, drain_seconds=320))
                store.start_level_job(dict(mode='exchange', id='a' * 32))
                offer = store.ws_offer(session)
                store.ws_claim(session, offer['id'])
                for sequence in range(1, 8):
                    mono[0] += 1
                    wall[0] += 1
                    if rewind and sequence == 4:
                        wall[0] -= 20
                    store.ws_ping(session, sequence)
                if restart:
                    store.db.close()
                    store = Store(path, lambda: wall[0], lambda: mono[0])
                else:
                    mono[0] += 1
                    wall[0] += 1
                    with self.assertRaises(Problem):
                        store.ws_ping(session, 8)
                result = store.snapshot()['level_job']
                self.assertEqual((result['status'], result['phase']), ('running', 'paused'))
                return result['recovery']['used_max']['fill']
            finally:
                store.db.close()

    def test_missing_start_receipt_wall_rewind_does_not_reduce_reserved_time(self):
        baseline = self.run_missing_receipt()
        self.assertGreaterEqual(baseline, 17.1)
        self.assertAlmostEqual(self.run_missing_receipt(rewind=True), baseline)

    def test_missing_start_receipt_restart_retains_monotonic_checkpoint_duration(self):
        self.assertGreaterEqual(self.run_missing_receipt(rewind=True, restart=True), 17.1)

    def test_off_authority_to_receipt_delay_only_increases_runtime_upper_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [1000.0]
            store = Store(str(Path(directory) / 'water.db'), lambda: now[0])
            try:
                session = store.ws_open(STATUS)
                store.configure_simulation(dict(level=100, fill_seconds=120, drain_seconds=320))
                store.start_level_job(dict(mode='exchange', id='a' * 32))
                offer = store.ws_offer(session)
                store.ws_claim(session, offer['id'])
                on_status = dict(STATUS, drain='1', state='DRAINING')
                store.ws_touch(session, on_status, dict(id=offer['id'], status='succeeded', result='OK'))
                for sequence in range(1, 61):
                    now[0] += 1
                    store.ws_ping(session, sequence)
                off = store.ws_offer(session)
                self.assertEqual(off['command'], 'DRAIN_OFF')
                store.ws_claim(session, off['id'])
                for sequence in range(61, 65):
                    now[0] += 1
                    store.ws_ping(session, sequence)
                store.ws_touch(session, STATUS, dict(id=off['id'], status='succeeded', result='OK'))
                r = store.level_job['recovery']
                self.assertAlmostEqual(r['used_min']['drain'], 60)
                self.assertAlmostEqual(r['used_max']['drain'], 64)
                self.assertAlmostEqual(r['level_max'], 100 - 60 * 100 / 320)
                self.assertAlmostEqual(r['possible_extra_seconds'], 4)
            finally:
                store.db.close()

    def test_lost_off_receipt_unknown_tail_is_not_charged_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [1000.0]
            store = Store(str(Path(directory) / 'water.db'), lambda: now[0])
            try:
                session = store.ws_open(STATUS)
                store.configure_simulation(dict(level=100, fill_seconds=120, drain_seconds=320))
                store.start_level_job(dict(mode='exchange', id='a' * 32))
                offer = store.ws_offer(session)
                store.ws_claim(session, offer['id'])
                store.ws_touch(session, dict(STATUS, drain='1', state='DRAINING'),
                               dict(id=offer['id'], status='succeeded', result='OK'))
                for sequence in range(1, 61):
                    now[0] += 1
                    store.ws_ping(session, sequence)
                off = store.ws_offer(session)
                store.ws_claim(session, off['id'])
                for sequence in range(61, 65):
                    now[0] += 1
                    store.ws_ping(session, sequence)
                now[0] += 1
                with self.assertRaises(Problem):
                    store.ws_ping(session, 65)
                r = store.level_job['recovery']
                frozen = (r['used_min']['drain'], r['used_max']['drain'], r['possible_extra_seconds'])
                self.assertEqual(frozen[0], 60)
                self.assertGreaterEqual(frozen[1], 74.1)
                for _ in range(3):
                    now[0] += 1
                    store.control_tick()
                    store.ws_close(session)
                r = store.level_job['recovery']
                self.assertEqual((r['used_min']['drain'], r['used_max']['drain'], r['possible_extra_seconds']), frozen)
            finally:
                store.db.close()

    def test_idle_thirty_second_pings_can_restore_but_long_gap_needs_two_new_pings(self):
        for interruption in (False, True):
            with self.subTest(interruption=interruption), tempfile.TemporaryDirectory() as directory:
                now = [1000.0]
                store = Store(str(Path(directory) / 'water.db'), lambda: now[0])
                try:
                    session = store.ws_open(STATUS)
                    store.configure_simulation(dict(level=100, fill_seconds=120, drain_seconds=320))
                    store.start_level_job(dict(mode='exchange', id='a' * 32))
                    store.ws_close(session)
                    fault = dict(STATUS, state='FAULT', reason='communication_timeout', ready='0')
                    session = store.ws_open(fault)
                    now[0] += 30
                    store.ws_ping(session, 1)
                    self.assertIsNone(store.ws_offer(session))
                    now[0] += 36 if interruption else 30
                    store.ws_ping(session, 2)
                    if interruption:
                        self.assertIsNone(store.ws_offer(session))
                        now[0] += 30
                        store.ws_ping(session, 3)
                    offer = store.ws_offer(session)
                    self.assertEqual(offer['command'], 'RESET')
                    store.ws_claim(session, offer['id'])
                    store.ws_touch(session, STATUS, dict(id=offer['id'], status='succeeded', result='OK'))
                    now[0] += 2
                    on = store.ws_offer(session)
                    self.assertEqual(on['command'], 'DRAIN')
                finally:
                    store.db.close()


if __name__ == '__main__':
    unittest.main()
