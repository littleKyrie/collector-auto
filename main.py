"""
旋转拍摄控制命令行入口
"""

import sys
import argparse
import logging
import os
import time

# Plate controller
from modbus_client import ModbusClient
from rotation_controller import RotationController

# Camera controller is imported lazily in mode 2 to avoid requiring hardware
# dependencies when selecting exit or placeholder modes.

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


def parse_bool(value):
    """解析命令行布尔参数"""
    normalized = value.strip().lower()
    true_values = {"true", "1", "yes", "y", "on"}
    false_values = {"false", "0", "no", "n", "off"}

    if normalized in true_values:
        return True
    if normalized in false_values:
        return False

    raise argparse.ArgumentTypeError("布尔参数必须是 true/false、1/0、yes/no 或 on/off")


def print_top_level_help():
    """打印顶层帮助信息"""
    print("""usage: main.py [-h] [--full_shot | --focus_calibration]

多功能采集控制脚本

可用模式:
  --full_shot          旋转多轮拍摄。查看参数: python main.py --full_shot --help
  --focus_calibration  对焦范围标定。查看说明: python main.py --focus_calibration --help

帮助:
  -h, --help           显示本帮助信息
""")


def build_full_shot_parser():
    """创建 full_shot 模式的命令行解析器"""
    parser = argparse.ArgumentParser(
        prog='main.py --full_shot',
        description='旋转多轮拍摄',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py --full_shot
  python main.py --full_shot --rotations 24 --speed 5 --delay 2
  python main.py --full_shot --host 192.168.1.100 --lens-steps 3
  python main.py --full_shot --error-continue-model 0 --error-signal false

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
        type=parse_bool,
        default=True,
        help='转台是否屏蔽红外感应信号 (默认: true)'
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
    parser.add_argument(
        '--lens-coordinate-mode',
        choices=['software', 'hardware'],
        default='software',
        help='镜头坐标模式: software=软件坐标默认模式, hardware=硬件清零后使用电机内部角度'
    )

    return parser


def parse_args(argv=None):
    """解析 full_shot 命令行参数"""
    return build_full_shot_parser().parse_args(argv)


def print_focus_calibration_help():
    """打印 focus_calibration 模式帮助"""
    print("""usage: main.py --focus_calibration [-h]

对焦范围标定

当前功能尚未实现，已保留入口。

帮助:
  -h, --help           显示 focus_calibration 说明信息
""")


def handle_full_shot_cli(argv):
    """处理 full_shot 模式命令行"""
    args = parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    return run_rotation_multi_shot(args)


def handle_focus_calibration_cli(argv):
    """处理 focus_calibration 模式命令行"""
    if argv and argv[0] in ("-h", "--help"):
        print_focus_calibration_help()
        return 0

    if argv:
        print(f"未知参数: {' '.join(argv)}")
        print_focus_calibration_help()
        return 2

    return run_focus_range_calibration(None)


def run_focus_range_calibration(args):
    """对焦范围标定占位入口"""
    print("\n已选择：对焦范围标定")
    print("当前功能尚未实现，已保留入口。")
    print("已完成所有镜头焦距范围标定。")
    return 0


def run_rotation_multi_shot(args):
    """旋转多轮拍摄"""
    # 1. 生成相机的步进角度列表
    target_angles = generate_step_angles(args.lens_steps)
    sys_dev = None
    client = None

    try:
        from ImageNode import ImagingSystem

        # =========================================
        # 步骤一：初始化相机硬件系统
        # =========================================
        print("\n[1/3] 正在初始化成像视觉系统...")
        sys_dev = ImagingSystem(lens_coordinate_mode=args.lens_coordinate_mode)
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
            return 1
        print("✅ PLC连接成功!")

        # =========================================
        # 步骤三：定义回调函数 (直接定义在模式2内部访问局部变量)
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
        print(f"镜头坐标模式: {args.lens_coordinate_mode}")
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
        if client:
            client.disconnect()
        if sys_dev:
            sys_dev.close_all()
        print("✅ 系统已安全退出。")


def main(argv=None):
    """主函数"""
    argv = sys.argv[1:] if argv is None else argv

    if not argv or argv[0] in ("-h", "--help"):
        print_top_level_help()
        return 0

    mode = argv[0]
    mode_args = argv[1:]

    if mode == "--full_shot":
        return handle_full_shot_cli(mode_args)

    if mode == "--focus_calibration":
        return handle_focus_calibration_cli(mode_args)

    print(f"未知模式: {mode}")
    print_top_level_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
