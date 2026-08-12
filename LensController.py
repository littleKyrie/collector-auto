import serial
import struct
import time

LENS_BAUDRATE = 115200
LENS_ENCODER_RES = 32768
LENS_SPEED_RPM = 200

LENS_SILENT_COMMAND = '016A030000000000000000000000'
LENS_CONFIG_COMMAND = '0164020000000000000000000000'
LENS_POWER_ON_HOMING_COMMAND = '0164060000000000000000000000'
LENS_RETURN_HOME_COMMAND = '0164070000000000000000000000'
LENS_STATUS_COMMAND = '0165000000000000000000000000'
LENS_SET_ZERO_COMMAND = '0164000000000000000000000000'
LENS_BOUNDARY_ZERO = 0xF5
LENS_BOUNDARY_FAR = 0x0B
LENS_BOUNDARY_STATUSES = {LENS_BOUNDARY_ZERO, LENS_BOUNDARY_FAR}

# 回零/移动碰壁容差：当电机触碰物理边界，但内部角度与目标角度之差
# 在此范围内时，视为已到位（以实测角度为准），不再报错。
BOUNDARY_ANGLE_TOLERANCE = 5.0  # 度

# full_shot 回零目标角度容差；允许机械零点附近存在少量负值和回差。
HOME_ANGLE_TOLERANCE = 5.0  # 度

# full_shot 普通绝对角度移动的到位容差；应比回零容差严格。
MOVE_ANGLE_TOLERANCE = 2.0  # 度

# full_shot 连续角度稳定所需时间；现场联调时可按电机响应调整。
POSITION_STABLE_DURATION = 1.0  # 秒

# full_shot 稳定窗口内允许的最大角度极差。
POSITION_STABLE_ANGLE_SPAN = 0.5  # 度

# full_shot 状态轮询间隔。
POSITION_POLL_INTERVAL = 0.5  # 秒

# full_shot 回零的正常等待时间与单次尝试绝对截止时间。
HOME_NORMAL_TIMEOUT = 10.0  # 秒
HOME_HARD_TIMEOUT = 15.0  # 秒

# full_shot 普通移动的正常等待时间与绝对截止时间。
MOVE_NORMAL_TIMEOUT = 4.0  # 秒
MOVE_HARD_TIMEOUT = 8.0  # 秒

# full_shot 回零失败后，厂商“上电找 0 -> 点击回 0”恢复次数。
HOME_RECOVERY_MAX_ATTEMPTS = 1

# 厂商“上电找 0”指令后的静默等待时间。
POWER_ON_HOMING_WAIT = 10.0  # 秒

WAIT_POLICY_LEGACY = 'legacy'
WAIT_POLICY_FULL_SHOT = 'full_shot'
VALID_WAIT_POLICIES = {WAIT_POLICY_LEGACY, WAIT_POLICY_FULL_SHOT}

COORDINATE_MODE_SOFTWARE = 'software'
COORDINATE_MODE_HARDWARE = 'hardware'
VALID_COORDINATE_MODES = {COORDINATE_MODE_SOFTWARE, COORDINATE_MODE_HARDWARE}


