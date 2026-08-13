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
        lens, result = self.run_wait([(0x00, 20.0), (0x00, 20.0)])

        self.assertFalse(result[0])
        self.assertEqual(
            lens.full_shot_last_wait_result["reason"],
            "stopped_position_mismatch",
        )

    def test_wrong_boundary_during_home_fails(self):
        lens, result = self.run_wait([(0x0B, 0.0), (0x0B, 0.0)])

        self.assertFalse(result[0])
        self.assertEqual(
            lens.full_shot_last_wait_result["reason"],
            "boundary_direction_mismatch",
        )

    def test_single_bad_terminal_sample_is_confirmed_as_transient(self):
        lens, result = self.run_wait([
            (0x00, 20.0),
            (0x00, 0.2),
        ])

        self.assertTrue(result[0])
        self.assertEqual(
            lens.full_shot_last_wait_result["reason"],
            "confirmed_stopped_at_target",
        )
        self.assertEqual(
            lens.full_shot_last_wait_result["terminal_mismatch_samples"],
            1,
        )

    def test_stability_is_not_accepted_before_normal_timeout(self):
        sequence = [(0xFF, -0.1)] * 20 + [(0x00, 0.0)]
        lens, result = self.run_wait(sequence)

        self.assertTrue(result[0])
        self.assertGreaterEqual(lens.index, 3)

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

    @staticmethod
    def failed_move(reason="stopped_position_mismatch"):
        return {
            "ok": False,
            "reason": reason,
            "operation": "absolute_move",
            "status": 0x00,
            "real_angle": -0.03,
        }

    @staticmethod
    def successful_move(angle=1700.0):
        return {
            "ok": True,
            "reason": "confirmed_stopped_at_target",
            "operation": "absolute_move",
            "status": 0x00,
            "real_angle": angle,
            "position_quality": "confirmed",
        }

    def make_open_lens(self):
        lens = LensController("TEST")
        lens.serial = mock.Mock(is_open=True)
        lens.full_shot_position_valid = True
        lens.full_shot_position_quality = "confirmed"
        return lens

    def test_absolute_move_rebuilds_coordinates_and_replays_target(self):
        lens = self.make_open_lens()
        with mock.patch.object(
            lens,
            "_execute_absolute_move_once",
            side_effect=[self.failed_move(), self.successful_move()],
        ) as execute_move, mock.patch.object(
            lens,
            "_rebuild_coordinate_system_once",
            return_value={"ok": True, "reason": "coordinate_rebuild_succeeded", "home": {"ok": True}},
        ) as rebuild, contextlib.redirect_stdout(io.StringIO()):
            ok = lens.move_to_absolute_angle(1700.0, wait_policy=WAIT_POLICY_FULL_SHOT)

        self.assertTrue(ok)
        self.assertEqual(execute_move.call_count, 2)
        rebuild.assert_called_once_with(1)
        self.assertTrue(lens.full_shot_last_motion_result["recovered"])
        self.assertEqual(lens.full_shot_last_motion_result["recovery_attempts_used"], 1)

    def test_second_recovery_restarts_from_coordinate_rebuild(self):
        lens = self.make_open_lens()
        with mock.patch.object(
            lens,
            "_execute_absolute_move_once",
            side_effect=[
                self.failed_move(),
                self.failed_move("timeout_still_moving"),
                self.successful_move(),
            ],
        ), mock.patch.object(
            lens,
            "_rebuild_coordinate_system_once",
            side_effect=[
                {"ok": True, "reason": "coordinate_rebuild_succeeded", "home": {"ok": True}},
                {"ok": True, "reason": "coordinate_rebuild_succeeded", "home": {"ok": True}},
            ],
        ) as rebuild, contextlib.redirect_stdout(io.StringIO()):
            ok = lens.move_to_absolute_angle(1700.0, wait_policy=WAIT_POLICY_FULL_SHOT)

        self.assertTrue(ok)
        self.assertEqual(rebuild.call_count, 2)
        self.assertEqual(lens.full_shot_last_motion_result["recovery_attempts_used"], 2)

    def test_recovery_budget_resets_for_each_new_motion(self):
        lens = self.make_open_lens()
        with mock.patch.object(
            lens,
            "_execute_absolute_move_once",
            side_effect=[
                self.failed_move(), self.successful_move(1000.0),
                self.failed_move(), self.successful_move(1200.0),
            ],
        ), mock.patch.object(
            lens,
            "_rebuild_coordinate_system_once",
            return_value={"ok": True, "reason": "coordinate_rebuild_succeeded", "home": {"ok": True}},
        ) as rebuild, contextlib.redirect_stdout(io.StringIO()):
            first = lens.move_to_absolute_angle(1000.0, wait_policy=WAIT_POLICY_FULL_SHOT)
            first_attempts = lens.full_shot_last_motion_result["recovery_attempts_used"]
            second = lens.move_to_absolute_angle(1200.0, wait_policy=WAIT_POLICY_FULL_SHOT)
            second_attempts = lens.full_shot_last_motion_result["recovery_attempts_used"]

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(rebuild.call_count, 2)
        self.assertEqual((first_attempts, second_attempts), (1, 1))

    def test_exhausted_recovery_returns_structured_failure(self):
        lens = self.make_open_lens()
        with mock.patch.object(
            lens,
            "_execute_absolute_move_once",
            side_effect=[self.failed_move()],
        ), mock.patch.object(
            lens,
            "_rebuild_coordinate_system_once",
            side_effect=[
                {"ok": False, "reason": "recovery_home_failed", "home": {"ok": False}},
                {"ok": False, "reason": "recovery_home_failed", "home": {"ok": False}},
            ],
        ), contextlib.redirect_stdout(io.StringIO()):
            ok = lens.move_to_absolute_angle(1700.0, wait_policy=WAIT_POLICY_FULL_SHOT)

        self.assertFalse(ok)
        self.assertEqual(lens.full_shot_last_motion_result["reason"], "coordinate_recovery_exhausted")
        self.assertEqual(lens.full_shot_last_motion_result["recovery_attempts_used"], 2)
        self.assertFalse(lens.full_shot_position_valid)

    def test_out_of_range_target_is_clamped_before_motion(self):
        lens = self.make_open_lens()
        with mock.patch.object(
            lens,
            "_execute_full_shot_motion_with_recovery",
            return_value=True,
        ) as execute, contextlib.redirect_stdout(io.StringIO()):
            ok = lens.move_to_absolute_angle(9999.0, wait_policy=WAIT_POLICY_FULL_SHOT)

        self.assertTrue(ok)
        kwargs = execute.call_args.kwargs
        self.assertEqual(kwargs["requested_angle"], 9999.0)
        self.assertEqual(kwargs["effective_target"], lens_module.MOTOR_ANGLE_MAX)
        self.assertTrue(kwargs["target_clamped"])


