"""
旋转拍摄控制命令行入口
"""

import sys
import argparse
import json
import logging
import os
import time
from datetime import datetime

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

# Lens map by default for each camera
ANGLE_START = 0.0      # 起始端角度(靠近默认0点)
ANGLE_END   = 2900.0   # 终点端角度

# Reference files
LENS_RANGE_MAP_PATH = os.path.join("configs", "lens_range_map.json")
FOCAL_CALI_LOG_DIR = os.path.join("log", "focal_cali")
FULL_SHOT_LOG_DIR = os.path.join("log", "full_shot")

# Constant tolerances
ANGLE_VERIFY_TOLERANCE = 5.0
ZERO_VERIFY_TOLERANCE = 1.0

# Utils
def generate_step_angles(steps, angle_start=ANGLE_START, angle_end=ANGLE_END):
    """根据步进次数，自动计算均分的物理角度列表"""
    if steps <= 0: return []
    if steps == 1: return [round(angle_start, 2)]
    angles = []
    step_interval = (angle_end - angle_start) / (steps - 1)
    for i in range(steps):
        angles.append(round(angle_start + (i * step_interval), 2))
    return angles


def decode_sdk_text(value):
    if isinstance(value, bytes):
        return value.decode("gbk", errors="ignore")
    return str(value)


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_parent_dir(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def default_lens_record(camera_name, serial_port):
    return {
        "serial_port": serial_port,
        "angle_start": ANGLE_START,
        "angle_end": ANGLE_END,
        "calibrated": False,
        "source": "pending",
        "samples": [],
    }


def create_default_lens_map(camera_infos):
    from ImageNode import CAMERA_COM_MAP

    return {
        "generated_at": timestamp(),
        "default_angle_start": ANGLE_START,
        "default_angle_end": ANGLE_END,
        "lens_ranges": {
            info["camera_name"]: default_lens_record(
                info["camera_name"],
                CAMERA_COM_MAP.get(info["camera_name"]),
            )
            for info in camera_infos
        },
    }


def load_lens_range_map(path=LENS_RANGE_MAP_PATH):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_lens_range_map(data, path=LENS_RANGE_MAP_PATH):
    ensure_parent_dir(path)
    data["updated_at"] = timestamp()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def merge_lens_map_with_current_cameras(data, camera_infos):
    from ImageNode import CAMERA_COM_MAP

    data.setdefault("generated_at", timestamp())
    data.setdefault("default_angle_start", ANGLE_START)
    data.setdefault("default_angle_end", ANGLE_END)
    lens_ranges = data.setdefault("lens_ranges", {})

    for info in camera_infos:
        camera_name = info["camera_name"]
        if camera_name not in lens_ranges:
            lens_ranges[camera_name] = default_lens_record(camera_name, CAMERA_COM_MAP.get(camera_name))
            continue

        lens_ranges[camera_name].setdefault("serial_port", CAMERA_COM_MAP.get(camera_name))
        lens_ranges[camera_name].setdefault("angle_start", ANGLE_START)
        lens_ranges[camera_name].setdefault("angle_end", ANGLE_END)
        lens_ranges[camera_name].setdefault("calibrated", False)
        lens_ranges[camera_name].setdefault("source", "pending")
        lens_ranges[camera_name].setdefault("samples", [])

    return data


def enumerate_connected_cameras():
    from Device import IMV_DeviceList, IMV_EInterfaceType, IMV_OK, MvCamera

    device_list = IMV_DeviceList()
    ret = MvCamera.IMV_EnumDevices(device_list, IMV_EInterfaceType.interfaceTypeGige)
    if ret != IMV_OK or device_list.nDevNum == 0:
        print("❌ 枚举设备失败或未找到相机。")
        return []

    camera_infos = []
    for idx in range(device_list.nDevNum):
        cam_info = device_list.pDevInfo[idx]
        camera_infos.append({
            "index": idx,
            "camera_name": decode_sdk_text(cam_info.cameraName),
            "camera_key": decode_sdk_text(cam_info.cameraKey),
        })
    return camera_infos


def log_event(log_events, message):
    line = f"{timestamp()} - {message}"
    log_events.append(line)
    print(message)


def write_calibration_log(log_events, state):
    os.makedirs(FOCAL_CALI_LOG_DIR, exist_ok=True)
    log_path = os.path.join(
        FOCAL_CALI_LOG_DIR,
        f"lens_range_calibration_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
    )
    with open(log_path, "w", encoding="utf-8") as f:
        for line in log_events:
            f.write(line + "\n")
        f.write("\nFinal lens range map:\n")
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"📝 标定日志已保存: {log_path}")
    return log_path


