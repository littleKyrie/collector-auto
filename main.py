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

# Mode id
MODE_EXIT = 0
MODE_FOCUS_RANGE_CALIBRATION = 1
MODE_ROTATION_MULTI_SHOT = 2
MODE_BACK_TO_MODE_SELECT = "back_to_mode_select"

MODE2_BACK_TO_MODE_SELECT = "back"
MODE2_RUN = "run"

ROTATION_MULTI_SHOT_PARAM_SPECS = {
    "rotations": {
        "label": "旋转次数",
        "description": "整数，大于等于 0",
        "type": int,
        "min": 0,
    },
    "speed": {
        "label": "转台旋转速度",
        "description": "数字，单位°/s，范围 0-30",
        "type": float,
        "min": 0,
        "max": 30,
    },
    "delay": {
        "label": "拍照后等待时间",
        "description": "数字，单位秒",
        "type": float,
        "min": 0,
    },
    "error_signal": {
        "label": "是否屏蔽红外感应信号",
        "description": "true/false",
        "type": bool,
    },
    "error_continue_model": {
        "label": "异常处理模式",
        "description": "0=继续运行, 1=回到起始位置, 2=默认",
        "type": int,
        "choices": [0, 1, 2],
    },
    "host": {
        "label": "PLC IP 地址",
        "description": "字符串，默认 192.168.1.88",
        "type": str,
    },
    "port": {
        "label": "Modbus 端口",
        "description": "整数，范围 1-65535",
        "type": int,
        "min": 1,
        "max": 65535,
    },
    "lens_steps": {
        "label": "镜头变焦步进次数",
        "description": "整数，大于 0",
        "type": int,
        "min": 1,
    },
    "lens_coordinate_mode": {
        "label": "镜头坐标模式",
        "description": "程序/镜头硬件变量记录旋转角度",
        "type": str,
        "choices": ["software", "hardware"],
    },
}

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

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='360度旋转控制脚本 - 控制PLC执行自动化旋转 - 旋转间隔控制相机拍照（模式1：间隔运行+触发）',
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
    parser.add_argument(
        '--lens-coordinate-mode',
        choices=['software', 'hardware'],
        default='software',
        help='镜头坐标模式: software=软件坐标默认模式, hardware=硬件清零后使用电机内部角度'
    )

    return parser.parse_args()


def prompt_program_mode(modes):
    """展示顶层模式菜单并读取用户选择"""
    while True:
        print("\n可选择的程序模式如下:")
        for mode_id, mode in sorted(modes.items()):
            print(f"  {mode_id}. {mode['name']}")

        raw_value = input("请输入模式 id: ").strip()

        try:
            selected_mode = int(raw_value)
        except ValueError:
            print("输入无效，请输入数字模式 id。")
            continue

        if selected_mode not in modes:
            print(f"未知模式 id: {selected_mode}")
            continue

        return selected_mode


def run_focus_range_calibration(args):
    """模式1：对焦范围标定占位入口"""
    print("\n已选择模式 1：对焦范围标定")
    print("当前功能尚未实现，已保留入口。")
    print("已完成所有镜头焦距范围标定。")
    return MODE_BACK_TO_MODE_SELECT


def print_rotation_multi_shot_params(args):
    """展示模式2当前可配置参数"""
    print("\n已选择模式 2：旋转多轮拍摄")
    print("\n当前可配置参数:")
    for name, spec in ROTATION_MULTI_SHOT_PARAM_SPECS.items():
        current_value = getattr(args, name)
        print(f"  {name:<24} {spec['label']}，{spec['description']}，当前值: {current_value}")

    print("\n输入格式:")
    print("  参数名 参数值, 参数名 参数值")
    print("\n示例:")
    print("  rotations 24, speed 5, delay 2")
    print("  host 192.168.1.100，port 502，lens_steps 3")
    print("\n请选择操作:")
    print("  0. 退出当前模式，返回模式选择")
    print("  1. 使用默认参数")
    print("  2. 手动修改参数")


def print_rotation_multi_shot_param_prompt():
    """展示模式2手动参数输入提示"""
    print("\n请输入需要修改的参数:")
    print("  格式：参数名 参数值, 参数名 参数值")
    print("  示例：rotations 24, speed 5, delay 2")
    print("  未输入的参数将继续使用当前值。")


def parse_param_overrides(raw_value):
    """将用户输入解析为参数覆盖字典"""
    normalized = raw_value.replace("，", ",")
    overrides = {}

    for group in normalized.split(","):
        group = group.strip()
        if not group:
            continue

        parts = group.split(None, 1)
        if len(parts) != 2:
            raise ValueError(f"参数格式错误: {group}")

        name, value = parts
        name = name.lstrip("-").replace("-", "_")
        if name in overrides:
            print(f"参数 {name} 重复输入，使用最后一次值: {value.strip()}")
        overrides[name] = value.strip()

    return overrides


