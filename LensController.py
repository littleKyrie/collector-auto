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
        self.current_angle = 0.0

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

    def _wait_until_stopped(self, timeout_s=10.0, interval_s=0.5):
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
                print(f"⚠️ [{self.port}] 电机触碰物理边界。状态={status}, 内部角度={real_angle}°")
                return False, status, real_angle

            if status in (0x01, 0xFF):
                print(f"      ...电机运行中... 内部角度: {real_angle}°", end='\r')

            time.sleep(interval_s)

        return False, last_status, last_angle

    def _log_home_internal_angle(self, real_angle):
        if real_angle is None:
            print(f"⚠️ [{self.port}] 电机已停稳，但未读取到内部角度；软件坐标仍设置为 0.0°")
            return

        if abs(real_angle) < 1.0:
            print(f"✅ [{self.port}] 回 0 后内部角度接近 0: {real_angle:.2f}°")
        else:
            print(f"⚠️ [{self.port}] 电机已停稳，但内部角度未归零: {real_angle:.2f}°；软件坐标仍设置为 0.0°")

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

    def return_to_home(self):
        """调用官方【点击回0】指令；拍摄流程中的回 0，不用于关机收缩。"""
        if not self.serial or not self.serial.is_open:
            return False

        print(f"🔙 [{self.port}] 正在执行官方【点击回0】指令...")
        self.send_command(LENS_RETURN_HOME_COMMAND, wait_time=0.5)

        stopped, status, real_angle = self._wait_until_stopped(timeout_s=10.0, interval_s=0.5)
        if not stopped:
            print(f"❌ [{self.port}] 回 0 超时或异常！状态={status}, 最后内部角度={real_angle}°，软件角度={self.current_angle:.2f}°")
            return False

        # 调试用途：归 0 后将当前位置写成电机内部 0 点。
        # self.set_current_position_as_hardware_zero()

        self._debug_log_internal_position_after_home()

        if self.coordinate_mode == COORDINATE_MODE_SOFTWARE:
            self._log_home_internal_angle(real_angle)
            self._set_software_zero()
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

    def move_to_absolute_angle(self, target_angle_deg, speed_rpm=LENS_SPEED_RPM):
        if not self.serial or not self.serial.is_open:
            return False

        if abs(target_angle_deg) <= 0.01:
            return self.return_to_home()

        if target_angle_deg < 0.0:
            print(f"⚠️ [{self.port}] 警告: 目标角度为负数(超出软件0点)，已强制限制为 0.0°。")
            target_angle_deg = 0.0

        delta_angle = target_angle_deg - self.current_angle
        if abs(delta_angle) < 0.1:
            return True

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

            stopped, status, real_angle = self._wait_until_stopped(timeout_s=4.0, interval_s=0.2)
            if not stopped:
                print(f"\n      ❌ [{self.port}] 主动查验超时或异常！状态={status}, 内部角度={real_angle}°，软件角度={self.current_angle:.2f}°")
                return False

            if self.coordinate_mode == COORDINATE_MODE_SOFTWARE:
                if real_angle is not None:
                    print(f"\n      ✅ [{self.port}] 电机停稳。内部角度={real_angle:.2f}°，软件坐标更新为 {target_angle_deg:.2f}°")
                else:
                    print(f"\n      ✅ [{self.port}] 电机停稳。软件坐标更新为 {target_angle_deg:.2f}°")
                self.current_angle = target_angle_deg
                return True

            if real_angle is not None and abs(real_angle - target_angle_deg) < 5.0:
                print(f"\n      ✅ [{self.port}] 硬件坐标准确到位。内部角度: {real_angle:.2f}°")
                self.current_angle = real_angle
                return True

            print(f"\n      ❌ [{self.port}] 硬件坐标偏差超限！目标:{target_angle_deg}°, 内部角度:{real_angle}°")
            return False

        except Exception as e:
            print(f"\n[{self.port}] ❌ 串口异常: {e}")
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
            }

        if abs(delta_angle_deg) < 0.01:
            status, real_angle = self.read_motor_status_and_position()
            return {
                "ok": status == 0x00,
                "status": status,
                "real_angle": real_angle,
                "boundary": status in LENS_BOUNDARY_STATUSES,
                "error": None if status == 0x00 else "status_not_ready",
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
                }

            if stopped and status == 0x00:
                self.current_angle = real_angle if real_angle is not None else self.current_angle + delta_angle_deg
                return {
                    "ok": True,
                    "status": status,
                    "real_angle": real_angle,
                    "boundary": False,
                    "error": None,
                }

            return {
                "ok": False,
                "status": status,
                "real_angle": real_angle,
                "boundary": False,
                "error": "move_not_stopped",
            }

        except Exception as e:
            print(f"\n[{self.port}] ❌ 标定移动串口异常: {e}")
            return {
                "ok": False,
                "status": -1,
                "real_angle": None,
                "boundary": False,
                "error": str(e),
            }