def print_lens_map_status(state, camera_infos):
    lens_ranges = state.get("lens_ranges", {})
    total = len(camera_infos)
    completed = 0
    print("\n当前 Lens map 状态:")
    for info in camera_infos:
        name = info["camera_name"]
        record = lens_ranges.get(name, {})
        calibrated = bool(record.get("calibrated"))
        if calibrated:
            completed += 1
        source = record.get("source", "pending")
        start = record.get("angle_start", ANGLE_START)
        end = record.get("angle_end", ANGLE_END)
        serial_port = record.get("serial_port")
        flag = "完成" if calibrated else "未完成"
        print(f"  - {name}: {flag}, source={source}, COM={serial_port}, range=[{start}, {end}]")
    print(f"进度: {completed}/{total}\n")


def connected_camera_names(camera_infos):
    return {info["camera_name"] for info in camera_infos}


def completed_camera_count(state, camera_infos):
    lens_ranges = state.get("lens_ranges", {})
    return sum(
        1 for info in camera_infos
        if lens_ranges.get(info["camera_name"], {}).get("calibrated")
    )


def normalize_internal_angle(angle):
    if angle is None:
        return None
    if -1.0 <= angle < 0.0:
        return 0.0
    if angle < -1.0:
        raise ValueError(f"内部角度为异常负数: {angle:.2f}°")
    return round(angle, 2)


def cleanup_lens_registry(lens_registry, log_events=None):
    while lens_registry:
        lens = lens_registry.pop()
        try:
            message = f"清理镜头 {lens.port}: 上电找 0 并关闭串口"
            if log_events is not None:
                log_event(log_events, message)
            else:
                print(message)
            lens.retract_lens_for_shutdown()
        except Exception as exc:
            if log_events is not None:
                log_event(log_events, f"镜头 {getattr(lens, 'port', '?')} 清理失败: {exc}")
            else:
                print(f"镜头 {getattr(lens, 'port', '?')} 清理失败: {exc}")
        finally:
            try:
                lens.close()
            except Exception:
                pass


def reset_record_to_pending(state, camera_name):
    record = state["lens_ranges"][camera_name]
    serial_port = record.get("serial_port")
    state["lens_ranges"][camera_name] = default_lens_record(camera_name, serial_port)


def set_record_to_default(state, camera_name):
    record = state["lens_ranges"][camera_name]
    record.update({
        "angle_start": ANGLE_START,
        "angle_end": ANGLE_END,
        "calibrated": True,
        "source": "default",
        "samples": [ANGLE_START, ANGLE_END],
    })


def set_record_to_measured(state, camera_name, samples):
    record = state["lens_ranges"][camera_name]
    angle_start = min(samples)
    angle_end = max(samples)
    record.update({
        "angle_start": round(angle_start, 2),
        "angle_end": round(angle_end, 2),
        "calibrated": True,
        "source": "measured",
        "samples": [round(samples[0], 2), round(samples[1], 2)],
    })

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

镜头范围:
  默认读取 configs/lens_range_map.json。
  每台相机使用自己条目中的 angle_start/angle_end 生成镜头步进。
  如果 JSON 不存在、相机缺少条目或条目未完成，会提示并使用默认范围 0.0/2900.0。
  source="default" 的条目会作为可用范围使用，并在控制台提示。

输出:
  图片输出到 Output/{camera.user_id}/Position{i}_Step{j}.bmp
  每个 step 的相机目标角度写入 log/full_shot/{run_timestamp}/Position{i}_Step{j}_metadata.json。

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

功能:
  为每台相机对应镜头建立独立焦距范围，并写入 configs/lens_range_map.json。
  本命令不打开相机拉流；请先在外部工业相机软件中打开待调试相机并观察画面。
  标定日志保存到 log/focal_cali/。

