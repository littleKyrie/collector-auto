import os
import contextlib
import io
import sys
import tempfile
import types
import unittest
from unittest import mock


serial_stub = types.ModuleType("serial")
serial_stub.Serial = mock.Mock()
sys.modules.setdefault("serial", serial_stub)

device_stub = types.ModuleType("Device")
device_stub.IMV_OK = 0
device_stub.CameraDevice = mock.Mock
sys.modules.setdefault("Device", device_stub)

import ImageNode as image_node_module


class FakeCamera:
    def __init__(self, name, capture_result=0):
        self.m_userId = name
        self.capture_result = capture_result
        self.capture_calls = []

    def snap_and_save(self, path):
        self.capture_calls.append(path)
        return self.capture_result


class FakeLens:
    def __init__(self, port, move_ok=True, reason="move_failed"):
        self.port = port
        self.move_ok = move_ok
        self.full_shot_position_valid = True
        self.full_shot_position_quality = "confirmed"
        self.last_measured_angle = 0.0
        self.full_shot_last_motion_result = {
            "ok": move_ok,
            "reason": "confirmed_stopped_at_target" if move_ok else reason,
        }
        self.calls = []
        self.initialize_calls = []

    def initialize_lens(self, **kwargs):
        self.initialize_calls.append(kwargs)
        return self.move_ok

    def move_to_absolute_angle(self, target, **kwargs):
        self.calls.append((target, kwargs))
        return self.move_ok


class FakeNode:
    def __init__(self, name, port, move_ok=True, capture_result=0):
        self.camera = FakeCamera(name, capture_result=capture_result)
        self.lens = FakeLens(port, move_ok=move_ok)
        self.enabled = True
        self.failure_reason = None
        self.failed_at_position = None
        self.failed_at_step = None

    isolate = image_node_module.ImagingNode.isolate
    status_summary = image_node_module.ImagingNode.status_summary


class ImagingSystemIsolationTests(unittest.TestCase):
    def make_system(self, nodes):
        system = image_node_module.ImagingSystem()
        system.nodes = nodes
        return system

    def test_failed_move_is_isolated_and_other_camera_continues(self):
        healthy = FakeNode("1", "COM7", move_ok=True)
        failed = FakeNode("2", "COM9", move_ok=False)
        system = self.make_system([healthy, failed])

        with mock.patch.object(image_node_module, "IMV_OK", 0), contextlib.redirect_stdout(
            io.StringIO()
        ):
            summary = system.parallel_move_lenses_by_camera(
                {"1": 100.0, "2": 200.0},
                position=23,
                step=1,
            )

        self.assertEqual(summary["successful"], ["1"])
        self.assertIn("2", summary["failed"])
        self.assertTrue(healthy.enabled)
        self.assertFalse(failed.enabled)
        self.assertEqual(failed.failed_at_position, 23)
        self.assertEqual(failed.failed_at_step, 1)
        self.assertEqual(
            healthy.lens.calls[0][1]["wait_policy"],
            image_node_module.WAIT_POLICY_FULL_SHOT,
        )

    def test_capture_filters_isolated_camera(self):
        healthy = FakeNode("1", "COM7")
        isolated = FakeNode("2", "COM9")
        with contextlib.redirect_stdout(io.StringIO()):
            isolated.isolate("previous_move_failed", position=22, step=1)
        system = self.make_system([healthy, isolated])

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            image_node_module, "IMV_OK", 0
        ), contextlib.redirect_stdout(io.StringIO()):
            summary = system.parallel_snap_rotation_step(
                temp_dir,
                position_index=23,
                step_index=1,
                camera_names=["1", "2"],
            )

            self.assertEqual(summary["successful"], ["1"])
            self.assertIn("2", summary["skipped"])
            self.assertEqual(len(healthy.camera.capture_calls), 1)
            self.assertEqual(isolated.camera.capture_calls, [])
            self.assertTrue(os.path.isdir(os.path.join(temp_dir, "1")))
            self.assertFalse(os.path.exists(os.path.join(temp_dir, "2")))

    def test_parallel_global_homing_uses_full_shot_policy(self):
        healthy = FakeNode("1", "COM7", move_ok=True)
        failed = FakeNode("2", "COM9", move_ok=False)
        failed.lens.full_shot_last_motion_result = {
            "ok": False,
            "reason": "initialization_recovery_exhausted",
        }
        system = self.make_system([healthy, failed])

        with contextlib.redirect_stdout(io.StringIO()):
            summary = system.parallel_global_homing()

        self.assertEqual(
            healthy.lens.initialize_calls[0]["wait_policy"],
            image_node_module.WAIT_POLICY_FULL_SHOT,
        )
        self.assertEqual(
            failed.lens.initialize_calls[0]["wait_policy"],
            image_node_module.WAIT_POLICY_FULL_SHOT,
        )
        self.assertIn("1", summary["successful"])
        self.assertIn("2", summary["failed"])
        self.assertEqual(failed.failure_reason, "initialization_recovery_exhausted")


if __name__ == "__main__":
    unittest.main()
