import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


# 路径辅助函数不依赖硬件。测试导入 main 时用最小桩替代未安装的 PLC 依赖。
modbus_stub = types.ModuleType("modbus_client")
modbus_stub.ModbusClient = type("ModbusClient", (), {})
rotation_stub = types.ModuleType("rotation_controller")
rotation_stub.RotationController = type("RotationController", (), {})

with mock.patch.dict(
    sys.modules,
    {
        "modbus_client": modbus_stub,
        "rotation_controller": rotation_stub,
    },
):
    import main


class FullShotArgumentTests(unittest.TestCase):
    def test_default_paths_point_to_project_directories(self):
        args = main.parse_args([])

        self.assertEqual(args.output_path, main.DEFAULT_OUTPUT_PATH)
        self.assertEqual(args.config_path, main.DEFAULT_CONFIG_PATH)

    def test_relative_paths_are_resolved_from_project_root(self):
        args = main.parse_args([
            "--output_path",
            os.path.join("relative", "results"),
            "--config_path",
            os.path.join("relative", "lens.json"),
        ])

        self.assertEqual(
            args.output_path,
            os.path.join(main.PROJECT_ROOT, "relative", "results"),
        )
        self.assertEqual(
            args.config_path,
            os.path.join(main.PROJECT_ROOT, "relative", "lens.json"),
        )

    def test_help_has_no_output_directory_side_effect(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "not-created")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as raised:
                    main.parse_args(["--help", "--output_path", output_path])

            self.assertEqual(raised.exception.code, 0)
            self.assertIn("--output_path", output.getvalue())
            self.assertIn("--config_path", output.getvalue())
            self.assertFalse(os.path.exists(output_path))


