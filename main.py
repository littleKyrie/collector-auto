"""
360度旋转控制命令行入口
"""

import sys
import argparse
import logging
import os
import time

# For plate controller
from modbus_client import ModbusClient
from rotation_controller import RotationController

# For camera controller
from ImageNode import *

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Lens map
ANGLE_START = 0.0      # 起始端角度(靠近默认0点)
ANGLE_END   = 2900.0   # 终点端角度

# Utils
def generate_step_angles(steps):
    """根据步进次数，自动计算均分的物理角度列表"""
    if steps <= 0: return []
    if steps == 1: return [ANGLE_START]
    angles = []
    step_interval = (ANGLE_END - ANGLE_START) / (steps - 1)
    for i in range(steps):
        angles.append(round(ANGLE_START + (i * step_interval), 2))
    return angles

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
        description='360度旋转控制脚本 - 控制PLC执行自动化旋转（模式1：间隔运行+触发）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                          # 使用默认参数(12次, 10°/s, 3秒延时)
  python main.py --rotations 24           # 24次旋转，每次15°
  python main.py --speed 5 --delay 5      # 速度5°/s，拍照后等待5秒
  python main.py --host 192.168.1.100     # 连接其他PLC地址
  python main.py --error-continue-model 0 # 异常后继续运行
  python main.py --error-signal False     # 不屏蔽红外感应信号

寄存器说明:
  D300: 间隔运行角度 (值=角度×100)
  D304: 运行速度 (值=速度×100)
  D320: 模式 (1=间隔运行+触发)
  D324: 机械轴当前位置
  D332: 异常后继续运行模式 (0=继续, 1=复位, 2=默认)
  S400: 确认参数写入
  S57746 (coilResume): 触发继续运行
  S57747 (coilReady): 机械轴就绪信号
  S57348 (coilMode): 模式切换 (0=自动, 1=手动)
  S57649 (coilNoError): 屏蔽雷达告警
  S57356 (coilErrorOccur): 错误发生标志
  S57496 (coilRecover): 异常复位
  S57494 (coilMachineStart): 手动模式启动
        """
    )
    
    parser.add_argument(
        '--rotations', '-r',
        type=int,
        default=12,
        help='转台旋转次数 (默认: 12次)'
    )
    
    parser.add_argument(
        '--speed', '-s',
        type=float,
        default=10.0,
        help='转台旋转速度，单位°/s (默认: 10，范围: 0-30)'
    )
    
    parser.add_argument(
        '--delay', '-d',
        type=float,
        default=3.0,
        help='转台拍照后等待时间，单位秒 (默认: 3)'
    )
    
    parser.add_argument(
        '--error-signal',
        type=bool,
        default=True,
        help='转台是否屏蔽红外感应信号 (默认: True)'
    )
    
    parser.add_argument(
        '--error-continue-model',
        type=int,
        default=2,
        choices=[0, 1, 2],
        help='转台异常处理模式: 0=继续运行, 1=回到起始位置, 2=默认 (默认: 2)'
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

    # For lens control
    parser.add_argument('--lens-steps', type=int, default=5, help=f'镜头变焦步进次数 (默认: 5)')
    
    args = parser.parse_args()
    
    # 设置日志级别
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # 1. 生成相机的步进角度列表
    target_angles = generate_step_angles(args.lens_steps)

    # =========================================
    # 步骤一：初始化相机硬件系统
    # =========================================
    print("\n[1/3] 正在初始化成像视觉系统...")
    sys_dev = ImagingSystem()
    sys_dev.init_system()
    sys_dev.open_all()
    # 执行镜头官方寻零
    # sys_dev.serial_global_homing()
    sys_dev.parallel_global_homing()

    # =========================================
    # 步骤二：连接 PLC 转台
    # =========================================
    # 创建Modbus客户端
    client = ModbusClient(host=args.host, port=args.port)
    print("\n[2/3] 正在连接PLC转台...")
    if not client.connect():
        print("错误: 无法连接到PLC，请检查网络连接和PLC状态")
        sys_dev.close_all()
        return 1
    print("✅ PLC连接成功!")

    # =========================================
    # 步骤三：定义回调函数 (直接定义在 main 内部访问局部变量)
    # =========================================
    def progress_callback(current, total):
        print(f"\n" + "="*55)
        print(f"🔄 转台进度: {current}/{total} | 转台已就位，相机接管控制权...")
        print("="*55)
        
        # 执行相机变焦与拍摄逻辑
        for step_idx, target_angle in enumerate(target_angles):
            print(f"\n 🎯 [阵位 {current} - 镜头步进 {step_idx + 1}/{len(target_angles)}] -> 目标角度: {target_angle}°")
            
            # 驱动镜头
            # sys_dev.serial_move_lenses(target_angle)
            sys_dev.parallel_move_lenses(target_angle)
            
            # 镜头停稳后拍照，按照阵位和步进分文件夹保存
            save_dir_base = os.path.abspath(f"./Output/Position_{current}/Step_{step_idx + 1}_Angle_{target_angle}")
            # sys_dev.serial_snap_all(save_dir_base)
            sys_dev.parallel_snap_all(save_dir_base)
            
        # 拍摄完毕，镜头平滑退回初始 0 点
        print(f"\n 🔙 第 {current} 阵位拍摄完毕，镜头正在复位...")
        # sys_dev.serial_move_lenses(0.0)
        sys_dev.parallel_move_lenses(0.0)
        
        print(f"✅ 镜头复位完成，归还控制权给转台。")
        print("="*55 + "\n")
    
    # =========================================
    # 步骤四：执行自动化联动循环
    # =========================================
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
        print("360度旋转控制系统（模式1：间隔运行+触发）")
        print("=" * 60)
        print(f"PLC地址: {args.host}:{args.port}")
        print(f"旋转次数: {args.rotations}次")
        print(f"每次角度: {360.0/args.rotations:.2f}°")
        print(f"旋转速度: {args.speed}°/s")
        print(f"镜头配置: 每阵位均分变焦 {args.lens_steps} 次")
        print(f"拍照后等待: {args.delay}秒")
        print(f"屏蔽红外信号: {args.error_signal}")
        error_modes = {0: "继续运行", 1: "回到起始位置", 2: "默认"}
        print(f"异常处理模式: {error_modes.get(args.error_continue_model, '未知')}")
        print("=" * 60)
        
        # 连接PLC
        # print("\n正在连接PLC...")
        # if not client.connect():
        #     print("错误: 无法连接到PLC，请检查网络连接和PLC状态")
        #     sys_dev.close_all()
        #     return 1
        #
        # print("PLC连接成功!")
        
        # 开始旋转
        print("\n[3/3] 开始执行旋转序列...")
        print("-" * 60)
        
        success = controller.run_rotation_sequence(
            rotations=args.rotations,
            speed=args.speed,
            delay=args.delay,
            progress_callback=progress_callback,
            error_signal=args.error_signal,
            error_continue_model=args.error_continue_model
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
        # 清理所有资源
        print("\n🧹 正在安全断开系统...")
        client.disconnect()
        sys_dev.close_all()
        print("✅ 系统已安全退出。")


if __name__ == '__main__':
    sys.exit(main())