启动前准备:
  1. 确认相机和镜头已连接。
  2. 确认 ImageNode.py 中 CAMERA_COM_MAP 的相机 name 和 COM 口映射正确。
  3. 在外部相机软件中打开待标定相机并拉流。

启动阶段命令:
  reuse               沿用已有 JSON 中已完成条目，只补齐新增或缺失相机。
  reset               覆盖旧 JSON，按默认范围 0.0/2900.0 重新创建所有条目。
  back                不进入本次标定，返回命令行。

相机 name 输入阶段:
  相机name            进入该相机/镜头的核对阶段。
  status              显示当前标定进度和每台相机范围。
  redo 相机name       重新标定某个已完成相机。
  back                保存当前 JSON 状态，清理已登记镜头后退出。

相机核对阶段:
  ok                  确认外部拉流画面与输入相机一致，进入实测调焦。
  default             不打开镜头，直接把默认范围 0.0/2900.0 写为可用标定结果。
  cancel              取消当前相机，返回相机 name 输入阶段。

单镜头调焦阶段:
  float角度           相对当前位置继续旋转，可正可负。推荐先 1000，再 100，最后 10。
  ok                  接受当前电机内部绝对角度作为当前端点。
  home                让当前镜头回到 0 点并重新读取角度。
  status              查看当前相机本轮已确认的端点 samples；未确认端点时显示默认参考范围。
  cancel              取消当前相机本轮标定，JSON 条目回退为默认未完成状态。

完成后命令:
  status              查看最终结果。
  redo 相机name       重新标定指定相机。
  back                退出当前 command。

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


def choose_initial_lens_map(camera_infos):
    if not os.path.exists(LENS_RANGE_MAP_PATH):
        state = create_default_lens_map(camera_infos)
        save_lens_range_map(state)
        print(f"已创建新的 Lens map: {LENS_RANGE_MAP_PATH}")
        return state

    while True:
        choice = input(
            f"检测到已有 {LENS_RANGE_MAP_PATH}，请输入 reuse 沿用、reset 重建、back 退出: "
        ).strip().lower()

        if choice == "back":
            return None

        if choice == "reset":
            state = create_default_lens_map(camera_infos)
            save_lens_range_map(state)
            print("已按当前相机列表重建 Lens map。")
            return state

        if choice == "reuse":
            try:
                state = load_lens_range_map()
            except Exception as exc:
                print(f"读取旧 JSON 失败: {exc}")
                continue
            state = merge_lens_map_with_current_cameras(state, camera_infos)
            save_lens_range_map(state)
            print("已沿用旧 Lens map，并补齐当前相机缺失条目。")
            return state

        print("输入无效，请输入 reuse、reset 或 back。")


def print_camera_reference(camera_info, state):
    camera_name = camera_info["camera_name"]
    record = state["lens_ranges"].get(camera_name, {})
    print("\n请核对当前相机信息:")
    print(f"  cameraName : {camera_name}")
    print(f"  cameraKey  : {camera_info.get('camera_key')}")
    print(f"  COM        : {record.get('serial_port')}")
    print(f"  JSON range : [{record.get('angle_start')}, {record.get('angle_end')}]")
    print(f"  calibrated : {record.get('calibrated')} / source={record.get('source')}")
    print("如果外部拉流画面不是该相机，请输入 cancel 返回。")


def boundary_hint(status):
    try:
        from LensController import LENS_BOUNDARY_ZERO, LENS_BOUNDARY_FAR
    except Exception:
        LENS_BOUNDARY_ZERO = 0xF5
        LENS_BOUNDARY_FAR = 0x0B

    if status == LENS_BOUNDARY_ZERO:
        return "已抵达零点边界，请输入正的相对角度离开边界。"
    if status == LENS_BOUNDARY_FAR:
        return "已抵达远端边界，请输入负的相对角度离开边界。"
    return f"检测到边界状态: 0x{status:02X}"


def validate_zero_angle(real_angle):
    normalized = normalize_internal_angle(real_angle)
    if normalized is None:
        raise ValueError("无法读取内部角度")
    if abs(normalized) > ZERO_VERIFY_TOLERANCE:
        raise ValueError(f"归零后内部角度不在 0 点附近: {normalized:.2f}°")
    return normalized


