import serial
import struct
import time

LENS_BAUDRATE = 115200
LENS_ENCODER_RES = 32768
LENS_SPEED_RPM = 200

class LensController:
    def __init__(self, port, baudrate=LENS_BAUDRATE, timeout=0.5, encoder_res=LENS_ENCODER_RES):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.encoder_res = encoder_res
        self.serial = None
        self.current_angle = 0.0

    def open(self):
        try:
            self.serial = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
            print(f"✅ [{self.port}] 镜头串口打开成功。")
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
        if not self.serial or not self.serial.is_open: return False
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

    def initialize_lens(self):
        if not self.serial or not self.serial.is_open:
            print(f"❌ [{self.port}] 严重错误: 串口未打开！")
            return False

        print(f"🔄 [{self.port}] 正在配置串口静默...")
        self.send_command('016A030000000000000000000000', wait_time=0.1) 
        self.send_command('0164020000000000000000000000', wait_time=0.1) 
        
        print(f"📍 [{self.port}] 正在下发【上电找0】指令")
        self.serial.reset_input_buffer()
        self.send_command('0164060000000000000000000000', wait_time=0.5)
        
        print(f"⏳ [{self.port}] 正在静默等待 5 秒，供硬件传感器完成标定...")
        time.sleep(5.0) 

        print(f"🔙 [{self.port}] 正在下发【点击回0】指令，驱动镜头归位...")
        self.serial.reset_input_buffer()
        self.send_command('0164070000000000000000000000', wait_time=0.5)

        print(f"⏳ [{self.port}] 正在静默等待 4 秒，供镜头完成物理退回动作...")
        time.sleep(4.0)

        print(f"🔍 [{self.port}] 开始轮询检查归零是否达标...")
        homing_success = False
        max_retries = 2  # 最多重复查询 2 次
        
        for attempt in range(max_retries):
            status, real_angle = self.read_motor_status_and_position()
            
            # 只要电机平稳停止，且处于极微小的物理容差内
            if status == 0x00 and real_angle is not None:
                if abs(real_angle) < 1.0: 
                    homing_success = True
                    self.current_angle = real_angle
                    break
            
            print(f"      ...第 {attempt + 1} 次检查: 状态={status}, 角度={real_angle}°，等待重试...")
            time.sleep(0.5) 

        # print("\n--- 🛠️ 人工调试：归零后最终底层状态核验 ---")
        # self.serial.reset_input_buffer()
        # final_status, final_angle = self.read_motor_status_and_position()
        
        # if final_status != -1 and final_angle is not None:
        #     print(f"      最终底层状态码: {final_status}")
        #     print(f"      最终底层真实角度: {final_angle:.2f}°")
        # else:
        #     print("      ⚠️ 无法读取最终状态，串口可能无响应。")
        # print("------------------------------------------\n")

        if homing_success:
            print(f"🎉 [{self.port}] 硬件系统绝对寻零完毕！当前坐标系已标定为 0.0°。")
            return True
        else:
            print(f"❌ [{self.port}] 自动寻零失败！最终读数与 0 偏差过大或未停稳，请排查。")
            return False

    def return_to_home(self):
        """专门调用官方的【点击回0】指令"""
        if not self.serial or not self.serial.is_open: return False
        
        print(f"🔙 [{self.port}] 正在执行官方【点击回0】指令...")
        self.serial.reset_input_buffer()
        # 官方点击回0指令: 01 64 07 00 00 00 00 00 00 00 00 00 00 00
        self.send_command('0164070000000000000000000000', wait_time=0.5)
        
        start_t = time.time()
        while time.time() - start_t < 10.0:
            status, real_angle = self.read_motor_status_and_position()
            if status == 0x00 and real_angle is not None:
                if abs(real_angle) < 1.0:
                    print(f"✅ [{self.port}] 精准回到 0 点。真实角度: {real_angle:.2f}°")
                    self.current_angle = real_angle
                    return True
            time.sleep(0.5)
            
        print(f"❌ [{self.port}] 回 0 超时！最后已知角度: {self.current_angle:.2f}°")
        return False

    def read_motor_status_and_position(self):
        if not self.serial or not self.serial.is_open: return -1, None
        
        self.serial.reset_input_buffer() 
        cmd = bytes.fromhex('0165000000000000000000000000')
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
                        frame = buffer[idx : idx+16]
                        if self._calc_crc16(frame[:14]) == frame[14:16]:
                            status = frame[3]
                            raw_pulses = struct.unpack('>i', frame[8:12])[0]
                            real_angle = (raw_pulses / self.encoder_res) * 360.0
                            return status, real_angle
                            
                    search_end = idx 
                    
        return -1, None

    def shutdown_lens(self):
        if not self.serial or not self.serial.is_open: return
        print(f"🛑 [{self.port}] 收到关机信号，正在退回初始原点...")
        self.return_to_home()
        self.close()

    def move_to_absolute_angle(self, target_angle_deg, speed_rpm=LENS_SPEED_RPM):
        if not self.serial or not self.serial.is_open: return False

        # 【核心拦截】：如果目标角度是 0，直接移交给官方专属的【回0指令】
        if abs(target_angle_deg) <= 0.01:
            return self.return_to_home()

        # 【安全边界】：0 点在反向极限，所有的移动必须是正数（向外伸出）
        if target_angle_deg < 0.0:
            print(f"⚠️ [{self.port}] 警告: 目标角度为负数(超出物理0点)，已强制限制为 0.0°。")
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

        try:
            self.serial.reset_input_buffer()
            self.serial.write(full_cmd)
            
            estimated_move_time = (abs(delta_angle) / 1000.0) * 0.87
            bulk_sleep_time = min(4.0, max(0.1, estimated_move_time - 0.1))
            
            print(f"      ...指令下发，执行大段静默等待: {bulk_sleep_time:.2f}s...")
            time.sleep(bulk_sleep_time)

            self.serial.reset_input_buffer()
            
            check_interval = 0.2     
            max_retries = int(4.0 / check_interval) 
            
            for attempt in range(max_retries):
                status, real_angle = self.read_motor_status_and_position()
                
                if status == -1:
                    continue
                    
                if status == 0x00:
                    if abs(real_angle - target_angle_deg) < 5.0:
                        print(f"\n      ✅ [{self.port}] 精准到位。真实角度: {real_angle:.2f}°")
                    else:
                        print(f"\n      ⚠️ [{self.port}] 电机停转但有偏差！目标:{target_angle_deg}°, 实际:{real_angle:.2f}°")
                    self.current_angle = real_angle 
                    return True

                elif status in (0x0B, 0xF5):
                    print(f"\n      ⚠️ [{self.port}] 触碰物理边界！拦截坐标: {real_angle:.2f}°")
                    self.current_angle = real_angle
                    return True

                elif status in (0x01, 0xFF):
                    print(f"      ...惯性平滑减速中... 实时角度: {real_angle:.2f}°", end='\r')
                    time.sleep(check_interval)
                    
            print(f"\n      ❌ [{self.port}] 主动查验严重超时！最后已知角度: {self.current_angle:.2f}°")
            return False
            
        except Exception as e:
            print(f"\n[{self.port}] ❌ 串口异常: {e}")
            return False