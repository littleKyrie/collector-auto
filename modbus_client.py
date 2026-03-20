"""
Modbus-TCP客户端封装模块
用于与PLC进行通讯
"""

import logging
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ModbusClient:
    """Modbus-TCP客户端封装类"""
    
    def __init__(self, host='192.168.1.88', port=502, timeout=3, retries=3):
        """
        初始化Modbus客户端
        
        Args:
            host: PLC IP地址
            port: Modbus端口
            timeout: 连接超时时间(秒)
            retries: 重试次数
        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.client = None
        self.connected = False
        
    def connect(self):
        """
        连接到PLC
        
        Returns:
            bool: 连接是否成功
        """
        try:
            logger.info(f"正在连接PLC: {self.host}:{self.port}")
            self.client = ModbusTcpClient(
                host=self.host,
                port=self.port,
                timeout=self.timeout
            )
            
            self.connected = self.client.connect()
            if self.connected:
                logger.info("PLC连接成功")
            else:
                logger.error("PLC连接失败")
                
            return self.connected
            
        except Exception as e:
            logger.error(f"连接异常: {e}")
            self.connected = False
            return False
    
    def disconnect(self):
        """断开连接"""
        if self.client:
            try:
                self.client.close()
                logger.info("已断开PLC连接")
            except Exception as e:
                logger.error(f"断开连接异常: {e}")
            finally:
                self.connected = False
                self.client = None
    
    def read_coil(self, address, unit=1):
        """
        读取线圈状态
        
        Args:
            address: 线圈地址
            unit: 单元号
            
        Returns:
            bool: 线圈状态，失败返回None
        """
        if not self.connected:
            logger.error("未连接到PLC")
            return None
            
        try:
            result = self.client.read_coils(address, count=1, device_id=unit)
            if result.isError():
                logger.error(f"读取线圈{address}失败: {result}")
                return None
            return result.bits[0]
            
        except ModbusException as e:
            logger.error(f"读取线圈异常: {e}")
            return None
    
    def write_coil(self, address, value, unit=1):
        """
        写入线圈
        
        Args:
            address: 线圈地址
            value: 写入值(True/False)
            unit: 单元号
            
        Returns:
            bool: 是否写入成功
        """
        if not self.connected:
            logger.error("未连接到PLC")
            return False
            
        try:
            result = self.client.write_coil(address, value, device_id=unit)
            if result.isError():
                logger.error(f"写入线圈{address}失败: {result}")
                return False
            logger.debug(f"写入线圈{address}={value}成功")
            return True
            
        except ModbusException as e:
            logger.error(f"写入线圈异常: {e}")
            return False
    
    def read_register(self, address, count=1, unit=1):
        """
        读取保持寄存器
        
        Args:
            address: 寄存器地址
            count: 读取数量
            unit: 单元号
            
        Returns:
            list: 寄存器值列表，失败返回None
        """
        if not self.connected:
            logger.error("未连接到PLC")
            return None
            
        try:
            result = self.client.read_holding_registers(address, count=count, device_id=unit)
            if result.isError():
                logger.error(f"读取寄存器{address}失败: {result}")
                return None
            return result.registers
            
        except ModbusException as e:
            logger.error(f"读取寄存器异常: {e}")
            return None
    
    def write_register(self, address, value, unit=1):
        """
        写入单个寄存器
        
        Args:
            address: 寄存器地址
            value: 写入值
            unit: 单元号
            
        Returns:
            bool: 是否写入成功
        """
        if not self.connected:
            logger.error("未连接到PLC")
            return False
            
        try:
            result = self.client.write_register(address, value, device_id=unit)
            if result.isError():
                logger.error(f"写入寄存器{address}失败: {result}")
                return False
            logger.debug(f"写入寄存器{address}={value}成功")
            return True
            
        except ModbusException as e:
            logger.error(f"写入寄存器异常: {e}")
            return False
    
    def write_registers(self, address, values, unit=1):
        """
        批量写入寄存器
        
        Args:
            address: 起始寄存器地址
            values: 写入值列表
            unit: 单元号
            
        Returns:
            bool: 是否写入成功
        """
        if not self.connected:
            logger.error("未连接到PLC")
            return False
            
        try:
            result = self.client.write_registers(address, values, device_id=unit)
            if result.isError():
                logger.error(f"批量写入寄存器{address}失败: {result}")
                return False
            logger.debug(f"批量写入寄存器{address}成功，数量: {len(values)}")
            return True
            
        except ModbusException as e:
            logger.error(f"批量写入寄存器异常: {e}")
            return False
    
    def is_connected(self):
        """检查连接状态"""
        return self.connected and self.client is not None