def print_current_calibration_status(
    camera_name,
    endpoint_idx,
    samples,
    current_internal_angle,
    default_samples,
):
    print(f"\n[{camera_name}] current calibration status")
    print(f"  endpoint : {endpoint_idx}/2")
    if current_internal_angle is None:
        print("  current  : no valid internal angle yet")
    else:
        print(f"  current  : {current_internal_angle:.2f}°")

    if samples:
        formatted_samples = ", ".join(f"{sample:.2f}" for sample in samples)
        print(f"  samples  : [{formatted_samples}]")
        if len(samples) == 1:
            print("  pending  : 1 endpoint remaining")
    else:
        formatted_defaults = ", ".join(f"{sample:.2f}" for sample in default_samples)
        print("  samples  : none confirmed in this round")
        print(f"  default  : [{formatted_defaults}]")
    print()


def classify_calibration_move(
    start_angle,
    relative_delta,
    expected_angle,
    measured_angle,
    status_code,
    result,
):
    try:
        from LensController import LENS_BOUNDARY_ZERO
    except Exception:
        LENS_BOUNDARY_ZERO = 0xF5

    if measured_angle is None:
        return {
            "kind": "normal",
            "corrected_angle": None,
            "corrected_expected_angle": expected_angle,
            "message": "",
        }

    if result.get("boundary"):
        kind = "zero_boundary" if status_code == LENS_BOUNDARY_ZERO else "far_boundary"
        status_text = f"0x{status_code:02X}" if isinstance(status_code, int) else str(status_code)
        return {
            "kind": kind,
            "corrected_angle": measured_angle,
            "corrected_expected_angle": measured_angle,
            "message": f"{boundary_hint(status_code)} raw_status={status_text}",
        }

    if (
        relative_delta < 0
        and expected_angle < 0
        and abs(measured_angle) <= ZERO_VERIFY_TOLERANCE
    ):
        return {
            "kind": "zero_boundary",
            "corrected_angle": measured_angle,
            "corrected_expected_angle": measured_angle,
            "message": (
                f"Reached zero boundary; corrected current angle to {measured_angle:.2f}°. "
                "Enter a positive relative angle to leave the boundary, or ok to accept this endpoint."
            ),
        }

    return {
        "kind": "normal",
        "corrected_angle": measured_angle,
        "corrected_expected_angle": expected_angle,
        "message": "",
    }


