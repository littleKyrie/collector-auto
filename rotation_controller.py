"""
旋转控制模块
实现360度旋转控制功能
"""

import time
import logging
import threading
from modbus_client import ModbusClient

# 配置日志
logger = logging.getLogger(__name__)

# 寄存器地址定义（根据Excel文档）
# D寄存器（保持寄存器）
D_SET_AUTO_ACTION_POSITION = 300    # 设置间隔运行角度
D_SET_AUTO_ACTION_VELOCITY = 304    # 设置运行速度
D_SET_MODEL_NUMBER = 320            # 设置模式参数

# 新增寄存器地址（模式1触发控制）
D_REGISTER_POSITION = 324           # 机械轴角度位置
D_REGISTER_CONTINUE = 332           # 异常后继续运行模式，0-继续 1-回到起始位置 2-默认

# S寄存器（线圈）
S_SET_ENABLE_SET_VALUE = 57744      # 400+57344 确认值参数写入

# 线圈地址常量（模式1触发控制）
coilResume = 57746                  # 402+57344 暂停后重新执行（模式1使用）
coilReady = 57747                   # 403+57344 机械轴移动完成，等待拍照信号
coilMode = 57348                    # 模式切换，0-自动 1-手动
coilNoError = 57649                 # 屏蔽雷达告警信号
coilErrorOccur = 57356              # 读取该线圈，是否出现错误，0-有错误，1-无错误
coilRecover = 57496                 # 异常后复位处理，消除错误
coilMachineStart = 57494            # 启动机器

# 线圈值常量
coilValueFalse = 0x0000             # 线圈断开值
coilValueTrue = 0xFF00              # 线圈闭合值
coilResultFalse = 0                 # 读取结果：断开
coilResultTrue = 1                  # 读取结果：闭合

# 错误处理模式常量
errHandleModeContinue = 0           # 继续运行
errHandleModeInit = 1               # 恢复到初始位置
errHandleModeDefault = 2            # 默认

# 保留旧常量名以保持兼容性
COIL_VALUE_FALSE = coilValueFalse
COIL_VALUE_TRUE = coilValueTrue
COIL_RESULT_FALSE = coilResultFalse
COIL_RESULT_TRUE = coilResultTrue
ERR_HANDLE_MODE_CONTINUE = errHandleModeContinue
ERR_HANDLE_MODE_INIT = errHandleModeInit
ERR_HANDLE_MODE_DEFAULT = errHandleModeDefault

# 保留旧常量名以保持兼容性
S_COIL_RESUME = coilResume
S_COIL_READY = coilReady
S_COIL_MODE = coilMode
S_COIL_NO_ERROR = coilNoError
S_COIL_ERROR_OCCUR = coilErrorOccur
S_COIL_RECOVER = coilRecover
S_COIL_MACHINE_START = coilMachineStart

