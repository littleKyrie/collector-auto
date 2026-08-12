# -- coding: utf-8 --

from Device import *
from LensController import *
import concurrent.futures
import os
import time
import sys

# TODO: 修改为实际连接的相机名称和对应的串口
CAMERA_COM_MAP = {
    "1": "COM7",
    "2": "COM9",
    "3": "COM8",
    "4": "COM10",
}

class ImagingNode:
    def __init__(self, cam_index, cam_info, lens_coordinate_mode='software'):
        self.camera = CameraDevice()
        self.camera.init(cam_index, cam_info)
        self.lens_coordinate_mode = lens_coordinate_mode
        self.enabled = True
        self.failure_reason = None
        self.failed_at_position = None
        self.failed_at_step = None
        
        cam_name = self.camera.m_userId
        com_port = CAMERA_COM_MAP.get(cam_name, None)
        
        if not com_port:
            print(f"⚠️ 警告: 未在映射表中找到相机 [{cam_name}] 对应的串口号！镜头不可控。")
            self.lens = None
        else:
            self.lens = LensController(port=com_port, coordinate_mode=self.lens_coordinate_mode)
            self.lens.camera_name = cam_name

    def isolate(self, reason, position=None, step=None):
        if self.enabled:
            print(
                f"❌ [相机{self.camera.m_userId}/{getattr(self.lens, 'port', '无镜头')}] "
                f"已隔离: reason={reason}, position={position}, step={step}；其余相机继续。"
            )
        self.enabled = False
        self.failure_reason = reason
        self.failed_at_position = position
        self.failed_at_step = step

    def status_summary(self):
        return {
            "camera": self.camera.m_userId,
            "port": getattr(self.lens, "port", None),
            "enabled": self.enabled,
            "failure_reason": self.failure_reason,
            "failed_at_position": self.failed_at_position,
            "failed_at_step": self.failed_at_step,
            "position_valid": getattr(self.lens, "full_shot_position_valid", None),
            "position_quality": getattr(self.lens, "full_shot_position_quality", None),
            "last_measured_angle": getattr(self.lens, "last_measured_angle", None),
            "last_motion_result": getattr(self.lens, "full_shot_last_motion_result", None),
        }

    def open_node(self):
        cam_ret = self.camera.openDevice()
        if cam_ret == IMV_OK:
            self.camera.setEnumSymbol("TriggerMode", "On")
            self.camera.setEnumSymbol("TriggerSource", "Software")
            self.camera.cam.IMV_StartGrabbing()
            print(f"✅ 相机 [{self.camera.m_userId}] 开启成功并启动拉流。")
        else:
            print(f"❌ 相机 [{self.camera.m_userId}] 开启失败！")
            return False

        if self.lens:
            self.lens.open()
        return True

    def close_node(self):
        self.camera.cam.IMV_StopGrabbing()
        self.camera.closeDevice()
        if self.lens:
            self.lens.shutdown_lens()