def calibrate_single_lens(camera_name, com_port, lens_registry, log_events):
    from LensController import LensController, COORDINATE_MODE_HARDWARE

    lens = LensController(port=com_port, coordinate_mode=COORDINATE_MODE_HARDWARE)
    samples = []
    current_internal_angle = None

    if not lens.open():
        return {"status": "failed", "reason": f"无法打开串口 {com_port}"}

    lens_registry.append(lens)
    log_event(log_events, f"[{camera_name}] 已打开镜头串口 {com_port}，进入标定")

    try:
        if not lens.initialize_lens():
            return {"status": "failed", "reason": "镜头初始化/归零失败"}

        status, real_angle = lens.read_motor_status_and_position()
        current_internal_angle = validate_zero_angle(real_angle)
        lens.current_angle = current_internal_angle
        print(f"✅ [{camera_name}] 归零验证通过，当前内部绝对角度: {current_internal_angle:.2f}°")

        # 调试用途：归零后如需手动启用硬件清零，可临时取消下一行注释。
        # lens.set_current_position_as_hardware_zero()

        for endpoint_idx in range(1, 3):
            print(f"\n[{camera_name}] 开始标定端点 {endpoint_idx}/2。")
            print("请输入相对当前位置的角度，可正可负；推荐调试步长 1000、100、10。")

            while True:
                raw = input(f"[{camera_name}] 端点 {endpoint_idx} 输入角度 / ok / home / status / cancel: ").strip()
                lowered = raw.lower()

                if lowered == "cancel":
                    return {"status": "cancelled", "reason": "用户取消当前镜头标定"}

                if lowered == "home":
                    if not lens.return_to_home():
                        return {"status": "failed", "reason": "home 回 0 失败"}
                    status, real_angle = lens.read_motor_status_and_position()
                    current_internal_angle = validate_zero_angle(real_angle)
                    lens.current_angle = current_internal_angle
                    print(f"✅ 已回到 0 点，当前内部绝对角度: {current_internal_angle:.2f}°")
                    continue

                if lowered == "status":
                    print_current_calibration_status(
                        camera_name=camera_name,
                        endpoint_idx=endpoint_idx,
                        samples=samples,
                        current_internal_angle=current_internal_angle,
                        default_samples=[ANGLE_START, ANGLE_END],
                    )
                    continue

                if lowered == "ok":
                    if current_internal_angle is None:
                        print("尚未读取到有效内部角度，请先输入相对角度移动或 home。")
                        continue
                    samples.append(current_internal_angle)
                    print(f"✅ 已缓存端点 {endpoint_idx}: {current_internal_angle:.2f}°")
                    log_event(log_events, f"[{camera_name}] 已缓存端点 {endpoint_idx}: {current_internal_angle:.2f}°")
                    break

                try:
                    relative_delta = float(raw)
                except ValueError:
                    print("输入无效：请输入 float 相对角度，或 ok/home/status/cancel。")
                    continue

                start_internal_angle = current_internal_angle
                expected_target_angle = start_internal_angle + relative_delta
                result = lens.move_relative_for_calibration(relative_delta)

                try:
                    measured_angle = normalize_internal_angle(result.get("real_angle"))
                except ValueError as exc:
                    return {"status": "failed", "reason": str(exc)}

                status_code = result.get("status")
                if measured_angle is not None:
                    current_internal_angle = measured_angle
                    lens.current_angle = measured_angle
                    print(f"当前电机内部绝对角度: {measured_angle:.2f}°")
                else:
                    print(f"当前电机内部绝对角度读取失败，状态: {status_code}")

                classification = classify_calibration_move(
                    start_angle=start_internal_angle,
                    relative_delta=relative_delta,
                    expected_angle=expected_target_angle,
                    measured_angle=measured_angle,
                    status_code=status_code,
                    result=result,
                )
                if classification["kind"] in ("zero_boundary", "far_boundary"):
                    corrected_angle = classification["corrected_angle"]
                    if corrected_angle is not None:
                        current_internal_angle = corrected_angle
                        lens.current_angle = corrected_angle
                    expected_target_angle = classification["corrected_expected_angle"]
                    print(f"⚠️ {classification['message']}")
                    log_event(
                        log_events,
                        f"[{camera_name}] boundary: kind={classification['kind']}, "
                        f"status={status_code}, expected={expected_target_angle}, angle={corrected_angle}",
                    )
                    continue

                if result.get("boundary"):
                    print(f"⚠️ {boundary_hint(status_code)} 原始状态码=0x{status_code:02X}")
                    log_event(log_events, f"[{camera_name}] 碰壁: status=0x{status_code:02X}, angle={measured_angle}")
                    continue

                if not result.get("ok"):
                    return {"status": "failed", "reason": f"镜头移动失败: {result.get('error')}, status={status_code}"}

                if measured_angle is None:
                    return {"status": "failed", "reason": "镜头停稳后未读取到内部角度"}

                deviation = abs(measured_angle - expected_target_angle)
                if deviation > ANGLE_VERIFY_TOLERANCE:
                    return {
                        "status": "failed",
                        "reason": (
                            f"预期角度与内部角度偏差过大: "
                            f"expected={expected_target_angle:.2f}°, actual={measured_angle:.2f}°"
                        ),
                    }

        return {"status": "success", "samples": samples}

    except Exception as exc:
        return {"status": "failed", "reason": str(exc)}

    finally:
        cleanup_lens_registry(lens_registry, log_events)