class LensController:
    def __init__(
        self,
        port,
        baudrate=LENS_BAUDRATE,
        timeout=0.5,
        encoder_res=LENS_ENCODER_RES,
        coordinate_mode=COORDINATE_MODE_SOFTWARE,
    ):
        if coordinate_mode not in VALID_COORDINATE_MODES:
            raise ValueError(f"Unsupported lens coordinate mode: {coordinate_mode}")

        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.encoder_res = encoder_res
        self.coordinate_mode = coordinate_mode
        self.serial = None
        self.camera_name = None
        self.current_angle = 0.0
        self.last_measured_angle = None
        self.full_shot_position_valid = False
        self.full_shot_position_quality = "unknown"
        self.full_shot_last_wait_result = None
        self.full_shot_last_motion_result = None

    def open(self):
        try:
            self.serial = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
            print(f"✅ [{self.port}] 镜头串口打开成功。坐标模式: {self.coordinate_mode}")
            return True
        except Exception as e:
            print(f"❌ [{self.port}] 打开串口失败: {e}")
            return False

    def close(self):
        if self.serial and self.serial.is_open:
            self.serial.close()

    def _calc_crc16(self, data: bytes) -> bytes:
        crc = 0xFFFF
        for pos in data:
            crc ^= pos
            for _ in range(8):
                if (crc & 1) != 0:
                    crc >>= 1
                    crc ^= 0xA001
                else:
                    crc >>= 1
        return struct.pack('<H', crc)

    def send_command(self, hex_str_without_crc, wait_time=0.2):
        if not self.serial or not self.serial.is_open:
            return False
        try:
            self.serial.reset_input_buffer()
            cmd = bytes.fromhex(hex_str_without_crc)
            full_cmd = cmd + self._calc_crc16(cmd)
            self.serial.write(full_cmd)
            time.sleep(wait_time)
            return True
        except Exception as e:
            print(f"❌ [{self.port}] 指令发送异常: {e}")
            return False

    def set_current_position_as_hardware_zero(self):
        """将当前位置设置为电机内部 0 点。调用点默认保持注释，现场调试时手动启用。"""
        return self.send_command(LENS_SET_ZERO_COMMAND, wait_time=0.2)

    def _maybe_set_hardware_zero(self):
        if self.coordinate_mode != COORDINATE_MODE_HARDWARE:
            return True

        # 调试用途：归 0 后将当前位置写成电机内部 0 点。
        # return self.set_current_position_as_hardware_zero()
        return True

    def _debug_log_internal_position_after_home(self):
        # 调试用途：验证点击回 0 后，电机内部角度是否会自动归零。
        # status, real_angle = self.read_motor_status_and_position()
        # print(f"[{self.port}] 回 0 后内部状态={status}, 内部角度={real_angle}°")
        pass

    def _set_software_zero(self):
        self.current_angle = 0.0

    @staticmethod
    def _format_status(status):
        if isinstance(status, int) and status >= 0:
            return f"0x{status:02X} ({status})"
        return str(status)

    def _device_label(self):
        if self.camera_name is None:
            return self.port
        return f"相机{self.camera_name}/{self.port}"

    def _store_full_shot_wait_result(self, **values):
        result = {
            "ok": False,
            "reason": "unknown",
            "operation": None,
            "status": -1,
            "real_angle": None,
            "expected_angle": None,
            "deviation": None,
            "position_quality": "unknown",
            "stable_duration": 0.0,
            "stable_span": None,
            "elapsed": 0.0,
            "boundary": None,
        }
        result.update(values)
        self.full_shot_last_wait_result = result
        return result

    def _wait_until_stopped(self, timeout_s=10.0, interval_s=0.5,
                            expected_angle=None, angle_tolerance=None,
                            wait_policy=WAIT_POLICY_LEGACY,
                            normal_timeout_s=None, hard_timeout_s=None,
                            operation=None, motion_direction=None):
        if wait_policy not in VALID_WAIT_POLICIES:
            raise ValueError(f"Unsupported wait policy: {wait_policy}")

        if wait_policy == WAIT_POLICY_FULL_SHOT:
            return self._wait_until_stopped_for_full_shot(
                expected_angle=expected_angle,
                angle_tolerance=angle_tolerance,
                operation=operation,
                normal_timeout_s=normal_timeout_s if normal_timeout_s is not None else timeout_s,
                hard_timeout_s=hard_timeout_s if hard_timeout_s is not None else timeout_s,
                interval_s=interval_s,
                motion_direction=motion_direction,
            )

        # legacy 分支供 focus_calibration 等既有调用使用，保持原有行为。
        start_t = time.time()
        last_status = -1
        last_angle = None

        while time.time() - start_t < timeout_s:
            status, real_angle = self.read_motor_status_and_position()
            last_status = status
            last_angle = real_angle

            if status == 0x00:
                return True, status, real_angle

            if status in (0x0B, 0xF5):
                boundary_name = "零点边界" if status == 0xF5 else "远端边界"
                print(f"⚠️ [{self.port}] 电机触碰物理{boundary_name}。状态=0x{status:02X}, 内部角度={real_angle}°")

                if expected_angle is not None and real_angle is not None and angle_tolerance is not None:
                    deviation = abs(real_angle - expected_angle)
                    if deviation <= angle_tolerance:
                        print(f"✅ [{self.port}] 碰壁但内部角度在容差内 "
                              f"(实测={real_angle:.2f}°, 期望={expected_angle:.2f}°, "
                              f"偏差={deviation:.2f}° <= {angle_tolerance}°)，视为到位。")
                        return True, status, real_angle

                return False, status, real_angle

            if status in (0x01, 0xFF):
                print(f"      ...电机运行中... 内部角度: {real_angle}°", end='\r')

            time.sleep(interval_s)

        return False, last_status, last_angle

    def _wait_until_stopped_for_full_shot(
        self,
        expected_angle,
        angle_tolerance,
        operation,
        normal_timeout_s,
        hard_timeout_s,
        interval_s=POSITION_POLL_INTERVAL,
        motion_direction=None,
    ):
        if operation not in ("home", "absolute_move"):
            raise ValueError(f"Unsupported full_shot operation: {operation}")
        if expected_angle is None or angle_tolerance is None:
            raise ValueError("full_shot wait requires expected_angle and angle_tolerance")
        if hard_timeout_s < normal_timeout_s:
            raise ValueError("hard_timeout_s must be >= normal_timeout_s")

        start_t = time.monotonic()
        last_status = -1
        last_angle = None
        last_logged_status = None
        candidate_started_at = None
        candidate_angles = []
        normal_timeout_logged = False

        while True:
            now = time.monotonic()
            elapsed = now - start_t
            if elapsed >= hard_timeout_s:
                break

            status, real_angle = self.read_motor_status_and_position()
            last_status = status
            last_angle = real_angle
            if real_angle is not None:
                self.last_measured_angle = real_angle

            deviation = (
                abs(real_angle - expected_angle)
                if real_angle is not None else None
            )
            within_tolerance = (
                deviation is not None and deviation <= angle_tolerance
            )
            status_text = self._format_status(status)

            if status != last_logged_status:
                print(
                    f"      [{self._device_label()}] {operation} 状态变化: status={status_text}, "
                    f"内部角度={real_angle}°, 目标={expected_angle:.2f}°, "
                    f"偏差={deviation if deviation is not None else 'N/A'}°"
                )
                last_logged_status = status

            if status == 0x00:
                if within_tolerance:
                    self._store_full_shot_wait_result(
                        ok=True,
                        reason="confirmed_stopped_at_target",
                        operation=operation,
                        status=status,
                        real_angle=real_angle,
                        expected_angle=expected_angle,
                        deviation=deviation,
                        position_quality="confirmed",
                        elapsed=elapsed,
                    )
                    return True, status, real_angle

                self._store_full_shot_wait_result(
                    reason="stopped_position_mismatch",
                    operation=operation,
                    status=status,
                    real_angle=real_angle,
                    expected_angle=expected_angle,
                    deviation=deviation,
                    elapsed=elapsed,
                )
                print(
                    f"❌ [{self._device_label()}] 电机已停稳但位置不符: status={status_text}, "
                    f"实测={real_angle}°, 目标={expected_angle:.2f}°, "
                    f"容差={angle_tolerance:.2f}°"
                )
                return False, status, real_angle

            if status in LENS_BOUNDARY_STATUSES:
                boundary = "zero" if status == LENS_BOUNDARY_ZERO else "far"
                if operation == "home":
                    boundary_matches_operation = status == LENS_BOUNDARY_ZERO
                elif motion_direction is None or motion_direction == 0:
                    boundary_matches_operation = False
                elif status == LENS_BOUNDARY_ZERO:
                    boundary_matches_operation = motion_direction < 0
                else:
                    boundary_matches_operation = motion_direction > 0
                if within_tolerance and boundary_matches_operation:
                    reason = (
                        "confirmed_zero_boundary"
                        if boundary == "zero" else "confirmed_far_boundary"
                    )
                    self._store_full_shot_wait_result(
                        ok=True,
                        reason=reason,
                        operation=operation,
                        status=status,
                        real_angle=real_angle,
                        expected_angle=expected_angle,
                        deviation=deviation,
                        position_quality="confirmed",
                        elapsed=elapsed,
                        boundary=boundary,
                        motion_direction=motion_direction,
                    )
                    print(
                        f"✅ [{self._device_label()}] 物理{boundary}边界与目标一致: "
                        f"status={status_text}, 实测={real_angle:.2f}°, "
                        f"偏差={deviation:.2f}°"
                    )
                    return True, status, real_angle

                self._store_full_shot_wait_result(
                    reason="boundary_position_mismatch",
                    operation=operation,
                    status=status,
                    real_angle=real_angle,
                    expected_angle=expected_angle,
                    deviation=deviation,
                    elapsed=elapsed,
                    boundary=boundary,
                    motion_direction=motion_direction,
                )
                print(
                    f"❌ [{self._device_label()}] 物理边界与本次目标/方向不符: "
                    f"status={status_text}, boundary={boundary}, "
                    f"motion_direction={motion_direction}, 实测={real_angle}°, "
                    f"目标={expected_angle:.2f}°"
                )
                return False, status, real_angle

            if status in (0x01, 0xFF) and within_tolerance:
                if candidate_started_at is None:
                    candidate_started_at = now
                    candidate_angles = [real_angle]
                    print(
                        f"⚠️ [{self._device_label()}] status={status_text} 但角度已进入目标容差，"
                        f"开始稳定候选: 实测={real_angle:.2f}°, 偏差={deviation:.2f}°"
                    )
                else:
                    candidate_angles.append(real_angle)

                stable_duration = now - candidate_started_at
                stable_span = max(candidate_angles) - min(candidate_angles)
                if stable_span > POSITION_STABLE_ANGLE_SPAN:
                    print(
                        f"⚠️ [{self._device_label()}] 稳定候选角度极差超限: "
                        f"{stable_span:.2f}° > {POSITION_STABLE_ANGLE_SPAN:.2f}°；"
                        f"从当前读数重新开始观察。"
                    )
                    candidate_started_at = now
                    candidate_angles = [real_angle]
                    stable_duration = 0.0
                    stable_span = 0.0
                if (
                    stable_duration >= POSITION_STABLE_DURATION
                    and stable_span <= POSITION_STABLE_ANGLE_SPAN
                ):
                    self._store_full_shot_wait_result(
                        ok=True,
                        reason="inferred_stable_at_target",
                        operation=operation,
                        status=status,
                        real_angle=real_angle,
                        expected_angle=expected_angle,
                        deviation=deviation,
                        position_quality="inferred",
                        stable_duration=stable_duration,
                        stable_span=stable_span,
                        elapsed=elapsed,
                        boundary="zero" if operation == "home" else None,
                    )
                    print(
                        f"✅ [{self._device_label()}] status={status_text} 目标附近连续稳定 "
                        f"{stable_duration:.2f}s，角度极差={stable_span:.2f}°；"
                        f"推断已到位。"
                    )
                    return True, status, real_angle
            elif candidate_started_at is not None:
                stable_span = (
                    max(candidate_angles) - min(candidate_angles)
                    if candidate_angles else None
                )
                print(
                    f"⚠️ [{self._device_label()}] 取消稳定候选: status={status_text}, "
                    f"内部角度={real_angle}°, 已观测极差={stable_span}°"
                )
                candidate_started_at = None
                candidate_angles = []

            if elapsed >= normal_timeout_s and not normal_timeout_logged:
                normal_timeout_logged = True
                print(
                    f"⚠️ [{self._device_label()}] 已达到正常等待时间 {normal_timeout_s:.2f}s，"
                    f"继续观察至硬超时 {hard_timeout_s:.2f}s。"
                )

            time.sleep(interval_s)

        stable_duration = (
            time.monotonic() - candidate_started_at
            if candidate_started_at is not None else 0.0
        )
        stable_span = (
            max(candidate_angles) - min(candidate_angles)
            if candidate_angles else None
        )
        if last_status == -1:
            reason = "serial_read_failed"
        elif candidate_started_at is not None:
            reason = "timeout_unstable_at_target"
        elif last_status in (0x01, 0xFF):
            reason = "timeout_still_moving"
        else:
            reason = "unexpected_status"

        elapsed = time.monotonic() - start_t
        deviation = (
            abs(last_angle - expected_angle)
            if last_angle is not None else None
        )
        self._store_full_shot_wait_result(
            reason=reason,
            operation=operation,
            status=last_status,
            real_angle=last_angle,
            expected_angle=expected_angle,
            deviation=deviation,
            stable_duration=stable_duration,
            stable_span=stable_span,
            elapsed=elapsed,
        )
        print(
            f"❌ [{self._device_label()}] full_shot 等待失败: reason={reason}, "
            f"status={self._format_status(last_status)}, 实测={last_angle}°, "
            f"目标={expected_angle:.2f}°, elapsed={elapsed:.2f}s"
        )
        return False, last_status, last_angle

    def _log_home_internal_angle(self, real_angle):
        if real_angle is None:
            print(f"⚠️ [{self.port}] 电机已停稳，但未读取到内部角度；软件坐标仍设置为 0.0°")
            return

        if abs(real_angle) < 1.0:
            print(f"✅ [{self.port}] 回 0 后内部角度接近 0: {real_angle:.2f}°")
        else:
            print(f"⚠️ [{self.port}] 电机已停稳，但内部角度未归零: {real_angle:.2f}°；软件坐标仍设置为 0.0°")

    def mark_full_shot_initialized(self):
        """由 full_shot 初始化上层在 initialize_lens 成功后调用。"""
        self.last_measured_angle = self.current_angle
        self.full_shot_position_valid = True
        self.full_shot_position_quality = "confirmed"
        self.full_shot_last_motion_result = {
            "ok": True,
            "reason": "initialized",
            "status": 0x00,
            "real_angle": self.current_angle,
            "position_quality": "confirmed",
            "recovered": False,
        }

    def _update_full_shot_position(self, real_angle, quality):
        if real_angle is None:
            self.full_shot_position_valid = False
            self.full_shot_position_quality = "unknown"
            return
        self.last_measured_angle = real_angle
        self.current_angle = real_angle
        self.full_shot_position_valid = True
        self.full_shot_position_quality = quality

    def _invalidate_full_shot_position(self, real_angle=None):
        if real_angle is not None:
            self.last_measured_angle = real_angle
        self.full_shot_position_valid = False
        self.full_shot_position_quality = "unknown"

    def initialize_lens(self):
        if not self.serial or not self.serial.is_open:
            print(f"❌ [{self.port}] 严重错误: 串口未打开！")
            return False

        print(f"🔄 [{self.port}] 正在配置串口静默...")
        self.send_command(LENS_SILENT_COMMAND, wait_time=0.1)
        self.send_command(LENS_CONFIG_COMMAND, wait_time=0.1)

        print(f"📍 [{self.port}] 正在下发【上电找0】指令")
        self.send_command(LENS_POWER_ON_HOMING_COMMAND, wait_time=1.0)

        print(f"⏳ [{self.port}] 正在静默等待 10 秒，供硬件传感器完成标定...")
        time.sleep(10.0)

        print(f"🔙 [{self.port}] 正在下发【点击回0】指令，驱动镜头归位...")
        self.send_command(LENS_RETURN_HOME_COMMAND, wait_time=1.0)

        print(f"⏳ [{self.port}] 正在静默等待 10 秒，供镜头完成物理退回动作...")
        time.sleep(10.0)

        # 调试用途：归 0 后将当前位置写成电机内部 0 点。
        # self.set_current_position_as_hardware_zero()

        self._debug_log_internal_position_after_home()

        print(f"🔍 [{self.port}] 开始轮询检查电机是否停稳...")
        stopped, status, real_angle = self._wait_until_stopped(timeout_s=2.0, interval_s=0.5)
        if not stopped:
            print(f"❌ [{self.port}] 自动寻零失败！状态={status}, 内部角度={real_angle}°")
            return False

        if self.coordinate_mode == COORDINATE_MODE_SOFTWARE:
            self._log_home_internal_angle(real_angle)
            self._set_software_zero()
            print(f"🎉 [{self.port}] 寻零完成，软件坐标已设置为 0.0°。")
            return True

        if not self._maybe_set_hardware_zero():
            return False

        status, real_angle = self.read_motor_status_and_position()
        if status == 0x00 and real_angle is not None and abs(real_angle) < 1.0:
            self.current_angle = real_angle
            print(f"🎉 [{self.port}] 硬件坐标已归零。内部角度: {real_angle:.2f}°")
            return True

        print(f"❌ [{self.port}] 硬件坐标模式归零失败！状态={status}, 内部角度={real_angle}°")
        return False

    def return_to_home(self, wait_policy=WAIT_POLICY_LEGACY):
        """调用官方【点击回0】指令；拍摄流程中的回 0，不用于关机收缩。"""
        if wait_policy == WAIT_POLICY_FULL_SHOT:
            return self._return_to_home_for_full_shot()
        if wait_policy != WAIT_POLICY_LEGACY:
            raise ValueError(f"Unsupported wait policy: {wait_policy}")

        if not self.serial or not self.serial.is_open:
            return False

        print(f"🔙 [{self.port}] 正在执行官方【点击回0】指令...")
        self.send_command(LENS_RETURN_HOME_COMMAND, wait_time=0.5)

        stopped, status, real_angle = self._wait_until_stopped(
            timeout_s=10.0, interval_s=0.5,
            expected_angle=0.0, angle_tolerance=BOUNDARY_ANGLE_TOLERANCE,
        )
        if not stopped:
            print(f"❌ [{self.port}] 回 0 超时或异常！状态={status}, 最后内部角度={real_angle}°，软件角度={self.current_angle:.2f}°")
            return False

        # 调试用途：归 0 后将当前位置写成电机内部 0 点。
        # self.set_current_position_as_hardware_zero()

        self._debug_log_internal_position_after_home()

        if self.coordinate_mode == COORDINATE_MODE_SOFTWARE:
            self._log_home_internal_angle(real_angle)
            if real_angle is not None:
                self.current_angle = real_angle
                print(f"🎉 [{self.port}] 寻零完成，软件坐标已校准为 {real_angle:.2f}°。")
            else:
                self._set_software_zero()
                print(f"🎉 [{self.port}] 寻零完成，软件坐标已设置为 0.0°。")
            return True

        if not self._maybe_set_hardware_zero():
            return False

        status, real_angle = self.read_motor_status_and_position()
        if status == 0x00 and real_angle is not None and abs(real_angle) < 1.0:
            self._set_software_zero()
            print(f"✅ [{self.port}] 硬件坐标回 0 成功。内部角度: {real_angle:.2f}°")
            return True

        print(f"❌ [{self.port}] 硬件坐标回 0 后角度异常！状态={status}, 内部角度={real_angle}°")
        return False

    def _return_to_home_for_full_shot(self):
        if not self.serial or not self.serial.is_open:
            self._invalidate_full_shot_position()
            self.full_shot_last_motion_result = {
                "ok": False,
                "reason": "serial_not_open",
                "operation": "home",
                "recovered": False,
            }
            return False

        total_attempts = 1 + HOME_RECOVERY_MAX_ATTEMPTS
        first_failure = None

        for attempt_index in range(total_attempts):
            recovered = attempt_index > 0
            attempt_number = attempt_index + 1

            if recovered:
                print(
                    f"⚠️ [{self._device_label()}] 首次回零未成功，执行厂商恢复 "
                    f"{attempt_index}/{HOME_RECOVERY_MAX_ATTEMPTS}: 上电找0 -> 点击回0。"
                )
                if not self.send_command(LENS_POWER_ON_HOMING_COMMAND, wait_time=0.5):
                    self._invalidate_full_shot_position()
                    self.full_shot_last_motion_result = {
                        "ok": False,
                        "reason": "power_on_homing_command_failed",
                        "operation": "home",
                        "attempt": attempt_number,
                        "recovered": False,
                        "first_failure": first_failure,
                    }
                    return False
                print(
                    f"⏳ [{self._device_label()}] 厂商恢复：等待 {POWER_ON_HOMING_WAIT:.2f}s "
                    f"供上电找0完成。"
                )
                time.sleep(POWER_ON_HOMING_WAIT)

            print(
                f"🔙 [{self._device_label()}] full_shot 回零尝试 {attempt_number}/{total_attempts}，"
                f"正在执行官方【点击回0】指令..."
            )
            if not self.send_command(LENS_RETURN_HOME_COMMAND, wait_time=0.5):
                wait_result = {
                    "ok": False,
                    "reason": "return_home_command_failed",
                    "operation": "home",
                    "status": -1,
                    "real_angle": None,
                }
                self.full_shot_last_wait_result = wait_result
                stopped, status, real_angle = False, -1, None
            else:
                stopped, status, real_angle = self._wait_until_stopped(
                    timeout_s=HOME_NORMAL_TIMEOUT,
                    interval_s=POSITION_POLL_INTERVAL,
                    expected_angle=0.0,
                    angle_tolerance=HOME_ANGLE_TOLERANCE,
                    wait_policy=WAIT_POLICY_FULL_SHOT,
                    normal_timeout_s=HOME_NORMAL_TIMEOUT,
                    hard_timeout_s=HOME_HARD_TIMEOUT,
                    operation="home",
                )
                wait_result = dict(self.full_shot_last_wait_result or {})

            if stopped:
                quality = wait_result.get("position_quality", "confirmed")
                self._update_full_shot_position(real_angle, quality)
                motion_result = dict(wait_result)
                motion_result.update({
                    "ok": True,
                    "operation": "home",
                    "attempt": attempt_number,
                    "recovered": recovered,
                    "first_failure": first_failure,
                })
                self.full_shot_last_motion_result = motion_result
                print(
                    f"🎉 [{self._device_label()}] full_shot 回零成功: "
                    f"status={self._format_status(status)}, 实测={real_angle}°, "
                    f"position_quality={quality}, recovered={recovered}。"
                )
                return True

            self._invalidate_full_shot_position(real_angle)
            if first_failure is None:
                first_failure = wait_result
            print(
                f"❌ [{self._device_label()}] full_shot 回零尝试 {attempt_number}/{total_attempts} 失败: "
                f"reason={wait_result.get('reason')}, "
                f"status={self._format_status(status)}, 实测={real_angle}°。"
            )

        final_result = dict(self.full_shot_last_wait_result or {})
        final_result.update({
            "ok": False,
            "operation": "home",
            "reason": "home_recovery_failed",
            "attempt": total_attempts,
            "recovered": False,
            "first_failure": first_failure,
        })
        self.full_shot_last_motion_result = final_result
        print(
            f"❌ [{self._device_label()}] 厂商恢复后仍无法确认回零，镜头应由上层隔离；"
            f"最后状态={self._format_status(final_result.get('status', -1))}, "
            f"最后角度={final_result.get('real_angle')}°。"
        )
        return False

    def read_motor_status_and_position(self):
        if not self.serial or not self.serial.is_open:
            return -1, None

        self.serial.reset_input_buffer()
        cmd = bytes.fromhex(LENS_STATUS_COMMAND)
        self.serial.write(cmd + self._calc_crc16(cmd))

        buffer = b''
        base_sleep = 0.01
        max_retries = 20

        for attempt in range(max_retries):
            time.sleep(base_sleep)

            if self.serial.in_waiting > 0:
                buffer += self.serial.read(self.serial.in_waiting)

                search_end = len(buffer)
                while True:
                    idx = buffer.rfind(b'\x01\x65', 0, search_end)
                    if idx == -1:
                        break

                    if idx + 16 <= len(buffer):
                        frame = buffer[idx:idx + 16]
                        if self._calc_crc16(frame[:14]) == frame[14:16]:
                            status = frame[3]
                            raw_pulses = struct.unpack('>i', frame[8:12])[0]
                            real_angle = (raw_pulses / self.encoder_res) * 360.0
                            return status, real_angle

                    search_end = idx

        return -1, None

    def retract_lens_for_shutdown(self):
        """关机前收缩镜头到底端，不执行点击回0。"""
        print(f"🔽 [{self.port}] 关机前执行【上电找0】收缩镜头...")
        # 备选方案：如需改用正向 3000° 收缩，可在现场调试后替换为相对运动指令。
        ok = self.send_command(LENS_POWER_ON_HOMING_COMMAND, wait_time=0.5)
        print(f"⏳ [{self.port}] 等待 5 秒，供镜头收缩到底端...")
        time.sleep(5.0)
        return ok

    def shutdown_lens(self):
        if not self.serial or not self.serial.is_open:
            return
        print(f"🛑 [{self.port}] 收到关机信号，正在收缩镜头到底端...")
        self.retract_lens_for_shutdown()
        self.close()

    def move_to_absolute_angle(self, target_angle_deg, speed_rpm=LENS_SPEED_RPM,
                               wait_policy=WAIT_POLICY_LEGACY):
        if wait_policy not in VALID_WAIT_POLICIES:
            raise ValueError(f"Unsupported wait policy: {wait_policy}")
        if not self.serial or not self.serial.is_open:
            if wait_policy == WAIT_POLICY_FULL_SHOT:
                self._invalidate_full_shot_position()
                self.full_shot_last_motion_result = {
                    "ok": False,
                    "reason": "serial_not_open",
                    "operation": "absolute_move",
                    "target_angle": target_angle_deg,
                }
            return False

        if wait_policy == WAIT_POLICY_FULL_SHOT:
            print(
                f"🎯 [{self._device_label()}] full_shot 绝对移动请求: "
                f"目标={target_angle_deg:.2f}°, 当前软件角度={self.current_angle:.2f}°, "
                f"position_valid={self.full_shot_position_valid}, "
                f"position_quality={self.full_shot_position_quality}。"
            )

        if abs(target_angle_deg) <= 0.01:
            return self.return_to_home(wait_policy=wait_policy)

        if target_angle_deg < 0.0:
            print(f"⚠️ [{self.port}] 警告: 目标角度为负数(超出软件0点)，已强制限制为 0.0°。")
            target_angle_deg = 0.0

        delta_angle = target_angle_deg - self.current_angle
        if abs(delta_angle) < 0.1:
            if wait_policy == WAIT_POLICY_LEGACY:
                return True

            if self.full_shot_position_valid:
                status, real_angle = self.read_motor_status_and_position()
                self.last_measured_angle = real_angle
                deviation = (
                    abs(real_angle - target_angle_deg)
                    if real_angle is not None else None
                )
                if (
                    status == 0x00
                    and deviation is not None
                    and deviation <= MOVE_ANGLE_TOLERANCE
                ):
                    self._update_full_shot_position(real_angle, "confirmed")
                    self.full_shot_last_motion_result = {
                        "ok": True,
                        "reason": "verified_small_delta_skip",
                        "operation": "absolute_move",
                        "status": status,
                        "real_angle": real_angle,
                        "target_angle": target_angle_deg,
                        "deviation": deviation,
                        "position_quality": "confirmed",
                    }
                    print(
                        f"✅ [{self._device_label()}] 小位移跳过前复核通过: "
                        f"status={self._format_status(status)}, 实测={real_angle:.2f}°, "
                        f"目标={target_angle_deg:.2f}°。"
                    )
                    return True

            print(
                f"⚠️ [{self._device_label()}] 小位移快捷返回被拒绝："
                f"full_shot 软件坐标无效或复核不通过，"
                f"将按当前实测位置重新计算。"
            )
            status, measured_angle = self.read_motor_status_and_position()
            if measured_angle is None:
                self._invalidate_full_shot_position()
                self.full_shot_last_motion_result = {
                    "ok": False,
                    "reason": "position_resync_failed",
                    "operation": "absolute_move",
                    "status": status,
                    "real_angle": None,
                    "target_angle": target_angle_deg,
                }
                return False
            self.last_measured_angle = measured_angle
            self.current_angle = measured_angle
            delta_angle = target_angle_deg - measured_angle
            if abs(delta_angle) < 0.1:
                stopped, status, measured_angle = self._wait_until_stopped(
                    timeout_s=MOVE_NORMAL_TIMEOUT,
                    interval_s=POSITION_POLL_INTERVAL,
                    expected_angle=target_angle_deg,
                    angle_tolerance=MOVE_ANGLE_TOLERANCE,
                    wait_policy=WAIT_POLICY_FULL_SHOT,
                    normal_timeout_s=MOVE_NORMAL_TIMEOUT,
                    hard_timeout_s=MOVE_HARD_TIMEOUT,
                    operation="absolute_move",
                    motion_direction=0,
                )
                if stopped:
                    wait_result = dict(self.full_shot_last_wait_result or {})
                    quality = wait_result.get("position_quality", "inferred")
                    self._update_full_shot_position(measured_angle, quality)
                    wait_result.update({
                        "ok": True,
                        "reason": "resynced_small_delta_skip",
                        "operation": "absolute_move",
                        "target_angle": target_angle_deg,
                    })
                    self.full_shot_last_motion_result = wait_result
                    return True
                self._invalidate_full_shot_position(measured_angle)
                motion_result = dict(self.full_shot_last_wait_result or {})
                motion_result.update({
                    "ok": False,
                    "operation": "absolute_move",
                    "target_angle": target_angle_deg,
                })
                self.full_shot_last_motion_result = motion_result
                return False

        v_val = int(speed_rpm * self.encoder_res / 6000)
        angle_signed_val = int(delta_angle * self.encoder_res / 360)

        speed_bytes = struct.pack('>I', v_val)
        angle_signed_bytes = struct.pack('>i', angle_signed_val)

        header = bytes([0x01, 0x64, 0x01])
        tail = bytes([0x00, 0x00, 0x00])
        cmd_without_crc = header + speed_bytes + angle_signed_bytes + tail
        full_cmd = cmd_without_crc + self._calc_crc16(cmd_without_crc)

        hex_str = full_cmd.hex(' ').upper()
        print(f"原始发送报文: {hex_str}")
        print(f"      软件坐标: 当前={self.current_angle:.2f}°, 目标={target_angle_deg:.2f}°, 相对位移={delta_angle:.2f}°")

        try:
            self.serial.reset_input_buffer()
            self.serial.write(full_cmd)

            estimated_move_time = (abs(delta_angle) / 1000.0) * 0.87
            bulk_sleep_time = min(4.0, max(0.1, estimated_move_time - 0.1))

            print(f"      ...指令下发，执行大段静默等待: {bulk_sleep_time:.2f}s...")
            time.sleep(bulk_sleep_time)

            if wait_policy == WAIT_POLICY_FULL_SHOT:
                stopped, status, real_angle = self._wait_until_stopped(
                    timeout_s=MOVE_NORMAL_TIMEOUT,
                    interval_s=POSITION_POLL_INTERVAL,
                    expected_angle=target_angle_deg,
                    angle_tolerance=MOVE_ANGLE_TOLERANCE,
                    wait_policy=WAIT_POLICY_FULL_SHOT,
                    normal_timeout_s=MOVE_NORMAL_TIMEOUT,
                    hard_timeout_s=MOVE_HARD_TIMEOUT,
                    operation="absolute_move",
                    motion_direction=1 if delta_angle > 0 else -1,
                )
            else:
                stopped, status, real_angle = self._wait_until_stopped(
                    timeout_s=4.0, interval_s=0.2,
                    expected_angle=target_angle_deg,
                    angle_tolerance=BOUNDARY_ANGLE_TOLERANCE,
                )
            if not stopped:
                print(f"\n      ❌ [{self.port}] 主动查验超时或异常！状态={status}, 内部角度={real_angle}°，软件角度={self.current_angle:.2f}°")
                if wait_policy == WAIT_POLICY_FULL_SHOT:
                    self._invalidate_full_shot_position(real_angle)
                    motion_result = dict(self.full_shot_last_wait_result or {})
                    motion_result.update({
                        "ok": False,
                        "operation": "absolute_move",
                        "target_angle": target_angle_deg,
                    })
                    self.full_shot_last_motion_result = motion_result
                return False

            if self.coordinate_mode == COORDINATE_MODE_SOFTWARE:
                if real_angle is not None:
                    self.current_angle = real_angle
                    print(f"\n      ✅ [{self.port}] 电机停稳。内部角度={real_angle:.2f}°，软件坐标已校准。")
                else:
                    self.current_angle = target_angle_deg
                    print(f"\n      ✅ [{self.port}] 电机停稳。软件坐标更新为 {target_angle_deg:.2f}°")
                if wait_policy == WAIT_POLICY_FULL_SHOT:
                    wait_result = dict(self.full_shot_last_wait_result or {})
                    quality = wait_result.get("position_quality", "confirmed")
                    synced_angle = real_angle if real_angle is not None else target_angle_deg
                    self._update_full_shot_position(synced_angle, quality)
                    wait_result.update({
                        "ok": True,
                        "operation": "absolute_move",
                        "target_angle": target_angle_deg,
                    })
                    self.full_shot_last_motion_result = wait_result
                return True

            if real_angle is not None and abs(real_angle - target_angle_deg) < 5.0:
                print(f"\n      ✅ [{self.port}] 硬件坐标准确到位。内部角度: {real_angle:.2f}°")
                self.current_angle = real_angle
                if wait_policy == WAIT_POLICY_FULL_SHOT:
                    wait_result = dict(self.full_shot_last_wait_result or {})
                    quality = wait_result.get("position_quality", "confirmed")
                    self._update_full_shot_position(real_angle, quality)
                    wait_result.update({
                        "ok": True,
                        "operation": "absolute_move",
                        "target_angle": target_angle_deg,
                    })
                    self.full_shot_last_motion_result = wait_result
                return True

            print(f"\n      ❌ [{self.port}] 硬件坐标偏差超限！目标:{target_angle_deg}°, 内部角度:{real_angle}°")
            if wait_policy == WAIT_POLICY_FULL_SHOT:
                self._invalidate_full_shot_position(real_angle)
                self.full_shot_last_motion_result = {
                    "ok": False,
                    "reason": "hardware_position_mismatch",
                    "operation": "absolute_move",
                    "status": status,
                    "real_angle": real_angle,
                    "target_angle": target_angle_deg,
                }
            return False

        except Exception as e:
            print(f"\n[{self.port}] ❌ 串口异常: {e}")
            if wait_policy == WAIT_POLICY_FULL_SHOT:
                self._invalidate_full_shot_position()
                self.full_shot_last_motion_result = {
                    "ok": False,
                    "reason": "serial_exception",
                    "operation": "absolute_move",
                    "target_angle": target_angle_deg,
                    "error": str(e),
                }
            return False

    def move_relative_for_calibration(self, delta_angle_deg, speed_rpm=LENS_SPEED_RPM):
        """标定用相对移动。返回电机真实状态和内部绝对角度，不用软件坐标作为标定结果。"""
        if not self.serial or not self.serial.is_open:
            return {
                "ok": False,
                "status": -1,
                "real_angle": None,
                "boundary": False,
                "error": "serial_not_open",
                "requested_delta": delta_angle_deg,
            }

        if abs(delta_angle_deg) < 0.01:
            status, real_angle = self.read_motor_status_and_position()
            return {
                "ok": status == 0x00,
                "status": status,
                "real_angle": real_angle,
                "boundary": status in LENS_BOUNDARY_STATUSES,
                "error": None if status == 0x00 else "status_not_ready",
                "requested_delta": delta_angle_deg,
            }

        v_val = int(speed_rpm * self.encoder_res / 6000)
        angle_signed_val = int(delta_angle_deg * self.encoder_res / 360)

        speed_bytes = struct.pack('>I', v_val)
        angle_signed_bytes = struct.pack('>i', angle_signed_val)

        header = bytes([0x01, 0x64, 0x01])
        tail = bytes([0x00, 0x00, 0x00])
        cmd_without_crc = header + speed_bytes + angle_signed_bytes + tail
        full_cmd = cmd_without_crc + self._calc_crc16(cmd_without_crc)

        print(f"      标定相对移动: {delta_angle_deg:.2f}°")
        print(f"      原始发送报文: {full_cmd.hex(' ').upper()}")

        try:
            self.serial.reset_input_buffer()
            self.serial.write(full_cmd)

            estimated_move_time = (abs(delta_angle_deg) / 1000.0) * 0.87
            bulk_sleep_time = min(4.0, max(0.1, estimated_move_time - 0.1))
            time.sleep(bulk_sleep_time)

            stopped, status, real_angle = self._wait_until_stopped(timeout_s=4.0, interval_s=0.2)
            boundary = status in LENS_BOUNDARY_STATUSES

            if boundary:
                self.current_angle = real_angle if real_angle is not None else self.current_angle
                return {
                    "ok": True,
                    "status": status,
                    "real_angle": real_angle,
                    "boundary": True,
                    "error": None,
                    "requested_delta": delta_angle_deg,
                }

            if stopped and status == 0x00:
                self.current_angle = real_angle if real_angle is not None else self.current_angle + delta_angle_deg
                return {
                    "ok": True,
                    "status": status,
                    "real_angle": real_angle,
                    "boundary": False,
                    "error": None,
                    "requested_delta": delta_angle_deg,
                }

            return {
                "ok": False,
                "status": status,
                "real_angle": real_angle,
                "boundary": False,
                "error": "move_not_stopped",
                "requested_delta": delta_angle_deg,
            }

        except Exception as e:
            print(f"\n[{self.port}] ❌ 标定移动串口异常: {e}")
            return {
                "ok": False,
                "status": -1,
                "real_angle": None,
                "boundary": False,
                "error": str(e),
                "requested_delta": delta_angle_deg,
            }
