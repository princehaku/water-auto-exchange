"""The aquarium level is an estimate driven by observed output transitions."""
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

    def calibrate(self, level=40):
        return self.store.configure_simulation(dict(level=level, fill_seconds=100, drain_seconds=50))

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