def convert_bool_param(value):
    """转换控制台输入的布尔值"""
    normalized = value.strip().lower()
    true_values = {"true", "1", "yes", "y", "on", "是", "真"}
    false_values = {"false", "0", "no", "n", "off", "否", "假"}

    if normalized in true_values:
        return True
    if normalized in false_values:
        return False

    raise ValueError("必须是布尔值，可输入 true/false、1/0、yes/no、是/否")


def convert_param_value(name, value, spec):
    """按参数规格转换输入值"""
    param_type = spec["type"]

    try:
        if param_type is bool:
            converted = convert_bool_param(value)
        elif param_type is int:
            converted = int(value)
        elif param_type is float:
            converted = float(value)
        elif param_type is str:
            converted = value
        else:
            raise ValueError(f"不支持的参数类型: {param_type}")
    except ValueError as exc:
        label = spec.get("label", name)
        if param_type is bool:
            raise ValueError(f"{name}({label}) {exc}") from exc
        if param_type is int:
            raise ValueError(f"{name}({label}) 必须是整数") from exc
        if param_type is float:
            raise ValueError(f"{name}({label}) 必须是数字") from exc
        raise

    if "min" in spec and converted < spec["min"]:
        raise ValueError(f"{name} 不能小于 {spec['min']}")

    if "max" in spec and converted > spec["max"]:
        raise ValueError(f"{name} 不能大于 {spec['max']}")

    if "choices" in spec and converted not in spec["choices"]:
        choices = ", ".join(str(choice) for choice in spec["choices"])
        raise ValueError(f"{name} 只能是以下值之一: {choices}")

    return converted


def apply_rotation_multi_shot_overrides(args, overrides):
    """校验并写回模式2参数覆盖值"""
    converted_values = {}

    for name, value in overrides.items():
        if name not in ROTATION_MULTI_SHOT_PARAM_SPECS:
            raise ValueError(f"未知参数: {name}")

        spec = ROTATION_MULTI_SHOT_PARAM_SPECS[name]
        converted_values[name] = convert_param_value(name, value, spec)

    for name, value in converted_values.items():
        setattr(args, name, value)

    return args


def print_final_rotation_multi_shot_params(args):
    """打印模式2最终运行参数"""
    print("\n最终运行参数:")
    for name in ROTATION_MULTI_SHOT_PARAM_SPECS:
        print(f"  {name}: {getattr(args, name)}")


def prompt_rotation_multi_shot_overrides(args):
    """模式2手动参数输入循环"""
    print_rotation_multi_shot_param_prompt()

    while True:
        raw_value = input("请输入参数: ").strip()

        if not raw_value:
            print_final_rotation_multi_shot_params(args)
            return MODE2_RUN, args

        try:
            overrides = parse_param_overrides(raw_value)
            apply_rotation_multi_shot_overrides(args, overrides)
        except ValueError as e:
            print(f"参数输入错误: {e}")
            print("请重新输入需要修改的参数。")
            continue

        print_final_rotation_multi_shot_params(args)
        return MODE2_RUN, args


def configure_rotation_multi_shot_args(args):
    """模式2参数确认子菜单"""
    while True:
        print_rotation_multi_shot_params(args)
        action = input("请输入操作 id: ").strip()

        if action == "0":
            return MODE2_BACK_TO_MODE_SELECT, args

        if action == "1":
            print_final_rotation_multi_shot_params(args)
            return MODE2_RUN, args

        if action == "2":
            status, args = prompt_rotation_multi_shot_overrides(args)
            if status == MODE2_RUN:
                return MODE2_RUN, args
            continue

        print("输入无效，请输入 0、1 或 2。")


def run_rotation_multi_shot(args):
    """模式2：旋转多轮拍摄"""
    status, args = configure_rotation_multi_shot_args(args)
    if status == MODE2_BACK_TO_MODE_SELECT:
        return MODE_BACK_TO_MODE_SELECT

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


def build_program_modes():
    """构建程序模式表"""
    return {
        MODE_EXIT: {
            "name": "退出程序",
            "handler": None,
        },
        MODE_FOCUS_RANGE_CALIBRATION: {
            "name": "对焦范围标定",
            "handler": run_focus_range_calibration,
        },
        MODE_ROTATION_MULTI_SHOT: {
            "name": "旋转多轮拍摄",
            "handler": run_rotation_multi_shot,
        },
    }


def main():
    """主函数"""
    args = parse_args()

    # 设置日志级别
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    modes = build_program_modes()

    try:
        while True:
            selected_mode = prompt_program_mode(modes)
            if selected_mode == MODE_EXIT:
                print("已选择退出程序。")
                return 0

            mode = modes[selected_mode]
            result = mode["handler"](args)

            if result == MODE_BACK_TO_MODE_SELECT:
                continue

            return result

    except KeyboardInterrupt:
        print("\n\n用户中断操作")
        return 130


if __name__ == '__main__':
    sys.exit(main())