def handle_camera_calibration(camera_name, camera_infos_by_name, state, lens_registry, log_events):
    from ImageNode import CAMERA_COM_MAP

    camera_info = camera_infos_by_name[camera_name]
    record = state["lens_ranges"][camera_name]
    com_port = CAMERA_COM_MAP.get(camera_name) or record.get("serial_port")

    print_camera_reference(camera_info, state)

    while True:
        action = input(f"[{camera_name}] 输入 ok 开始实测、default 使用默认范围、cancel 返回: ").strip().lower()

        if action == "cancel":
            log_event(log_events, f"[{camera_name}] 用户在相机核对阶段取消")
            return "cancelled"

        if action == "default":
            set_record_to_default(state, camera_name)
            save_lens_range_map(state)
            log_event(log_events, f"[{camera_name}] 使用默认范围 0.0/2900.0 并标记为可用")
            return "completed"

        if action == "ok":
            if not com_port:
                print(f"相机 {camera_name} 没有 COM 映射，无法控制镜头。")
                return "failed"

            result = calibrate_single_lens(camera_name, com_port, lens_registry, log_events)
            if result["status"] == "success":
                set_record_to_measured(state, camera_name, result["samples"])
                save_lens_range_map(state)
                log_event(
                    log_events,
                    f"[{camera_name}] 标定完成: START={state['lens_ranges'][camera_name]['angle_start']}, "
                    f"END={state['lens_ranges'][camera_name]['angle_end']}",
                )
                return "completed"

            reset_record_to_pending(state, camera_name)
            save_lens_range_map(state)
            log_event(log_events, f"[{camera_name}] 本轮标定失效: {result.get('reason')}")
            print(f"⚠️ [{camera_name}] 本轮标定失效: {result.get('reason')}")
            return result["status"]

        print("输入无效，请输入 ok、default 或 cancel。")


def run_completion_loop(camera_infos, camera_infos_by_name, state, lens_registry, log_events):
    while True:
        command = input("全部条目已完成。请输入 status、redo 相机name 或 back 退出: ").strip()
        lowered = command.lower()

        if lowered == "status":
            print_lens_map_status(state, camera_infos)
            continue

        if lowered == "back":
            cleanup_lens_registry(lens_registry, log_events)
            write_calibration_log(log_events, state)
            return 0

        if lowered.startswith("redo "):
            camera_name = command[5:].strip()
            if camera_name not in camera_infos_by_name:
                print(f"相机 {camera_name} 不在当前枚举列表中。")
                continue
            reset_record_to_pending(state, camera_name)
            save_lens_range_map(state)
            handle_camera_calibration(camera_name, camera_infos_by_name, state, lens_registry, log_events)
            if completed_camera_count(state, camera_infos) < len(camera_infos):
                return None
            continue

        print("输入无效，请输入 status、redo 相机name 或 back。")


def run_focus_range_calibration(args):
    """交互式焦距范围标定入口"""
    print("\n已选择：对焦范围标定")
    log_events = []
    lens_registry = []

    try:
        camera_infos = enumerate_connected_cameras()
        if not camera_infos:
            return 1

        state = choose_initial_lens_map(camera_infos)
        if state is None:
            print("已退出本次标定。")
            return 0

        camera_infos_by_name = {info["camera_name"]: info for info in camera_infos}
        valid_names = connected_camera_names(camera_infos)

        print("\n已识别相机/镜头映射:")
        print_lens_map_status(state, camera_infos)

        while True:
            if completed_camera_count(state, camera_infos) == len(camera_infos):
                print("✅ 当前连接相机已全部完成焦距范围标定。")
                result = run_completion_loop(camera_infos, camera_infos_by_name, state, lens_registry, log_events)
                if result is not None:
                    return result
                continue

            command = input("请输入外部软件正在拉流的相机 name，或 status / redo 相机name / back: ").strip()
            lowered = command.lower()

            if lowered == "status":
                print_lens_map_status(state, camera_infos)
                continue

            if lowered == "back":
                cleanup_lens_registry(lens_registry, log_events)
                write_calibration_log(log_events, state)
                return 0

            if lowered.startswith("redo "):
                camera_name = command[5:].strip()
                if camera_name not in valid_names:
                    print(f"相机 {camera_name} 不在当前枚举列表中。")
                    continue
                reset_record_to_pending(state, camera_name)
                save_lens_range_map(state)
                handle_camera_calibration(camera_name, camera_infos_by_name, state, lens_registry, log_events)
                continue

            camera_name = command
            if camera_name not in valid_names:
                print(f"相机 name 不存在: {camera_name}")
                continue

            record = state["lens_ranges"].get(camera_name)
            if not record:
                print(f"Lens map 缺少相机条目: {camera_name}")
                continue

            if not record.get("serial_port"):
                print(f"相机 {camera_name} 无串口映射，请检查 CAMERA_COM_MAP。")
                continue

            if record.get("calibrated"):
                print(f"相机 {camera_name} 已完成标定。如需重做，请输入: redo {camera_name}")
                continue

            handle_camera_calibration(camera_name, camera_infos_by_name, state, lens_registry, log_events)

    except KeyboardInterrupt:
        print("\n用户中断焦距标定。")
        log_event(log_events, "用户 KeyboardInterrupt 中断焦距标定")
        cleanup_lens_registry(lens_registry, log_events)
        if 'state' in locals() and state:
            write_calibration_log(log_events, state)
        return 130

    except Exception as exc:
        logger.error(f"焦距标定异常: {exc}")
        print(f"焦距标定异常: {exc}")
        log_event(log_events, f"焦距标定异常: {exc}")
        cleanup_lens_registry(lens_registry, log_events)
        if 'state' in locals() and state:
            write_calibration_log(log_events, state)
        return 1

    finally:
        cleanup_lens_registry(lens_registry, log_events)