class OutputDirectoryTests(unittest.TestCase):
    def test_nonexistent_output_directory_is_created(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "results")
            config_path = os.path.join(temp_dir, "lens.json")

            resolved, state, removed_count = main.prepare_output_directory(
                output_path,
                project_root=os.path.join(temp_dir, "project"),
                config_path=config_path,
            )

            self.assertEqual(resolved, os.path.abspath(output_path))
            self.assertEqual(state, "created")
            self.assertEqual(removed_count, 0)
            self.assertTrue(os.path.isdir(output_path))
            self.assertEqual(os.listdir(output_path), [])

    def test_nonempty_output_directory_is_fully_cleared(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "results")
            camera_path = os.path.join(output_path, "1")
            os.makedirs(camera_path)
            Path(camera_path, "old.bmp").write_bytes(b"old image")
            Path(output_path, "unrelated.txt").write_text("old", encoding="utf-8")

            _, state, removed_count = main.prepare_output_directory(
                output_path,
                project_root=os.path.join(temp_dir, "project"),
                config_path=os.path.join(temp_dir, "lens.json"),
            )

            self.assertEqual(state, "cleared")
            self.assertEqual(removed_count, 2)
            self.assertEqual(os.listdir(output_path), [])

    def test_regular_file_cannot_be_used_as_output_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "result-file")
            Path(output_path).write_text("not a directory", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "不是目录"):
                main.prepare_output_directory(
                    output_path,
                    project_root=os.path.join(temp_dir, "project"),
                    config_path=os.path.join(temp_dir, "lens.json"),
                )

    def test_project_root_cannot_be_used_as_output_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "项目根目录"):
                main.prepare_output_directory(
                    temp_dir,
                    project_root=temp_dir,
                    config_path=os.path.join(temp_dir, "config", "lens.json"),
                )

    def test_filesystem_root_cannot_be_used_as_output_directory(self):
        filesystem_root = Path(main.PROJECT_ROOT).anchor
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "文件系统根目录"):
                main.validate_output_directory_path(
                    filesystem_root,
                    project_root=temp_dir,
                    config_path=os.path.join(temp_dir, "lens.json"),
                )

    def test_output_directory_cannot_contain_config_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "configs", "lens.json")
            os.makedirs(os.path.dirname(config_path))
            Path(config_path).write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "会删除配置"):
                main.prepare_output_directory(
                    temp_dir,
                    project_root=os.path.join(temp_dir, "project"),
                    config_path=config_path,
                )

            self.assertTrue(os.path.isfile(config_path))

    def test_directory_symlink_is_removed_without_touching_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "results")
            external_path = os.path.join(temp_dir, "external")
            os.makedirs(output_path)
            os.makedirs(external_path)
            external_file = Path(external_path, "keep.txt")
            external_file.write_text("keep", encoding="utf-8")
            link_path = os.path.join(output_path, "linked-directory")

            try:
                os.symlink(external_path, link_path, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境不能创建目录符号链接: {exc}")

            main.prepare_output_directory(
                output_path,
                project_root=os.path.join(temp_dir, "project"),
                config_path=os.path.join(temp_dir, "lens.json"),
            )

            self.assertFalse(os.path.lexists(link_path))
            self.assertEqual(external_file.read_text(encoding="utf-8"), "keep")


class FullShotConfigTests(unittest.TestCase):
    def test_missing_config_uses_empty_map(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "missing.json")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                data = main.load_full_shot_lens_range_map(config_path)

            self.assertEqual(data, {})
            self.assertIn(config_path, output.getvalue())

    def test_valid_config_is_loaded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "lens.json")
            expected = {"lens_ranges": {"1": {"calibrated": True}}}
            Path(config_path).write_text(
                json.dumps(expected, ensure_ascii=False),
                encoding="utf-8",
            )

            self.assertEqual(main.load_full_shot_lens_range_map(config_path), expected)

    def test_invalid_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "lens.json")
            Path(config_path).write_text("{invalid json", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "读取镜头配置失败"):
                main.load_full_shot_lens_range_map(config_path)

    def test_invalid_lens_ranges_shape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "lens.json")
            Path(config_path).write_text(
                json.dumps({"lens_ranges": []}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "lens_ranges"):
                main.load_full_shot_lens_range_map(config_path)


class FullShotIntegrationTests(unittest.TestCase):
    def test_custom_paths_are_used_and_output_is_cleared_only_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "results")
            config_path = os.path.join(temp_dir, "lens.json")
            log_path = os.path.join(temp_dir, "logs")
            os.makedirs(output_path)
            Path(output_path, "stale.txt").write_text("stale", encoding="utf-8")
            Path(config_path).write_text(
                json.dumps({
                    "lens_ranges": {
                        "1": {
                            "calibrated": True,
                            "angle_start": 0.0,
                            "angle_end": 100.0,
                            "source": "manual",
                        }
                    }
                }),
                encoding="utf-8",
            )

            class Camera:
                m_userId = "1"

            class Node:
                camera = Camera()

            class ImagingSystem:
                def __init__(self, lens_coordinate_mode):
                    self.nodes = [Node()]

                def init_system(self):
                    pass

                def open_all(self):
                    pass

                def parallel_global_homing(self):
                    pass

                def parallel_move_lenses_by_camera(self, target_angles):
                    pass

                def parallel_move_lenses(self, target_angle):
                    pass

                def parallel_snap_rotation_step(self, root, position, step):
                    camera_dir = os.path.join(root, "1")
                    os.makedirs(camera_dir, exist_ok=True)
                    Path(camera_dir, f"Position{position}_Step{step}.bmp").write_bytes(b"bmp")

                def close_all(self):
                    pass

            class Client:
                def __init__(self, host, port):
                    pass

                def connect(self):
                    return True

                def disconnect(self):
                    pass

            class Controller:
                def __init__(self, client):
                    pass

                def validate_parameters(self, rotations, speed, delay):
                    return True, ""

                def run_rotation_sequence(self, **kwargs):
                    callback = kwargs["progress_callback"]
                    callback(1, 2)
                    callback(2, 2)
                    return True

            image_node_stub = types.ModuleType("ImageNode")
            image_node_stub.ImagingSystem = ImagingSystem
            args = SimpleNamespace(
                output_path=output_path,
                config_path=config_path,
                lens_coordinate_mode="software",
                lens_steps=1,
                host="127.0.0.1",
                port=502,
                rotations=2,
                speed=5.0,
                delay=0.0,
                error_signal=True,
                error_continue_model=2,
            )

            with mock.patch.dict(sys.modules, {"ImageNode": image_node_stub}), mock.patch.object(
                main, "ModbusClient", Client
            ), mock.patch.object(
                main, "RotationController", Controller
            ), mock.patch.object(
                main, "FULL_SHOT_LOG_DIR", log_path
            ), contextlib.redirect_stdout(io.StringIO()):
                result = main.run_rotation_multi_shot(args)

            self.assertEqual(result, 0)
            self.assertFalse(os.path.exists(os.path.join(output_path, "stale.txt")))
            self.assertTrue(os.path.isfile(os.path.join(output_path, "1", "Position1_Step1.bmp")))
            self.assertTrue(os.path.isfile(os.path.join(output_path, "1", "Position2_Step1.bmp")))

            metadata_files = sorted(Path(log_path).glob("*/*_metadata.json"))
            self.assertEqual(len(metadata_files), 2)
            metadata = json.loads(metadata_files[0].read_text(encoding="utf-8"))
            self.assertEqual(metadata["lens_range_map_path"], main.metadata_path(config_path))
            self.assertEqual(
                metadata["image_path_pattern"],
                main.metadata_path(os.path.join(
                    output_path,
                    "{camera_user_id}",
                    "Position{i}_Step{j}.bmp",
                )),
            )


if __name__ == "__main__":
    unittest.main()
