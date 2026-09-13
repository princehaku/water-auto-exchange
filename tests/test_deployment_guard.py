import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('deployment_guard', Path(__file__).resolve().parents[1] / 'deploy/check-idle.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class DeploymentGuardTests(unittest.TestCase):
    def test_closed_outputs_and_finished_jobs_allow_update(self):
        self.assertEqual('', guard.idle_reason(self.idle()))

    @staticmethod
    def idle():
        return dict(online=True, device=dict(outputs_known='1', fill='0', drain='0'),
                    level_job=dict(status='completed'), output_runs=dict(fill=None, drain=dict(status='completed')),
                    commands=[], control_limits={})

    def test_every_active_or_unknown_path_defers_update(self):
        changes = [dict(online=False), dict(device=dict(outputs_known='0', fill='0', drain='0')),
                   dict(device=dict(outputs_known='1', fill='1', drain='0')),
                   dict(device=dict(outputs_known='1', fill='0', drain='1')),
                   dict(level_job=dict(status='running', phase='waiting')),
                   dict(output_runs=dict(fill=dict(status='running', phase='stopping'))),
                   dict(commands=[dict(status='queued')]), dict(commands=[dict(status='delivered')]),
                   dict(control_limits=dict(fill_deadline=123)), dict(control_limits=dict(timeout_pending='drain'))]
        for change in changes:
            with self.subTest(change=change):
                snapshot = copy.deepcopy(self.idle())
                snapshot.update(change)
                self.assertTrue(guard.idle_reason(snapshot))