def run_rotation_multi_shot(args):
    """旋转多轮拍摄"""
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

        lens_range_map = load_lens_range_map() or {}
        lens_ranges = lens_range_map.get("lens_ranges", {})
        per_lens_step_angles = {}

        for node in sys_dev.nodes:
            cam_name = node.camera.m_userId
            record = lens_ranges.get(cam_name)

            if not record:
                print(f"⚠️ [{cam_name}] 未找到 Lens map 条目，使用默认范围 {ANGLE_START}/{ANGLE_END}。")
                angle_start, angle_end = ANGLE_START, ANGLE_END
            elif not record.get("calibrated"):
                print(f"⚠️ [{cam_name}] 未完成标定，使用默认范围 {ANGLE_START}/{ANGLE_END}。")
                angle_start, angle_end = ANGLE_START, ANGLE_END
            else:
                angle_start = float(record.get("angle_start", ANGLE_START))
                angle_end = float(record.get("angle_end", ANGLE_END))
                if record.get("source") == "default":
                    print(f"ℹ️ [{cam_name}] 使用默认焦距范围条目: {angle_start}/{angle_end}。")

            per_lens_step_angles[cam_name] = generate_step_angles(
                args.lens_steps,
                angle_start=angle_start,
                angle_end=angle_end,
            )

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
        output_root = os.path.abspath("./Output")
        run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        command_log_dir = FULL_SHOT_LOG_DIR
        run_log_dir = os.path.join(command_log_dir, run_timestamp)
        os.makedirs(run_log_dir, exist_ok=True)

        def progress_callback(current, total):
            print(f"\n" + "="*55)
            print(f"🔄 转台进度: {current}/{total} | 转台已就位，相机接管控制权...")
            print("="*55)

            # 执行相机变焦与拍摄逻辑
            for step_idx in range(args.lens_steps):
                target_angles_by_camera = {
                    cam_name: step_angles[step_idx]
                    for cam_name, step_angles in per_lens_step_angles.items()
                    if step_idx < len(step_angles)
                }
                print(f"\n 🎯 [阵位 {current} - 镜头步进 {step_idx + 1}/{args.lens_steps}]")
                for cam_name, target_angle in target_angles_by_camera.items():
                    print(f"    [{cam_name}] 目标角度: {target_angle}°")

                # 驱动镜头
                # sys_dev.serial_move_lenses_by_camera(target_angles_by_camera)
                sys_dev.parallel_move_lenses_by_camera(target_angles_by_camera)

                # 镜头停稳后拍照；图片按相机目录保存，step metadata 写入 log
                os.makedirs(run_log_dir, exist_ok=True)
                metadata_path = os.path.join(
                    run_log_dir,
                    f"Position{current}_Step{step_idx + 1}_metadata.json",
                )
                with open(metadata_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "position": current,
                        "step_index": step_idx + 1,
                        "lens_steps": args.lens_steps,
                        "target_angles_by_camera": target_angles_by_camera,
                        "lens_range_map_path": LENS_RANGE_MAP_PATH,
                        "image_path_pattern": "Output/{camera_user_id}/Position{i}_Step{j}.bmp",
                        "generated_at": timestamp(),
                    }, f, ensure_ascii=False, indent=2)
                # sys_dev.serial_snap_rotation_step(output_root, current, step_idx + 1)
                sys_dev.parallel_snap_rotation_step(output_root, current, step_idx + 1)

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
