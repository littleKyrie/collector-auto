import contextlib
import io
import sys
import types
import unittest
from unittest import mock

serial_stub = types.ModuleType("serial")
serial_stub.Serial = mock.Mock()
sys.modules.setdefault("serial", serial_stub)

import LensController as lens_module
from LensController import (
    LensController,
    WAIT_POLICY_FULL_SHOT,
)


class SequenceLens(LensController):
    def __init__(self, sequence):
        super().__init__("TEST")
        self.sequence = list(sequence)
        self.index = 0

    def read_motor_status_and_position(self):
        if self.index < len(self.sequence):
            value = self.sequence[self.index]
            self.index += 1
            return value
        return self.sequence[-1]


class MonotonicClock:
    def __init__(self, step=0.25):
        self.value = 0.0
        self.step = step

    def __call__(self):
        value = self.value
        self.value += self.step
        return value


class FullShotWaitTests(unittest.TestCase):
    def run_wait(self, sequence, operation="home", expected=0.0, tolerance=5.0):
        lens = SequenceLens(sequence)
        clock = MonotonicClock()
        with mock.patch.object(lens_module.time, "monotonic", side_effect=clock), mock.patch.object(
            lens_module.time, "sleep", return_value=None
        ), contextlib.redirect_stdout(io.StringIO()):
            result = lens._wait_until_stopped(
                timeout_s=1.0,
                interval_s=0.1,
                expected_angle=expected,
                angle_tolerance=tolerance,
                wait_policy=WAIT_POLICY_FULL_SHOT,
                normal_timeout_s=1.0,
                hard_timeout_s=4.0,
                operation=operation,
            )
        return lens, result

    def test_ff_stable_near_zero_is_inferred_success(self):
        lens, result = self.run_wait([
            (0xFF, -2.65),
            (0xFF, -2.60),
            (0xFF, -2.62),
            (0xFF, -2.61),
            (0xFF, -2.63),
            (0xFF, -2.62),
        ])

        self.assertTrue(result[0])
        self.assertEqual(result[1], 0xFF)
        self.assertEqual(
            lens.full_shot_last_wait_result["reason"],
            "inferred_stable_at_target",
        )
        self.assertEqual(
            lens.full_shot_last_wait_result["position_quality"],
            "inferred",
        )

    def test_ff_changing_angle_reaches_hard_timeout(self):
        lens, result = self.run_wait(
            [(0xFF, -0.8 if index % 2 == 0 else -1.8) for index in range(40)]
        )

        self.assertFalse(result[0])
        self.assertIn(
            lens.full_shot_last_wait_result["reason"],
            {"timeout_unstable_at_target", "timeout_still_moving"},
        )

    def test_stopped_but_wrong_position_fails(self):
        lens, result = self.run_wait([(0x00, 20.0)])

        self.assertFalse(result[0])
        self.assertEqual(
            lens.full_shot_last_wait_result["reason"],
            "stopped_position_mismatch",
        )

    def test_wrong_boundary_during_home_fails(self):
        lens, result = self.run_wait([(0x0B, 0.0)])

        self.assertFalse(result[0])
        self.assertEqual(
            lens.full_shot_last_wait_result["reason"],
            "boundary_position_mismatch",
        )

    def test_legacy_default_keeps_existing_ff_timeout_behavior(self):
        lens = SequenceLens([(0xFF, -2.65)])
        clock = MonotonicClock(step=0.6)
        with mock.patch.object(lens_module.time, "time", side_effect=clock), mock.patch.object(
            lens_module.time, "sleep", return_value=None
        ), contextlib.redirect_stdout(io.StringIO()):
            stopped, status, angle = lens._wait_until_stopped(
                timeout_s=1.0,
                interval_s=0.1,
                expected_angle=0.0,
                angle_tolerance=5.0,
            )

        self.assertFalse(stopped)
        self.assertEqual(status, 0xFF)
        self.assertEqual(angle, -2.65)
        self.assertIsNone(lens.full_shot_last_wait_result)


class FullShotRecoveryTests(unittest.TestCase):
    def test_home_failure_runs_vendor_recovery_once(self):
        lens = LensController("TEST")
        lens.serial = mock.Mock(is_open=True)
        wait_results = [
            (False, 0xFF, -10.0),
            (True, 0x00, 0.1),
        ]

        def fake_wait(*args, **kwargs):
            stopped, status, angle = wait_results.pop(0)
            lens.full_shot_last_wait_result = {
                "ok": stopped,
                "reason": "confirmed_stopped_at_target" if stopped else "timeout_still_moving",
                "status": status,
                "real_angle": angle,
                "position_quality": "confirmed" if stopped else "unknown",
            }
            return stopped, status, angle

        with mock.patch.object(lens, "send_command", return_value=True) as send_command, mock.patch.object(
            lens, "_wait_until_stopped", side_effect=fake_wait
        ), mock.patch.object(lens_module.time, "sleep", return_value=None), contextlib.redirect_stdout(
            io.StringIO()
        ):
            ok = lens.return_to_home(wait_policy=WAIT_POLICY_FULL_SHOT)

        self.assertTrue(ok)
        commands = [call.args[0] for call in send_command.call_args_list]
        self.assertEqual(commands.count(lens_module.LENS_POWER_ON_HOMING_COMMAND), 1)
        self.assertEqual(commands.count(lens_module.LENS_RETURN_HOME_COMMAND), 2)
        self.assertTrue(lens.full_shot_last_motion_result["recovered"])
        self.assertTrue(lens.full_shot_position_valid)
        self.assertAlmostEqual(lens.current_angle, 0.1)


if __name__ == "__main__":
    unittest.main()
