"""
360度旋转控制命令行入口
"""

import sys
import argparse
import logging
from modbus_client import ModbusClient
from rotation_controller import RotationController

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def progress_callback(current, total):
    """
    进度显示回调函数
    
    Args:
        current: 当前完成次数
        total: 总次数
    """
    print(f"进度: {current}/{total} ({current*100//total}%)")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='360度旋转控制脚本 - 控制PLC执行自动化旋转',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                          # 使用默认参数(12次, 10°/s, 3秒延时)
  python main.py --rotations 24           # 24次旋转，每次15°
  python main.py --speed 5 --delay 5      # 速度5°/s，延时5秒
  python main.py --host 192.168.1.100     # 连接其他PLC地址

寄存器说明:
  D300: 间隔运行角度 (值=角度×100)
  D304: 运行速度 (值=速度×100)
  D316: 延时时间 (秒)
  D320: 模式 (0=间隔运行+延时)
  S400: 确认参数写入
  S401: 启动自动运行
  S403: 单次移动完成标志
        """
    )
    
    parser.add_argument(
        '--rotations', '-r',
        type=int,
        default=12,
        help='旋转次数 (默认: 12次)'
    )
    
    parser.add_argument(
        '--speed', '-s',
        type=float,
        default=10.0,
        help='旋转速度，单位°/s (默认: 10，范围: 0-30)'
    )
    
    parser.add_argument(
        '--delay', '-d',
        type=float,
        default=3.0,
        help='每次旋转后的停顿时间，单位秒 (默认: 3)'
    )
    
    parser.add_argument(
        '--host',
        type=str,
        default='192.168.1.88',
        help='PLC IP地址 (默认: 192.168.1.88)'
    )
    
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=502,
        help='Modbus端口 (默认: 502)'
    )
    
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='显示详细日志'
    )
    
    args = parser.parse_args()
    
    # 设置日志级别
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # 创建Modbus客户端
    client = ModbusClient(host=args.host, port=args.port)
    
    try:
        # 创建旋转控制器
        controller = RotationController(client)
        
        # 验证参数
        valid, error_msg = controller.validate_parameters(
            args.rotations, args.speed, args.delay
        )
        if not valid:
            print(f"错误: {error_msg}")
            return 1
        
        # 显示配置信息
        print("=" * 60)
        print("360度旋转控制系统")
        print("=" * 60)
        print(f"PLC地址: {args.host}:{args.port}")
        print(f"旋转次数: {args.rotations}次")
        print(f"每次角度: {360.0/args.rotations:.2f}°")
        print(f"旋转速度: {args.speed}°/s")
        print(f"停顿时间: {args.delay}秒")
        print("=" * 60)
        
        # 连接PLC
        print("\n正在连接PLC...")
        if not client.connect():
            print("错误: 无法连接到PLC，请检查网络连接和PLC状态")
            return 1
        
        print("PLC连接成功!")
        
        # 开始旋转
        print("\n开始执行旋转序列...")
        print("-" * 60)
        
        success = controller.run_rotation_sequence(
            rotations=args.rotations,
            speed=args.speed,
            delay=args.delay,
            progress_callback=progress_callback
        )
        
        print("-" * 60)
        
        if success:
            print("\n✓ 旋转完成!")
            return 0
        else:
            print("\n✗ 旋转失败!")
            return 1
            
    except KeyboardInterrupt:
        print("\n\n用户中断操作")
        return 130
        
    except Exception as e:
        logger.error(f"程序异常: {e}")
        print(f"\n错误: {e}")
        return 1
        
    finally:
        # 断开连接
        client.disconnect()
        print("已断开PLC连接")


if __name__ == '__main__':
    sys.exit(main())