class ImagingSystem:
    def __init__(self, lens_coordinate_mode='software'):
        self.nodes = []
        self.lens_coordinate_mode = lens_coordinate_mode

    def init_system(self):
        deviceList = IMV_DeviceList()
        nRet = MvCamera.IMV_EnumDevices(deviceList, IMV_EInterfaceType.interfaceTypeGige)
        if IMV_OK != nRet or deviceList.nDevNum == 0:
            print("❌ 枚举设备失败或未找到相机！")
            sys.exit()

        print(f"🔍 找到 {deviceList.nDevNum} 台相机。正在初始化成像节点...")
        for i in range(deviceList.nDevNum):
            node = ImagingNode(i, deviceList.pDevInfo[i], lens_coordinate_mode=self.lens_coordinate_mode)
            self.nodes.append(node)

    def open_all(self):
        for node in self.nodes:
            if not node.open_node():
                node.isolate("camera_open_failed")

    def close_all(self):
        for node in self.nodes:
            node.close_node()
        print("✅ 所有相机和镜头均已关闭。")

    def serial_global_homing(self):
        print("\n⚙️  ================ 执行系统全局绝对归零 ================")
        for node in self.nodes:
            if node.lens:
                node.lens.initialize_lens()
        print("✅  所有镜头全局归零完毕！绝对物理坐标系已确立。")

    def parallel_global_homing(self):
        print("\n⚙️  ================ 并发执行系统全局绝对归零 ================")
        nodes_with_lens = [node for node in self.nodes if node.lens and node.enabled]

        if not nodes_with_lens:
            print(" ⚠️ 没有可初始化的镜头。")
            return self._build_summary([], {}, [])

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes_with_lens)) as executor:
            futures = {
                executor.submit(node.lens.initialize_lens): node
                for node in nodes_with_lens
            }
            successful = []
            failed = {}
            for future, node in futures.items():
                cam_name = node.camera.m_userId
                try:
                    ok = bool(future.result())
                except Exception as exc:
                    ok = False
                    reason = f"initialize_exception: {exc}"
                else:
                    reason = "initialize_failed"

                if ok:
                    node.lens.mark_full_shot_initialized()
                    successful.append(cam_name)
                else:
                    node.isolate(reason)
                    failed[cam_name] = node.status_summary()

        if failed:
            print(f"⚠️ 全局归零部分完成，已隔离 {len(failed)} 台相机，其余继续。")
        else:
            print("✅  所有镜头全局归零完毕！绝对物理坐标系已确立。")
        return self._build_summary(successful, failed, [])

    @staticmethod
    def _build_summary(successful, failed, skipped):
        return {
            "ok": not failed,
            "successful": list(successful),
            "failed": dict(failed),
            "skipped": list(skipped),
        }

    def get_full_shot_status(self):
        return {
            node.camera.m_userId: node.status_summary()
            for node in self.nodes
        }

    def serial_move_lenses(self, target_angle):
        print(f"\n ⚙️ 正在向所有镜头串行下发转动指令 -> 目标角度: {target_angle}°")
        for node in self.nodes:
            if node.lens:
                node.lens.move_to_absolute_angle(target_angle)
        print(" ✅ 所有镜头均已到位。")

    def parallel_move_lenses(self, target_angle, position=None, step=None):
        print(f"\n ⚙️ 正在向所有镜头并发下发转动指令 -> 目标角度: {target_angle}°")
        nodes_with_lens = [node for node in self.nodes if node.lens and node.enabled]
        skipped = [
            node.camera.m_userId for node in self.nodes
            if not node.enabled or not node.lens
        ]
        
        if not nodes_with_lens:
            print(" ⚠️ 没有可控的镜头。")
            return self._build_summary([], {}, skipped)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes_with_lens)) as executor:
            futures = {
                executor.submit(
                    node.lens.move_to_absolute_angle,
                    target_angle,
                    wait_policy=WAIT_POLICY_FULL_SHOT,
                ): node
                for node in nodes_with_lens
            }
            successful = []
            failed = {}
            for future, node in futures.items():
                cam_name = node.camera.m_userId
                try:
                    ok = bool(future.result())
                except Exception as exc:
                    ok = False
                    node.lens.full_shot_last_motion_result = {
                        "ok": False,
                        "reason": "move_exception",
                        "error": str(exc),
                        "target_angle": target_angle,
                    }

                if ok:
                    successful.append(cam_name)
                else:
                    reason = (node.lens.full_shot_last_motion_result or {}).get(
                        "reason", "move_failed"
                    )
                    node.isolate(reason, position=position, step=step)
                    failed[cam_name] = node.status_summary()

        if failed:
            print(f" ⚠️ 部分镜头到位，已隔离 {len(failed)} 台，其余继续。")
        else:
            print(" ✅ 所有活动镜头均已同步到位。")
        return self._build_summary(successful, failed, skipped)

    def serial_move_lenses_by_camera(self, target_angles_by_camera):
        print("\n ⚙️ 正在按相机名串行下发镜头目标角度...")
        for node in self.nodes:
            cam_name = node.camera.m_userId
            if not node.lens:
                print(f" ⚠️ [{cam_name}] 没有可控镜头，跳过。")
                continue
            if cam_name not in target_angles_by_camera:
                print(f" ⚠️ [{cam_name}] 缺少目标角度，跳过。")
                continue
            target_angle = target_angles_by_camera[cam_name]
            print(f"    -> [{cam_name}] 目标角度: {target_angle}°")
            node.lens.move_to_absolute_angle(target_angle)
        print(" ✅ 按相机名串行移动完成。")

    def parallel_move_lenses_by_camera(self, target_angles_by_camera,
                                       position=None, step=None):
        print("\n ⚙️ 正在按相机名并发下发镜头目标角度...")
        nodes_with_targets = [
            node for node in self.nodes
            if node.enabled and node.lens and node.camera.m_userId in target_angles_by_camera
        ]
        skipped = [
            node.camera.m_userId for node in self.nodes
            if not node.enabled or not node.lens
        ]

        if not nodes_with_targets:
            print(" ⚠️ 没有可控镜头或没有匹配的目标角度。")
            return self._build_summary([], {}, skipped)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes_with_targets)) as executor:
            futures = {}
            for node in nodes_with_targets:
                cam_name = node.camera.m_userId
                target_angle = target_angles_by_camera[cam_name]
                print(f"    -> [{cam_name}] 目标角度: {target_angle}°")
                future = executor.submit(
                    node.lens.move_to_absolute_angle,
                    target_angle,
                    wait_policy=WAIT_POLICY_FULL_SHOT,
                )
                futures[future] = (node, target_angle)

            successful = []
            failed = {}
            for future, (node, target_angle) in futures.items():
                cam_name = node.camera.m_userId
                try:
                    ok = bool(future.result())
                except Exception as exc:
                    ok = False
                    node.lens.full_shot_last_motion_result = {
                        "ok": False,
                        "reason": "move_exception",
                        "error": str(exc),
                        "target_angle": target_angle,
                    }

                if ok:
                    successful.append(cam_name)
                else:
                    reason = (node.lens.full_shot_last_motion_result or {}).get(
                        "reason", "move_failed"
                    )
                    node.isolate(reason, position=position, step=step)
                    failed[cam_name] = node.status_summary()

        missing = [
            node.camera.m_userId for node in self.nodes
            if node.enabled and node.lens and node.camera.m_userId not in target_angles_by_camera
        ]
        for cam_name in missing:
            print(f" ⚠️ [{cam_name}] 缺少目标角度，未移动。")

        if failed:
            print(f" ⚠️ 按相机名并发移动部分完成，已隔离 {len(failed)} 台，其余继续。")
        else:
            print(" ✅ 按相机名并发移动完成。")
        return self._build_summary(successful, failed, skipped + missing)

    def serial_snap_all(self, save_dir_base):
        os.makedirs(save_dir_base, exist_ok=True)
        print(" 📸 正在依次触发拍摄并写入磁盘...")
        for node in self.nodes:
            cam_name = node.camera.m_userId
            save_path = os.path.join(save_dir_base, f"{cam_name}.bmp")
            
            t0 = time.time()
            ret = node.camera.snap_and_save(save_path)
            t1 = time.time()
            
            if ret == IMV_OK:
                print(f"    ✅ [{cam_name}] 保存成功 (耗时: {t1-t0:.2f}s) -> {save_path}")
            else:
                print(f"    ❌ [{cam_name}] 保存失败！错误码: {ret}")

    def serial_snap_rotation_step(self, output_root, position_index, step_index, extension=".bmp"):
        os.makedirs(output_root, exist_ok=True)
        print(" Taking rotation step images serially...")
        for node in self.nodes:
            cam_name = node.camera.m_userId
            save_dir = os.path.join(output_root, cam_name)
            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, f"Position{position_index}_Step{step_index}{extension}")

            t0 = time.time()
            ret = node.camera.snap_and_save(save_path)
            t1 = time.time()

            if ret == IMV_OK:
                print(f"    [{cam_name}] save ok ({t1-t0:.2f}s) -> {save_path}")
            else:
                print(f"    [{cam_name}] save failed, code: {ret}")

    def parallel_snap_rotation_step(self, output_root, position_index, step_index,
                                    extension=".bmp", camera_names=None):
        os.makedirs(output_root, exist_ok=True)
        print(" Taking rotation step images in parallel...")

        allowed_names = set(camera_names) if camera_names is not None else None
        nodes = [
            node for node in self.nodes
            if node.enabled and (
                allowed_names is None or node.camera.m_userId in allowed_names
            )
        ]
        skipped = [
            node.camera.m_userId for node in self.nodes
            if node not in nodes
        ]
        if not nodes:
            print(" No camera nodes available for capture.")
            return self._build_summary([], {}, skipped)

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes)) as executor:
            futures = []
            for node in nodes:
                cam_name = node.camera.m_userId
                save_dir = os.path.join(output_root, cam_name)
                os.makedirs(save_dir, exist_ok=True)
                save_path = os.path.join(save_dir, f"Position{position_index}_Step{step_index}{extension}")
                t0 = time.time()
                future = executor.submit(node.camera.snap_and_save, save_path)
                futures.append((cam_name, save_path, t0, future))

            successful = []
            failed = {}
            for cam_name, save_path, t0, future in futures:
                node = next(n for n in nodes if n.camera.m_userId == cam_name)
                try:
                    ret = future.result()
                except Exception as exc:
                    print(f"    [{cam_name}] save exception: {exc}")
                    node.isolate(
                        f"capture_exception: {exc}",
                        position=position_index,
                        step=step_index,
                    )
                    failed[cam_name] = node.status_summary()
                    continue

                t1 = time.time()
                if ret == IMV_OK:
                    print(f"    [{cam_name}] save ok ({t1-t0:.2f}s) -> {save_path}")
                    successful.append(cam_name)
                else:
                    print(f"    [{cam_name}] save failed, code: {ret}")
                    node.isolate(
                        f"capture_failed:{ret}",
                        position=position_index,
                        step=step_index,
                    )
                    failed[cam_name] = node.status_summary()

        return self._build_summary(successful, failed, skipped)

    def parallel_snap_all(self, save_dir_base):
        os.makedirs(save_dir_base, exist_ok=True)
        print(" 📸 正在并发触发拍摄并写入磁盘...")

        nodes = list(self.nodes)
        if not nodes:
            print(" ⚠️ 没有可拍摄的相机节点。")
            return

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes)) as executor:
            futures = []
            for node in nodes:
                cam_name = node.camera.m_userId
                save_path = os.path.join(save_dir_base, f"{cam_name}.bmp")
                t0 = time.time()
                future = executor.submit(node.camera.snap_and_save, save_path)
                futures.append((cam_name, save_path, t0, future))

            for cam_name, save_path, t0, future in futures:
                try:
                    ret = future.result()
                except Exception as exc:
                    print(f"    ❌ [{cam_name}] 保存异常: {exc}")
                    continue

                t1 = time.time()
                if ret == IMV_OK:
                    print(f"    ✅ [{cam_name}] 保存成功 (耗时: {t1-t0:.2f}s) -> {save_path}")
                else:
                    print(f"    ❌ [{cam_name}] 保存失败！错误码: {ret}")