class FullShotInitializationTests(unittest.TestCase):
    def make_open_lens(self):
        lens = LensController("TEST")
        lens.serial = mock.Mock(is_open=True)
        return lens

    def test_legacy_initialize_remains_default(self):
        lens = self.make_open_lens()
        with mock.patch.object(lens, "send_command", return_value=True), mock.patch.object(
            lens, "_wait_until_stopped", return_value=(True, 0x00, 0.0)
        ) as wait, mock.patch.object(
            lens_module.time, "sleep", return_value=None
        ), contextlib.redirect_stdout(io.StringIO()):
            ok = lens.initialize_lens()

        self.assertTrue(ok)
        self.assertNotIn("wait_policy", wait.call_args.kwargs)
        self.assertIsNone(lens.full_shot_last_motion_result)

    def test_full_shot_initialize_succeeds_without_using_recovery_budget(self):
        lens = self.make_open_lens()
        home_result = {
            "ok": True,
            "reason": "inferred_stable_at_target",
            "operation": "home",
            "status": 0xFF,
            "real_angle": -2.12,
            "position_quality": "inferred",
        }
        with mock.patch.object(lens, "send_command", return_value=True), mock.patch.object(
            lens, "_execute_home_once", return_value=home_result
        ), mock.patch.object(
            lens_module.time, "sleep", return_value=None
        ), contextlib.redirect_stdout(io.StringIO()):
            ok = lens.initialize_lens(wait_policy=WAIT_POLICY_FULL_SHOT)

        self.assertTrue(ok)
        self.assertEqual(lens.full_shot_last_motion_result["reason"], "initialized")
        self.assertEqual(lens.full_shot_last_motion_result["recovery_attempts_used"], 0)
        self.assertEqual(lens.full_shot_position_quality, "inferred")
        self.assertAlmostEqual(lens.current_angle, -2.12)

    def test_full_shot_initialize_recovers_after_initial_failure(self):
        lens = self.make_open_lens()
        initial_failure = {
            "ok": False,
            "reason": "timeout_still_moving",
            "operation": "home",
            "status": 0xFF,
            "real_angle": -8.0,
        }
        recovered_home = {
            "ok": True,
            "reason": "confirmed_stopped_at_target",
            "operation": "home",
            "status": 0x00,
            "real_angle": 0.1,
            "position_quality": "confirmed",
        }
        with mock.patch.object(lens, "send_command", return_value=True), mock.patch.object(
            lens, "_execute_home_once", return_value=initial_failure
        ), mock.patch.object(
            lens,
            "_rebuild_coordinate_system_once",
            return_value={
                "ok": True,
                "reason": "coordinate_rebuild_succeeded",
                "home": recovered_home,
            },
        ) as rebuild, mock.patch.object(
            lens_module.time, "sleep", return_value=None
        ), contextlib.redirect_stdout(io.StringIO()):
            ok = lens.initialize_lens(wait_policy=WAIT_POLICY_FULL_SHOT)

        self.assertTrue(ok)
        rebuild.assert_called_once_with(1)
        self.assertEqual(
            lens.full_shot_last_motion_result["reason"],
            "initialized_after_recovery",
        )
        self.assertEqual(lens.full_shot_last_motion_result["recovery_attempts_used"], 1)
        self.assertTrue(lens.full_shot_position_valid)

    def test_full_shot_initialize_isolatable_after_recovery_exhausted(self):
        lens = self.make_open_lens()
        initial_failure = {
            "ok": False,
            "reason": "timeout_still_moving",
            "operation": "home",
            "status": 0xFF,
            "real_angle": -8.0,
        }
        with mock.patch.object(lens, "send_command", return_value=True), mock.patch.object(
            lens, "_execute_home_once", return_value=initial_failure
        ), mock.patch.object(
            lens,
            "_rebuild_coordinate_system_once",
            side_effect=[
                {"ok": False, "reason": "recovery_home_failed", "home": {"ok": False}},
                {"ok": False, "reason": "recovery_home_failed", "home": {"ok": False}},
            ],
        ) as rebuild, mock.patch.object(
            lens_module.time, "sleep", return_value=None
        ), contextlib.redirect_stdout(io.StringIO()):
            ok = lens.initialize_lens(wait_policy=WAIT_POLICY_FULL_SHOT)

        self.assertFalse(ok)
        self.assertEqual(rebuild.call_count, lens_module.HOME_RECOVERY_MAX_ATTEMPTS)
        self.assertEqual(
            lens.full_shot_last_motion_result["reason"],
            "initialization_recovery_exhausted",
        )
        self.assertEqual(
            lens.full_shot_last_motion_result["recovery_attempts_used"],
            lens_module.HOME_RECOVERY_MAX_ATTEMPTS,
        )
        self.assertFalse(lens.full_shot_position_valid)


if __name__ == "__main__":
    unittest.main()
