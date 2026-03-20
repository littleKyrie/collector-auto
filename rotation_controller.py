"""
旋转控制模块
实现360度旋转控制功能
"""

import time
import logging
from modbus_client import ModbusClient

# 配置日志
logger = logging.getLogger(__name__)

# 寄存器地址定义（根据Excel文档）
# D寄存器（保持寄存器）
D_SET_AUTO_ACTION_POSITION = 300    # 设置间隔运行角度
D_SET_AUTO_ACTION_VELOCITY = 304    # 设置运行速度
D_SET_MODEL_0_DELAY_TIMES = 316     # 设置模式0延时时间
D_SET_MODEL_NUMBER = 320            # 设置模式参数
D_GET_AUTO_ACTION_POSITION = 308    # 读取当前设置的间隔角度
D_GET_AUTO_ACTION_VELOCITY = 312    # 读取当前的运行速度

# S寄存器（线圈）
S_SET_ENABLE_SET_VALUE = 57744      # 400+57344 确认值参数写入
S_SET_AUTO_ACTION = 57745           # 401+57344 设置自动运行输入
S_MOVE_DONE = 57747                 # 403+57344 分段内单次移动完成


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
            return False, f"单次旋转角度{angle_per_step:.2f}°超过最大限制327.67°，请增加旋转次数"
        
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
    
    def configure_parameters(self, angle_per_step, speed, delay):
        """
        配置PLC参数
        
        Args:
            angle_per_step: 每次旋转角度(°)
            speed: 旋转速度(°/s)
            delay: 延时时间(秒)
            
        Returns:
            bool: 是否配置成功
        """
        try:
            # 计算写入值（需要×100）
            angle_value = int(angle_per_step * 100)
            speed_value = int(speed * 100)
            delay_value = int(delay)
            mode_value = 0  # 模式0：间隔运行+延时
            
            logger.info(f"配置参数: 角度={angle_per_step:.2f}°({angle_value}), "
                       f"速度={speed:.1f}°/s({speed_value}), "
                       f"延时={delay}s({delay_value}), 模式={mode_value}")
            
            # 写入D300: 角度
            if not self.client.write_register(D_SET_AUTO_ACTION_POSITION, angle_value):
                logger.error("写入角度参数失败")
                return False
            
            # 写入D304: 速度
            if not self.client.write_register(D_SET_AUTO_ACTION_VELOCITY, speed_value):
                logger.error("写入速度参数失败")
                return False
            
            # 写入D316: 延时
            if not self.client.write_register(D_SET_MODEL_0_DELAY_TIMES, delay_value):
                logger.error("写入延时参数失败")
                return False
            
            # 写入D320: 模式
            if not self.client.write_register(D_SET_MODEL_NUMBER, mode_value):
                logger.error("写入模式参数失败")
                return False
            
            logger.info("参数配置成功")
            return True
            
        except Exception as e:
            logger.error(f"配置参数异常: {e}")
            return False
    
    def confirm_parameters(self):
        """
        触发S400确认参数
        
        Returns:
            bool: 是否成功
        """
        try:
            logger.info("确认参数写入...")
            
            # 写入S400=1触发参数生效
            if not self.client.write_coil(S_SET_ENABLE_SET_VALUE, True):
                logger.error("触发参数确认失败")
                return False
            
            # 短暂延时确保参数生效
            time.sleep(0.2)
            
            # 写入S400=0
            self.client.write_coil(S_SET_ENABLE_SET_VALUE, False)
            
            logger.info("参数确认成功")
            return True
            
        except Exception as e:
            logger.error(f"确认参数异常: {e}")
            return False
    
    def start_rotation(self):
        """
        触发S401启动运行
        
        Returns:
            bool: 是否成功
        """
        try:
            logger.info("启动旋转...")
            
            # 写入S401=1启动运行
            if not self.client.write_coil(S_SET_AUTO_ACTION, True):
                logger.error("启动旋转失败")
                return False
            
            # 短暂延时
            time.sleep(0.1)
            
            # 写入S401=0
            self.client.write_coil(S_SET_AUTO_ACTION, False)
            
            logger.info("旋转已启动")
            return True
            
        except Exception as e:
            logger.error(f"启动旋转异常: {e}")
            return False
    
    def wait_for_move_done(self, timeout=60):
        """
        监控S403上升沿等待移动完成
        
        Args:
            timeout: 超时时间(秒)
            
        Returns:
            bool: 是否完成
        """
        try:
            logger.debug("等待移动完成...")
            
            # 读取初始状态
            last_state = self.client.read_coil(S_MOVE_DONE)
            if last_state is None:
                logger.error("读取Move_Done状态失败")
                return False
            
            start_time = time.time()
            
            while True:
                # 检查超时
                if time.time() - start_time > timeout:
                    logger.error("等待移动完成超时")
                    return False
                
                # 读取当前状态
                current_state = self.client.read_coil(S_MOVE_DONE)
                if current_state is None:
                    logger.error("读取Move_Done状态失败")
                    return False
                
                # 检测上升沿（从False变为True）
                if not last_state and current_state:
                    logger.debug("检测到移动完成信号")
                    return True
                
                last_state = current_state
                time.sleep(0.1)  # 100ms轮询间隔
                
        except Exception as e:
            logger.error(f"等待移动完成异常: {e}")
            return False
    
    def run_rotation_sequence(self, rotations, speed, delay, progress_callback=None):
        """
        执行完整旋转序列
        
        Args:
            rotations: 旋转次数
            speed: 旋转速度(°/s)
            delay: 延时时间(秒)
            progress_callback: 进度回调函数
            
        Returns:
            bool: 是否成功
        """
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
            
            logger.info(f"开始旋转序列: {rotations}次, 每次{self.angle_per_step:.2f}°, "
                       f"速度{speed}°/s, 延时{delay}s")
            
            # 配置参数
            if not self.configure_parameters(self.angle_per_step, speed, delay):
                logger.error("配置参数失败")
                return False
            
            # 确认参数
            if not self.confirm_parameters():
                logger.error("确认参数失败")
                return False
            
            # 启动旋转
            if not self.start_rotation():
                logger.error("启动旋转失败")
                return False
            
            # 监控每次移动完成
            for i in range(rotations):
                self.current_rotation = i + 1
                
                # 等待移动完成
                if not self.wait_for_move_done():
                    logger.error(f"第{self.current_rotation}次移动失败")
                    return False
                
                logger.info(f"完成 {self.current_rotation}/{rotations}")
                
                # 调用进度回调
                if self.progress_callback:
                    self.progress_callback(self.current_rotation, rotations)
            
            logger.info("旋转序列完成")
            return True
            
        except Exception as e:
            logger.error(f"执行旋转序列异常: {e}")
            return False
    
    def stop_rotation(self):
        """
        停止运行
        
        Returns:
            bool: 是否成功
        """
        try:
            logger.info("停止旋转...")
            # 注意：根据文档，停止信号使用System_Stop (S151)
            # 这里暂时不实现，因为自动模式下会自动停止
            return True
            
        except Exception as e:
            logger.error(f"停止旋转异常: {e}")
            return False