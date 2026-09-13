"""The aquarium level is an estimate driven by observed output transitions."""
import json
import tempfile
import unittest
from pathlib import Path

from server.app import Problem, Store


STATUS = dict(project='water_auto_exchange', version='0.8.0', control_mode='manual',
              state='IDLE', reason='ready', ready='1', fill='0', drain='0',
              outputs_known='1', need_fill='unknown', overflow='0', cycle='0',
              overflow_protection='0')
GATEWAY = 'a' * 32


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.store = Store(':memory:', lambda: self.now)
        self.report()

    def tearDown(self):
        self.store.db.close()

    def report(self, **changes):
        self.store.poll(GATEWAY, dict(STATUS, **changes))

    def calibrate(self, level=40, **changes):
        return self.store.configure_simulation(dict(level=level, fill_seconds=100, drain_seconds=50, **changes))

    def simulation(self):
        return self.store.snapshot()['simulation']

    def test_volume_requires_capacity_but_percent_eta_does_not(self):
        empty = self.simulation()
        for key in ('capacity_liters', 'volume_liters', 'fill_rate_lpm', 'drain_rate_lpm',
                    'net_lpm', 'fill_run_liters', 'drain_run_liters', 'fill_total_liters',
                    'drain_total_liters', 'eta_full_seconds', 'eta_empty_seconds'):
            self.assertIsNone(empty[key], key)
        self.calibrate()
        self.now += 1
        self.report(state='FILLING', fill='1')
        sim = self.simulation()
        self.assertEqual(sim['eta_full_seconds'], 60)
        self.assertIsNone(sim['net_lpm'])
        self.assertIsNone(sim['fill_run_liters'])
        self.now += 4
        self.report()
        self.assertEqual(self.simulation()['level'], 44)
        self.assertIsNone(self.simulation()['volume_liters'])

    def test_capacity_optional_preserved_cleared_and_validated(self):
        for capacity in (0, .099, 100001, True, False, '60', [], {}, float('nan'), float('inf')):
            with self.subTest(capacity=capacity), self.assertRaises(Problem) as error:
                self.calibrate(capacity_liters=capacity)
            self.assertEqual(error.exception.message, 'invalid_simulation')
        for capacity in (.1, 60.5, 100000):
            self.assertEqual(self.calibrate(capacity_liters=capacity)['capacity_liters'], capacity)
        self.assertEqual(self.calibrate()['capacity_liters'], 100000)
        self.assertEqual(self.calibrate()['volume_liters'], 40000)
        self.assertIsNone(self.calibrate(capacity_liters=None)['capacity_liters'])
        self.assertIsNone(self.calibrate()['fill_total_liters'])

    def test_independent_volume_runs_totals_and_net_eta(self):
        sim = self.calibrate(capacity_liters=60)
        self.assertEqual(sim['volume_liters'], 24)
        self.assertEqual(sim['fill_rate_lpm'], 36)
        self.assertEqual(sim['drain_rate_lpm'], 72)
        self.assertEqual(sim['net_lpm'], 0)
        self.assertIsNone(sim['eta_full_seconds'])
        self.assertIsNone(sim['eta_empty_seconds'])
        self.now = 1001
        self.report(state='FILLING', fill='1')
        self.assertEqual(self.simulation()['net_lpm'], 36)
        self.assertEqual(self.simulation()['eta_full_seconds'], 60)
        self.now = 1006
        self.report(state='EXCHANGING', fill='1', drain='1')
        sim = self.simulation()
        self.assertEqual(sim['fill_run_liters'], 3)
        self.assertEqual(sim['drain_run_liters'], 0)
        self.assertEqual(sim['net_lpm'], -36)
        self.assertEqual(sim['eta_empty_seconds'], 45)
        self.assertIsNone(sim['eta_full_seconds'])
        self.now = 1016
        self.report(state='DRAINING', drain='1')
        sim = self.simulation()
        self.assertEqual(sim['fill_run_liters'], 9)
        self.assertEqual(sim['drain_run_liters'], 12)
        self.assertEqual(sim['fill_total_liters'], 9)
        self.assertEqual(sim['drain_total_liters'], 12)
        self.assertEqual(sim['net_lpm'], -72)
        self.assertEqual(sim['eta_empty_seconds'], 17.5)
        self.now = 1021
        self.report(state='EXCHANGING', fill='1', drain='1')
        sim = self.simulation()
        self.assertEqual(sim['fill_run_liters'], 0)
        self.assertEqual(sim['drain_run_liters'], 18)
        self.assertEqual(sim['fill_total_liters'], 9)
        self.now = 1026
        self.report()
        sim = self.simulation()
        self.assertEqual(sim['fill_run_liters'], 3)
        self.assertEqual(sim['drain_run_liters'], 24)
        self.assertEqual(sim['fill_total_liters'], 12)
        self.assertEqual(sim['drain_total_liters'], 24)
        self.assertEqual(sim['volume_liters'], 12)
        self.assertEqual(sim['net_lpm'], 0)

    def test_reading_snapshot_and_queueing_commands_never_adds_liters(self):
        self.calibrate(capacity_liters=60)
        self.store.enqueue('FILL', '1' * 32)
        self.now += 5
        for _ in range(5):
            self.assertEqual(self.simulation()['fill_total_liters'], 0)
        self.report(state='FILLING', fill='1')
        self.now += 3
        for _ in range(5):
            self.assertEqual(self.simulation()['fill_run_liters'], 0)
            self.assertEqual(self.simulation()['level'], 40)
        self.store.poll(GATEWAY, dict(STATUS, state='FILLING', fill='1'),
                        dict(id='1' * 32, status='succeeded', result='OK FILL'))
        self.assertAlmostEqual(self.simulation()['fill_run_liters'], 1.8)
        # Another ON is idempotent, even if command delivery and a same-time
        # device report both occur; only elapsed confirmed time counts.
        self.store.enqueue('FILL', '2' * 32)
        self.report(state='FILLING', fill='1')
        self.assertAlmostEqual(self.simulation()['fill_run_liters'], 1.8)

    def test_volume_keeps_counting_at_full_and_empty_limits(self):
        self.calibrate(99, capacity_liters=60)
        self.now += 1
        self.report(state='FILLING', fill='1')
        self.now += 10
        self.report(state='FILLING', fill='1')
        sim = self.simulation()
        self.assertEqual(sim['level'], 100)
        self.assertEqual(sim['volume_liters'], 60)
        self.assertEqual(sim['fill_run_liters'], 6)
        self.assertEqual(sim['eta_full_seconds'], 0)
        self.report()
        self.calibrate(1)
        self.report(state='DRAINING', drain='1')
        self.now += 10
        self.report(state='DRAINING', drain='1')
        sim = self.simulation()
        self.assertEqual(sim['level'], 0)
        self.assertEqual(sim['drain_run_liters'], 12)
        self.assertEqual(sim['eta_empty_seconds'], 0)

    def test_equal_dual_rates_count_both_without_eta(self):
        self.store.configure_simulation(dict(level=50, fill_seconds=60, drain_seconds=60, capacity_liters=60))
        self.report(state='EXCHANGING', fill='1', drain='1')
        self.now += 10
        self.report(state='EXCHANGING', fill='1', drain='1')
        sim = self.simulation()
        self.assertEqual(sim['fill_run_liters'], 10)
        self.assertEqual(sim['drain_run_liters'], 10)
        self.assertEqual(sim['level'], 50)
        self.assertEqual(sim['net_lpm'], 0)
        self.assertIsNone(sim['eta_full_seconds'])
        self.assertIsNone(sim['eta_empty_seconds'])

    def test_unknown_report_stops_integration_and_preserves_last_run(self):
        self.calibrate(capacity_liters=60)
        self.report(state='FILLING', fill='1')
        self.now += 5
        self.report(state='FILLING', fill='1')
        self.now += 3
        self.report(state='FAULT', outputs_known='0')
        sim = self.simulation()
        self.assertTrue(sim['uncertain'])
        self.assertEqual(sim['level'], 45)
        self.assertEqual(sim['fill_total_liters'], 3)
        self.assertIsNone(sim['net_lpm'])
        self.assertIsNone(sim['eta_full_seconds'])
        self.now += 1
        self.report(state='FILLING', fill='1')
        self.now += 5
        self.report(state='FILLING', fill='1')
        self.assertEqual(self.simulation()['fill_run_liters'], 3)
        self.assertEqual(self.simulation()['fill_total_liters'], 3)

    def test_unknown_idle_output_also_requires_recalibration(self):
        self.calibrate(capacity_liters=60)
        self.now += 1
        self.report(state='FAULT', outputs_known='0')
        self.report()
        self.assertTrue(self.simulation()['uncertain'])
        self.assertIsNone(self.simulation()['net_lpm'])

    def test_twelve_second_report_boundary_and_gap_freeze_counts(self):
        self.calibrate(capacity_liters=100)
        self.report(state='FILLING', fill='1')
        self.now += 12
        self.report(state='FILLING', fill='1')
        self.assertEqual(self.simulation()['fill_total_liters'], 12)
        self.now += 12.01
        self.report(state='FILLING', fill='1')
        self.assertEqual(self.simulation()['fill_total_liters'], 12)
        self.assertTrue(self.simulation()['uncertain'])
        self.now += 1
        self.report()
        self.assertEqual(self.simulation()['fill_total_liters'], 12)

    def test_calibration_resets_all_volume_counters(self):
        self.calibrate(capacity_liters=60)
        self.report(state='EXCHANGING', fill='1', drain='1')
        self.now += 5
        self.report()
        self.assertEqual(self.simulation()['fill_total_liters'], 3)
        self.assertEqual(self.simulation()['drain_total_liters'], 6)
        self.assertEqual(self.simulation()['calibrated_at'], 1000)
        self.assertEqual(self.simulation()['updated_at'], 1005)
        sim = self.calibrate(70, capacity_liters=120)
        self.assertEqual(sim['volume_liters'], 84)
        self.assertEqual(sim['fill_rate_lpm'], 72)
        self.assertEqual(sim['calibrated_at'], 1005)
        for key in ('fill_run_liters', 'drain_run_liters', 'fill_total_liters', 'drain_total_liters'):
            self.assertEqual(sim[key], 0)

    def test_offline_calibration_is_rejected_and_trend_unavailable(self):
        self.calibrate(capacity_liters=60)
        self.now += 11
        sim = self.simulation()
        self.assertIsNone(sim['net_lpm'])
        self.assertEqual(sim['volume_liters'], 24)
        with self.assertRaises(Problem) as error:
            self.calibrate(capacity_liters=120)
        self.assertEqual(error.exception.message, 'simulation_requires_idle')
        self.assertEqual(self.simulation()['capacity_liters'], 60)

    def test_ping_without_output_report_does_not_keep_active_estimate_fresh(self):
        store = Store(':memory:', lambda: self.now)
        self.addCleanup(store.db.close)
        session = store.ws_open(STATUS)
        store.configure_simulation(dict(level=40, fill_seconds=100, drain_seconds=50, capacity_liters=60))
        store.ws_touch(session, dict(STATUS, state='FILLING', fill='1'))
        self.now += 5
        store.ws_touch(session, dict(STATUS, state='FILLING', fill='1'))
        self.now += 13
        store.ws_touch(session)
        snapshot = store.snapshot()
        self.assertTrue(snapshot['online'])
        self.assertTrue(snapshot['simulation']['uncertain'])
        self.assertEqual(snapshot['simulation']['fill_run_liters'], 3)
        self.assertIsNone(snapshot['simulation']['net_lpm'])
        store.ws_touch(session, dict(STATUS, state='FILLING', fill='1'))
        self.assertEqual(store.snapshot()['simulation']['fill_run_liters'], 3)

    def test_websocket_disconnect_and_reconnect_preserve_confirmed_volumes(self):
        store = Store(':memory:', lambda: self.now)
        self.addCleanup(store.db.close)
        session = store.ws_open(STATUS)
        store.configure_simulation(dict(level=40, fill_seconds=100, drain_seconds=50, capacity_liters=60))
        store.ws_touch(session, dict(STATUS, state='EXCHANGING', fill='1', drain='1'))
        self.now += 5
        store.ws_touch(session, dict(STATUS, state='EXCHANGING', fill='1', drain='1'))
        store.ws_close(session)
        self.now += 1
        session = store.ws_open(dict(STATUS, state='EXCHANGING', fill='1', drain='1'))
        self.now += 5
        store.ws_touch(session, dict(STATUS, state='EXCHANGING', fill='1', drain='1'))
        sim = store.snapshot()['simulation']
        self.assertTrue(sim['uncertain'])
        self.assertEqual(sim['fill_run_liters'], 3)
        self.assertEqual(sim['drain_run_liters'], 6)
        self.assertEqual(sim['fill_total_liters'], 3)
        self.assertEqual(sim['drain_total_liters'], 6)

    def test_old_saved_percentage_calibration_migrates_without_invented_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'water.db')
            first = Store(path, lambda: self.now)
            first.poll(GATEWAY, STATUS)
            first.configure_simulation(dict(level=55, fill_seconds=100, drain_seconds=50))
            legacy = {key: value for key, value in first.simulation.items()
                      if not key.endswith('_liters') and not key.endswith('_last_known_on') and key != 'calibrated_at'}
            first.db.execute('UPDATE aquarium_simulation SET value=? WHERE id=1', (json.dumps(legacy),))
            first.db.commit()
            first.db.close()
            second = Store(path, lambda: self.now)
            try:
                sim = second.snapshot()['simulation']
                self.assertEqual(sim['level'], 55)
                self.assertFalse(sim['uncertain'])
                self.assertIsNone(sim['capacity_liters'])
                self.assertIsNone(sim['calibrated_at'])
                self.assertIsNone(sim['fill_total_liters'])
                second.poll(GATEWAY, STATUS)
                second.configure_simulation(dict(level=55, fill_seconds=100, drain_seconds=50, capacity_liters=60))
                self.assertEqual(second.snapshot()['simulation']['fill_total_liters'], 0)
            finally:
                second.db.close()

    def test_active_restart_preserves_volume_and_never_fills_unobserved_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'water.db')
            first = Store(path, lambda: self.now)
            first.poll(GATEWAY, STATUS)
            first.configure_simulation(dict(level=55, fill_seconds=100, drain_seconds=50, capacity_liters=60))
            first.poll(GATEWAY, dict(STATUS, state='FILLING', fill='1'))
            self.now += 5
            first.poll(GATEWAY, dict(STATUS, state='FILLING', fill='1'))
            first.db.close()
            self.now += 5
            second = Store(path, lambda: self.now)
            try:
                second.poll(GATEWAY, dict(STATUS, state='FILLING', fill='1'))
                sim = second.snapshot()['simulation']
                self.assertEqual(sim['level'], 60)
                self.assertEqual(sim['capacity_liters'], 60)
                self.assertTrue(sim['uncertain'])
                self.assertEqual(sim['fill_run_liters'], 3)
                self.assertEqual(sim['fill_total_liters'], 3)
                second.poll(GATEWAY, STATUS)
                second.configure_simulation(dict(level=60, fill_seconds=100, drain_seconds=50))
                self.assertEqual(second.snapshot()['simulation']['capacity_liters'], 60)
                self.assertEqual(second.snapshot()['simulation']['fill_total_liters'], 0)
            finally:
                second.db.close()

    def test_idle_restart_preserves_last_completed_run_and_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'water.db')
            first = Store(path, lambda: self.now)
            first.poll(GATEWAY, STATUS)
            first.configure_simulation(dict(level=55, fill_seconds=100, drain_seconds=50, capacity_liters=60))
            first.poll(GATEWAY, dict(STATUS, state='EXCHANGING', fill='1', drain='1'))
            self.now += 5
            first.poll(GATEWAY, STATUS)
            first.db.close()
            self.now += 500
            second = Store(path, lambda: self.now)
            try:
                sim = second.snapshot()['simulation']
                self.assertEqual(sim['level'], 50)
                self.assertFalse(sim['uncertain'])
                self.assertEqual(sim['calibrated_at'], 1000)
                self.assertEqual(sim['fill_run_liters'], 3)
                self.assertEqual(sim['drain_run_liters'], 6)
                self.assertIsNone(sim['net_lpm'])
                second.poll(GATEWAY, STATUS)
                self.assertEqual(second.snapshot()['simulation']['net_lpm'], 0)
                second.poll(GATEWAY, dict(STATUS, state='FILLING', fill='1'))
                sim = second.snapshot()['simulation']
                self.assertEqual(sim['fill_run_liters'], 0)
                self.assertEqual(sim['drain_run_liters'], 6)
                self.assertEqual(sim['fill_total_liters'], 3)
            finally:
                second.db.close()

    def test_independent_outputs_and_net_flow(self):
        self.calibrate()
        self.now = 1001
        self.report(state='FILLING', fill='1')
        self.now = 1006
        self.report(state='EXCHANGING', fill='1', drain='1')
        sim = self.store.snapshot()['simulation']
        self.assertEqual(sim['level'], 45)
        self.assertEqual(sim['fill_on_since'], 1001)
        self.assertEqual(sim['drain_on_since'], 1006)
        self.now = 1016
        self.report(state='DRAINING', drain='1')
        self.assertEqual(self.store.snapshot()['simulation']['level'], 35)
        self.now = 1021
        self.report()
        sim = self.store.snapshot()['simulation']
        self.assertEqual(sim['level'], 25)
        self.assertIsNone(sim['fill_on_since'])
        self.assertIsNone(sim['drain_on_since'])
        self.assertFalse(sim['uncertain'])

    def test_repeated_report_does_not_restart_timer(self):
        self.calibrate()
        self.now = 1001
        self.report(state='FILLING', fill='1')
        self.now = 1004
        self.report(state='FILLING', fill='1')
        sim = self.store.snapshot()['simulation']
        self.assertEqual(sim['fill_on_since'], 1001)
        self.assertEqual(sim['level'], 43)

    def test_uncalibrated_unknown_active_report_requires_all_off_before_new_timers(self):
        self.report(state='EXCHANGING', fill='1', drain='1')
        self.assertEqual(self.simulation()['fill_on_since'], 1000)
        self.assertEqual(self.simulation()['drain_on_since'], 1000)
        self.now += 3
        self.report(state='FAULT', outputs_known='0')
        self.assertTrue(self.simulation()['uncertain'])
        self.now += 1
        self.report(state='EXCHANGING', fill='1', drain='1')
        sim = self.simulation()
        self.assertTrue(sim['uncertain'])
        self.assertIsNone(sim['fill_on_since'])
        self.assertIsNone(sim['drain_on_since'])
        self.report(state='DRAINING', drain='1')
        self.assertTrue(self.simulation()['uncertain'])
        self.assertIsNone(self.simulation()['drain_on_since'])
        self.report()
        self.assertFalse(self.simulation()['uncertain'])
        self.now += 2
        self.report(state='EXCHANGING', fill='1', drain='1')
        sim = self.simulation()
        self.assertFalse(sim['uncertain'])
        self.assertEqual(sim['fill_on_since'], 1006)
        self.assertEqual(sim['drain_on_since'], 1006)
        self.assertIsNone(sim['level'])

    def test_uncalibrated_disconnect_and_stale_reports_cannot_restart_active_timers(self):
        for interruption in ('disconnect', 'snapshot_gap', 'report_gap'):
            with self.subTest(interruption=interruption):
                store = Store(':memory:', lambda: self.now)
                self.addCleanup(store.db.close)
                session = store.ws_open(STATUS)
                active = dict(STATUS, state='EXCHANGING', fill='1', drain='1')
                store.ws_touch(session, active)
                if interruption == 'disconnect':
                    store.ws_close(session)
                    self.now += 1
                    session = store.ws_open(active)
                else:
                    self.now += 13
                    if interruption == 'snapshot_gap':
                        store.ws_touch(session)
                        self.assertTrue(store.snapshot()['simulation']['uncertain'])
                    store.ws_touch(session, active)
                sim = store.snapshot()['simulation']
                self.assertTrue(sim['uncertain'])
                self.assertIsNone(sim['fill_on_since'])
                self.assertIsNone(sim['drain_on_since'])
                store.ws_touch(session, STATUS)
                self.assertFalse(store.snapshot()['simulation']['uncertain'])
                self.now += 1
                store.ws_touch(session, active)
                sim = store.snapshot()['simulation']
                self.assertEqual(sim['fill_on_since'], self.now)
                self.assertEqual(sim['drain_on_since'], self.now)
                self.assertIsNone(sim['level'])

    def test_uncalibrated_active_restart_requires_all_off_before_new_timer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'water.db')
            first = Store(path, lambda: self.now)
            first.poll(GATEWAY, STATUS)
            first.poll(GATEWAY, dict(STATUS, state='FILLING', fill='1'))
            first.db.close()
            self.now += 5
            second = Store(path, lambda: self.now)
            try:
                second.poll(GATEWAY, dict(STATUS, state='FILLING', fill='1'))
                sim = second.snapshot()['simulation']
                self.assertTrue(sim['uncertain'])
                self.assertIsNone(sim['fill_on_since'])
                second.poll(GATEWAY, STATUS)
                self.assertFalse(second.snapshot()['simulation']['uncertain'])
                self.now += 1
                second.poll(GATEWAY, dict(STATUS, state='FILLING', fill='1'))
                sim = second.snapshot()['simulation']
                self.assertEqual(sim['fill_on_since'], self.now)
                self.assertFalse(sim['uncertain'])
                self.assertIsNone(sim['level'])
            finally:
                second.db.close()

    def test_active_gap_requires_recalibration(self):
        self.calibrate()
        self.now = 1001
        self.report(state='FILLING', fill='1')
        self.now = 1015
        self.report()
        sim = self.store.snapshot()['simulation']
        self.assertTrue(sim['uncertain'])
        self.assertEqual(sim['level'], 40)
        self.calibrate(65)
        self.assertFalse(self.store.snapshot()['simulation']['uncertain'])

    def test_disconnect_during_active_output_invalidates_estimate(self):
        self.calibrate()
        self.now = 1001
        self.report(state='DRAINING', drain='1')
        self.now = 1002
        self.store.gateway = None
        self.store.connection_event(False, 'connection_closed')
        self.assertTrue(self.store.snapshot()['simulation']['uncertain'])

    def test_calibration_requires_idle_and_valid_numbers(self):
        for value in (dict(level=-1, fill_seconds=100, drain_seconds=50),
                      dict(level=30, fill_seconds=0, drain_seconds=50),
                      dict(level=True, fill_seconds=100, drain_seconds=50)):
            with self.assertRaises(Problem):
                self.store.configure_simulation(value)
        self.now = 1001
        self.report(state='FILLING', fill='1')
        with self.assertRaises(Problem) as error:
            self.calibrate()
        self.assertEqual(error.exception.message, 'simulation_requires_idle')

    def test_persists_and_active_restart_is_uncertain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'water.db'
            first = Store(str(path), lambda: self.now)
            first.poll(GATEWAY, STATUS)
            first.configure_simulation(dict(level=55, fill_seconds=100, drain_seconds=50))
            self.now = 1001
            first.poll(GATEWAY, dict(STATUS, state='FILLING', fill='1'))
            first.db.close()
            second = Store(str(path), lambda: self.now)
            sim = second.snapshot()['simulation']
            self.assertEqual(sim['level'], 55)
            self.assertTrue(sim['uncertain'])
            second.db.close()


if __name__ == '__main__':
    unittest.main()