class RotationController:
    """旋转控制器类"""
    
    def __init__(self, modbus_client):
        """
        初始化旋转控制器
        
        Args:
            modbus_client: ModbusClient实例
        """
        self.client = modbus_client
        self.total_rotations = 0
        self.current_rotation = 0
        self.angle_per_step = 0
        self.speed = 0
        self.delay = 0
        self.progress_callback = None
        
    def validate_parameters(self, rotations, speed, delay):
        """
        验证参数有效性
        
        Args:
            rotations: 旋转次数
            speed: 旋转速度(°/s)
            delay: 延时时间(秒)
            
        Returns:
            tuple: (bool, str) 是否有效和错误信息
        """
        # 检查旋转次数
        if not isinstance(rotations, int) or rotations <= 0:
            return False, "旋转次数必须为正整数"
        
        # 检查速度范围
        if not (0 < speed <= 30):
            return False, "速度必须在0-30°/s范围内"
        
        # 检查延时
        if delay < 0:
            return False, "延时时间不能为负数"
        
        # 计算每次旋转角度
        angle_per_step = 360.0 / rotations
        
        # 检查单次角度是否超过INT范围限制（327.67°）
        if angle_per_step > 327.67:
            return False, f"单次旋转角度{angle_per_step:.2f}°超过最大限制范围（327.67°），请增加旋转次数"
        
        return True, ""
    
    def calculate_angle_per_step(self, rotations):
        """
        计算每次旋转角度
        
        Args:
            rotations: 旋转次数
            
        Returns:
            float: 每次旋转角度
        """
        return 360.0 / rotations
    
    def run_rotation_sequence(self, rotations, speed, delay, progress_callback=None, 
                              error_signal=True, error_continue_model=errHandleModeDefault):
        """
        执行完整旋转序列（模式1：间隔运行+触发）
        
        Args:
            rotations: 旋转次数
            speed: 旋转速度(°/s)
            delay: 延时时间(秒)
            progress_callback: 进度回调函数 (current, total)
            error_signal: 是否屏蔽红外感应信号 (默认True)
            error_continue_model: 异常处理模式 (0=继续, 1=复位, 2=默认)
            
        Returns:
            bool: 是否成功
        """
        error_thread = None
        stop_event = threading.Event()
        
        try:
            # 验证参数
            valid, error_msg = self.validate_parameters(rotations, speed, delay)
            if not valid:
                logger.error(f"参数验证失败: {error_msg}")
                return False
            
            # 保存参数
            self.total_rotations = rotations
            self.current_rotation = 0
            self.angle_per_step = self.calculate_angle_per_step(rotations)
            self.speed = speed
            self.delay = delay
            self.progress_callback = progress_callback
            
            logger.info(f"开始旋转序列（模式1）: {rotations}次, 每次{self.angle_per_step:.2f}°, "
                       f"速度{speed}°/s, 延时{delay}s")
            
            # 3.2 实现红外感应信号屏蔽控制（coilNoError）
            if error_signal:
                logger.info("屏蔽红外感应信号")
                if not self.client.write_coil(coilNoError, True):
                    logger.error("屏蔽红外感应信号失败")
                    return False
            
            # 3.3 实现模式1配置（D320=1）和参数写入（D300, D304）
            logger.info("配置模式1参数...")
            angle_value = int(self.angle_per_step * 100)
            speed_value = int(speed * 100)
            mode_value = 1  # 模式1：间隔运行+触发
            
            # 写入D300: 角度
            if not self.client.write_register(D_SET_AUTO_ACTION_POSITION, angle_value):
                logger.error("写入角度参数失败")
                return False
            
            # 写入D304: 速度
            if not self.client.write_register(D_SET_AUTO_ACTION_VELOCITY, speed_value):
                logger.error("写入速度参数失败")
                return False
            
            # 写入D320: 模式1
            if not self.client.write_register(D_SET_MODEL_NUMBER, mode_value):
                logger.error("写入模式参数失败")
                return False
            
            # 3.4 实现参数确认（coilEnsure）
            logger.info("确认参数写入...")
            if not self.client.write_coil(S_SET_ENABLE_SET_VALUE, True):
                logger.error("触发参数确认失败")
                return False
            time.sleep(0.2)
            self.client.write_coil(S_SET_ENABLE_SET_VALUE, False)
            
            # 3.5 实现位置检测与复位调用（reset_to_home）
            logger.info("检查并复位到起始位置...")
            if not self.reset_to_home():
                logger.error("复位到起始位置失败")
                return False
            
            # 3.6 实现手动模式启动调用（start_machine）
            logger.info("手动模式启动设备...")
            if not self.start_machine():
                logger.error("启动设备失败")
                return False
            
            # 3.7 启动异步错误检测线程（check_error）
            logger.info("启动异步错误检测线程...")
            error_thread = threading.Thread(
                target=self.check_error,
                args=(stop_event, error_continue_model)
            )
            error_thread.daemon = True
            error_thread.start()
            
            # 3.8 实现触发-等待-回调循环
            logger.info("开始触发-等待-回调循环...")
            for i in range(rotations):
                self.current_rotation = i + 1
                
                # 发送coilResume触发旋转
                logger.debug(f"发送coilResume信号（第{self.current_rotation}次）")
                if not self.client.write_coil(coilResume, True):
                    logger.error(f"发送coilResume失败")
                    return False
                time.sleep(0.1)
                self.client.write_coil(coilResume, False)
                
                # 等待coilReady机械轴就绪
                if not self.wait_for_ready():
                    logger.error(f"等待机械轴就绪超时（第{self.current_rotation}次）")
                    return False
                
                # 执行回调（如拍照）
                if self.progress_callback:
                    logger.debug(f"执行进度回调（第{self.current_rotation}次）")
                    self.progress_callback(self.current_rotation, rotations)
                
                # 延时（用于拍照后等待）
                if delay > 0:
                    time.sleep(delay)
                
                logger.info(f"完成 {self.current_rotation}/{rotations}")
            
            logger.info("旋转序列完成")
            return True
            
        except Exception as e:
            logger.error(f"执行旋转序列异常: {e}")
            return False
        finally:
            # 3.9 实现线程清理
            if error_thread and error_thread.is_alive():
                logger.info("停止错误检测线程...")
                stop_event.set()
                error_thread.join(timeout=2)
                if error_thread.is_alive():
                    logger.warning("错误检测线程未在2秒内停止")
    
    def reset_to_home(self):
        """
        检测机械轴当前位置，若不在起始位置则执行复位
        
        Returns:
            bool: 是否成功
        """
        try:
            # 读取当前位置
            result = self.client.read_register(D_REGISTER_POSITION, count=2)
            if result is None:
                logger.error("读取机械轴位置失败")
                return False
            
            # 解析角度值（大端序，2个寄存器）
            cur_angle = (result[0] << 16) | result[1] if len(result) >= 2 else result[0]
            logger.info(f"当前机械轴位置: {cur_angle / 100:.2f}°")
            
            # 判断是否在起始位置附近（误差±1°，即±100）
            if cur_angle > 36010 or (cur_angle > 10 and cur_angle < 35990):
                logger.info("机械轴未处于起始位置，执行复位...")
                
                # 切换自动模式（黄灯长亮）
                if not self.client.write_coil(S_COIL_MODE, False):
                    logger.error("切换自动模式失败")
                    return False
                time.sleep(1)
                
                # 切换手动模式（绿灯长亮）
                if not self.client.write_coil(S_COIL_MODE, True):
                    logger.error("切换手动模式失败")
                    return False
                time.sleep(1)
                
                # 等待回到起始位置（估算时间）
                wait_time = cur_angle / (self.speed * 100) if self.speed > 0 else 10
                logger.info(f"等待复位完成，预计{wait_time:.1f}秒")
                time.sleep(wait_time + 1)
            
            logger.info("机械轴已处于起始位置")
            return True
            
        except Exception as e:
            logger.error(f"复位到起始位置异常: {e}")
            return False
    
    def wait_for_ready(self, timeout=60):
        """
        轮询coilReady信号等待机械轴就绪
        
        Args:
            timeout: 超时时间(秒)
            
        Returns:
            bool: 是否就绪
        """
        try:
            logger.debug("等待机械轴就绪...")
            
            start_time = time.time()
            
            while True:
                # 检查超时
                if time.time() - start_time > timeout:
                    logger.error("等待机械轴就绪超时")
                    return False
                
                # 读取coilReady状态
                ready_state = self.client.read_coil(coilReady)
                if ready_state is None:
                    logger.error("读取coilReady状态失败")
                    return False
                
                # 检测就绪信号
                if ready_state:
                    logger.debug("机械轴已就绪")
                    return True
                
                time.sleep(0.1)  # 100ms轮询间隔
                
        except Exception as e:
            logger.error(f"等待机械轴就绪异常: {e}")
            return False
    
    def check_error(self, stop_event, error_continue_model=errHandleModeDefault):
        """
        后台线程持续监控coilErrorOccur，检测到错误时执行异常处理
        
        Args:
            stop_event: threading.Event对象，用于控制线程停止
            error_continue_model: 异常处理模式 (0=继续, 1=复位, 2=默认)
        """
        # 创建独立的Modbus连接以避免线程安全问题
        error_client = None
        try:
            # 使用相同的连接参数创建新客户端
            error_client = ModbusClient(
                host=self.client.host,
                port=self.client.port
            )
            
            if not error_client.connect():
                logger.error("错误检测线程无法连接PLC")
                return
            
            logger.info("错误检测线程已启动")
            
            while not stop_event.is_set():
                # 读取错误状态
                error_state = error_client.read_coil(coilErrorOccur)
                if error_state is None:
                    logger.warning("读取错误状态失败，重试中...")
                    time.sleep(1)
                    continue
                
                # coilErrorOccur: 0-有错误，1-无错误
                if error_state == coilResultFalse:
                    logger.error("检测到PLC错误!")
                    
                    # 根据模式处理错误
                    if error_continue_model == errHandleModeContinue:
                        # 模式0: 继续运行
                        logger.info("错误处理模式: 继续运行")
                        # 写入D332=0
                        error_client.write_register(D_REGISTER_CONTINUE, errHandleModeContinue)
                    elif error_continue_model == errHandleModeInit:
                        # 模式1: 回到起始位置
                        logger.info("错误处理模式: 回到起始位置")
                        # 写入D332=1
                        error_client.write_register(D_REGISTER_CONTINUE, errHandleModeInit)
                    else:
                        # 模式2: 默认
                        logger.info("错误处理模式: 默认")
                        # 写入D332=2
                        error_client.write_register(D_REGISTER_CONTINUE, errHandleModeDefault)
                    
                    # 触发复位处理
                    error_client.write_coil(coilRecover, True)
                    time.sleep(0.1)
                    error_client.write_coil(coilRecover, False)
                    
                    logger.info("错误已处理")
                
                time.sleep(1)  # 1秒轮询间隔
                
        except Exception as e:
            logger.error(f"错误检测线程异常: {e}")
        finally:
            if error_client:
                error_client.disconnect()
            logger.info("错误检测线程已停止")
    
    def start_machine(self):
        """
        手动模式下启动设备
        
        Returns:
            bool: 是否成功
        """
        try:
            logger.info("手动模式启动设备...")
            
            # 确保在手动模式
            if not self.client.write_coil(coilMode, True):
                logger.error("切换到手动模式失败")
                return False
            time.sleep(0.5)
            
            # 发送启动信号
            if not self.client.write_coil(coilMachineStart, True):
                logger.error("发送启动信号失败")
                return False
            time.sleep(0.1)
            
            # 恢复启动信号
            self.client.write_coil(coilMachineStart, False)
            
            logger.info("设备启动成功")
            return True
            
        except Exception as e:
            logger.error(f"启动设备异常: {e}")
            return False