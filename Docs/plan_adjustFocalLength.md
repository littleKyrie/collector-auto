# Adjust Focal Length Plan

## 目标

为每一个镜头建立独立的 Lens map，使每个镜头都有自己的 `ANGLE_START` 和 `ANGLE_END`。当前 `main.py` 已改为 command 控制台模式，因此焦距标定应实现为 `python main.py --focus_calibration` 这一条命令下的交互式会话：按已连接相机数量创建镜头标定实例，逐个完成每台相机对应镜头的两端焦距角度标定，最后写出固定 JSON 状态文件和日志并结束该命令。

当前 `main.py` 只有一组全局常量：

```python
ANGLE_START = 0.0
ANGLE_END = 2900.0
```

后续需要替换为“按镜头/相机 id 管理”的结构，并让 `--full_shot` 的拍摄步进使用对应镜头自己的范围，而不是全局范围。

## 已确认的现有基础

1. `ImageNode.py` 已有 `CAMERA_COM_MAP`，通过相机 `cameraName` 映射到镜头串口。
2. `Device.py` 的 `CameraDevice.init()` 会把 SDK 枚举到的 `camInfo.cameraName` 保存为 `m_userId`。
3. `LensController.py` 已有：
   - 上电找 0：`LENS_POWER_ON_HOMING_COMMAND`
   - 点击回 0：`LENS_RETURN_HOME_COMMAND`
   - 读取状态和内部角度：`read_motor_status_and_position()`
   - 软件坐标模式：`software`
   - 硬件坐标模式：`hardware`
   - 硬件清零函数：`set_current_position_as_hardware_zero()`
   - 清零调用点目前已按调试用途保留为注释
4. `main.py` 当前使用 command 控制台入口：
   - `python main.py --full_shot` 调用 `handle_full_shot_cli()` 和 `run_rotation_multi_shot(args)`。
   - `python main.py --focus_calibration` 调用 `handle_focus_calibration_cli()` 和 `run_focus_range_calibration(None)`。
   - `run_focus_range_calibration(args)` 目前仍是占位函数。
5. `MVSDK/IMVApi.py` 暴露了 `IMV_OpenEx()` 和 `IMV_GIGE_GetAccessPermission()`；`MVSDK/IMVDefines.py` 里已有 `IMV_ECameraAccessPermission` 枚举。

## MVSDK 只读约束

`./MVSDK` 目录下的所有文件都视为官方 SDK 基准文件，默认绝对正确无误。

约束：

1. 不修改 `./MVSDK` 目录下的任何文件，包括 `IMVApi.py`、`IMVDefines.py` 和其他 SDK 文件。
2. 不在计划实现中 patch、重写、格式化或替换 SDK 文件。
3. 一切 SDK 相关调用以 `./MVSDK` 当前提供的接口和定义为基准。
4. 如果业务层需要更友好的调用方式，只能在 `Device.py`、`main.py` 或新增的非 SDK 模块中封装 wrapper/adapter。
5. 如果发现 SDK 调用行为和预期不一致，只记录现象和返回值，不直接修改 SDK 文件。

## Command 入口适配

当前不再使用旧的交互式模式选择菜单，而是由 `main(argv)` 根据第一个命令参数分派：

```text
python main.py --focus_calibration
python main.py --focus_calibration --help
python main.py --full_shot
python main.py --full_shot --help
```

焦距标定实现要求：

1. `handle_focus_calibration_cli(argv)` 继续只接受 `-h/--help`，普通执行时调用 `run_focus_range_calibration(None)`。
2. `run_focus_range_calibration(None)` 内部负责完整标定会话，不再返回旧的“模式选择界面”。
3. 标定完成全部镜头后不直接退出，而是进入完成后命令循环；用户输入 `back` 后才返回 `0`。正常情况下每个镜头在单镜头流程结束时已经完成“上电找 0 + 关闭串口 + 注销”，因此顶层注册表应为空。
4. 用户在顶层命令中断，例如 `KeyboardInterrupt`，返回非零或 `130`；下次重新启动标定时提示用户选择 `reuse/reset/back`，不再无条件重新初始化 JSON。
5. 顶层相机 name 输入阶段只保留 `back` 作为退出命令：退出前先清理注册表中仍残留的镜头，逐个执行上电找 0、关闭串口并注销，然后结束当前 `--focus_calibration` 命令会话。
6. 用户在单个镜头内输入 `cancel` 只取消当前相机本轮标定，不退出 `--focus_calibration` 命令会话；当前镜头仍必须执行上电找 0、关闭串口并注销。
7. `print_focus_calibration_help()` 需要从“当前功能尚未实现”更新为真实说明，列出 `status/back/cancel/home/ok` 等交互命令含义，并区分顶层 `back` 和单镜头调焦阶段的 `cancel`。
8. `run_focus_range_calibration()` 必须有顶层 `try/finally` 退出保护：只要 Python 仍有机会执行清理逻辑，就尽力清理注册表中残留的镜头，逐个执行上电找 0、关闭串口并注销。

### Help 模式输出要求

进入当前 command 对应的 help 模式时，只输出用户操作说明，不打开相机、不打开串口、不读写 JSON、不创建日志文件，也不执行任何镜头动作。help 返回值应为 `0`。

`python main.py --focus_calibration --help` 和 `python main.py --focus_calibration -h` 需要输出以下内容：

1. 程序大致功能：
   - 为每台相机对应镜头建立独立焦距范围。
   - 标定结果写入 `configs/lens_range_map.json`。
   - 标定日志保存到 `log/focal_cali/`。
   - 本程序不打开相机拉流，相机画面由外部工业相机软件打开供人工观察。
2. 启动前准备：
   - 确认相机和镜头已连接。
   - 确认 `CAMERA_COM_MAP` 中相机 name 与 COM 口映射正确。
   - 在外部相机软件中打开待调试相机并拉流。
   - `./MVSDK` 为官方 SDK 基准目录，不修改其中任何文件。
3. 启动阶段参数和命令：
   - `reuse`：沿用已有 `configs/lens_range_map.json` 中已完成条目，只补齐新增或缺失相机。
   - `reset`：覆盖旧 JSON，按默认范围 `0.0/2900.0` 重新创建所有条目。
   - `back`：不进入本次标定，返回命令行。
4. 相机 name 输入阶段：
   - 输入相机 name：进入该相机/镜头的核对阶段。
   - `status`：显示当前标定进度、已完成条目、默认条目和未完成条目。
   - `redo 相机name`：重新标定某个已完成相机，尤其用于全部完成后的返工。
   - `back`：保存当前 JSON 状态，清理已登记镜头后退出当前 command。
5. 相机核对阶段：
   - 程序输出相机 name、可读 cameraKey、COM 口、JSON 当前范围等参考信息。
   - `ok`：确认外部拉流画面与输入相机一致，进入实测调焦。
   - `default`：不打开镜头，直接把默认范围 `0.0/2900.0` 写为可用标定结果。
   - `cancel`：发现输入相机与外部拉流不一致时，取消当前相机并返回相机 name 输入阶段。
6. 单镜头调焦阶段：
   - 输入 float 数值：表示相对当前位置继续旋转的角度，可正可负。
   - 推荐调试步长：先用 `1000` 粗调，再用 `100` 中调，最后用 `10` 微调。
   - 每次移动后，控制台输出电机内部读取到的真实绝对角度。
   - `ok`：接受当前内部绝对角度作为当前端点。
   - `home`：让当前镜头回到 0 点并重新读取角度，用于现场复核。
   - `cancel`：取消当前相机本轮标定，当前 JSON 条目回退为默认未完成状态，镜头上电找 0、关闭串口并注销后返回相机 name 输入阶段。
7. 完成后命令阶段：
   - 全部相机完成后不立即退出，而是显示完成摘要。
   - `status`：查看最终结果。
   - `redo 相机name`：重新标定指定相机。
   - `back`：确认退出当前 command。

`python main.py --full_shot --help` 需要输出旋转拍摄命令的用户说明：

1. 程序大致功能：
   - 读取 `configs/lens_range_map.json` 中每个镜头自己的 `angle_start/angle_end`。
   - 按每个镜头专属范围生成拍摄步进角度。
   - 同一步中允许不同镜头移动到不同目标角度。
2. 启动前检查：
   - 如果 JSON 不存在或存在未完成条目，输出明显警告。
   - `source="default"` 的条目可以使用，但需要提示该相机沿用默认范围。
3. 输出位置：
   - 图片输出到 `Output/Position_{current}/Step_{step_idx + 1}`。
   - 每个 step 的具体相机角度写入 metadata JSON 或日志。

help 文本只面向现场操作，不写程序实现细节；用户应能仅凭该说明完成启动、选择 JSON 策略、输入相机 name、核对相机、调焦、确认端点、取消当前标定、查看状态、重做和退出。

## SDK 占用相机调试辅助

`--focus_calibration` 当前只保留人工操作模式：用户先在外部工业相机软件中打开某台相机拉流，然后在本程序中输入对应相机 name，程序再进入该相机对应镜头的焦距标定流程。

外部拉流相机和程序输入相机是否一致，主要由人工确认：用户在外部软件里看画面、在程序里输入同一个相机 name。SDK 权限查询只作为可选调试辅助，不作为普通流程的强制门禁，也不作为自动选择相机的入口。

从当前 SDK 封装看，可保留如下调试路径：

1. `--focus_calibration` 进入后只执行设备枚举，不调用本程序的 `open_all()`，避免抢占外部软件拉流。
2. 用户输入相机 name 后，先在枚举结果中找到该相机对应的设备信息。
3. 对该相机创建临时 handle。
4. 调用 `IMV_GIGE_GetAccessPermission()` 查询访问权限。
5. 使用 `IMV_ECameraAccessPermission` 的枚举值判断该相机是否处于已被外部软件占用/不可由本程序直接打开的状态。
6. 该调用点默认注释，现场调试时手动启用，用来验证人工输入相机是否与外部软件拉流相机一致。

待验证点：

1. `IMV_GIGE_GetAccessPermission()` 是否必须在 `IMV_Open()` 后才能调用。
2. 外部软件拉流时，枚举结果里是否仍能拿到 `cameraName` 和 `cameraKey`。
3. 外部软件以不同权限打开相机时，SDK 返回值是否稳定可区分。
4. 若业务侧需要额外日志或异常保护，只在 SDK 外层新增调试封装，不修改 `./MVSDK`。

已确认点：

1. `serialNumber` 不由相机 SDK 提供，不作为 SDK 枚举信息依赖。
2. 串口号来自人工在电脑 COM 口中识别并提前维护的 `CAMERA_COM_MAP` 映射。

判定策略：

1. 人工输入相机 name 是唯一进入标定的方式。
2. 普通流程只校验相机 name 是否存在、是否有串口映射、是否已经完成标定。
3. SDK 权限查询调用保留为注释代码，调试时可手动开启并打印权限状态。
4. 如果 SDK 权限查询不可用，不阻断普通人工标定流程。
5. 如果人工调焦时发现画面没有变化，说明可能打开了错误相机；此时使用本轮标定中止流程，镜头执行上电找 0 后关闭，等待下次重新调试。
6. `IMV_ECameraAccessPermission` 是访问权限枚举，实际查询仍通过 `IMV_GIGE_GetAccessPermission()` 完成。

## Lens Map 数据结构

新增镜头范围数据结构，建议放在 `main.py` 或单独配置模块中，同时使用一个固定路径 JSON 文件作为 command 控制台模式下的标定状态落点。

默认范围沿用当前全局值：

```python
DEFAULT_ANGLE_START = 0.0
DEFAULT_ANGLE_END = 2900.0
```

建议结构：

```python
LENS_RANGE_MAP = {
    "1号": {
        "angle_start": 0.0,
        "angle_end": 2900.0,
        "calibrated": False,
    },
    "2号": {
        "angle_start": 0.0,
        "angle_end": 2900.0,
        "calibrated": False,
    },
}
```

固定状态文件建议：

```text
configs/lens_range_map.json
```

JSON 结构建议：

```json
{
  "generated_at": "2026-07-03 10:00:00",
  "default_angle_start": 0.0,
  "default_angle_end": 2900.0,
  "lens_ranges": {
    "1号": {
      "serial_port": "COM7",
      "angle_start": 0.0,
      "angle_end": 2900.0,
      "calibrated": false,
      "source": "pending",
      "samples": []
    },
    "2号": {
      "serial_port": "COM9",
      "angle_start": 0.0,
      "angle_end": 2900.0,
      "calibrated": false,
      "source": "pending",
      "samples": []
    }
  }
}
```

实例化规则：

1. 启用焦距标定前，先枚举当前相机并检查 `configs/lens_range_map.json` 是否存在。
2. 如果 JSON 不存在，直接根据当前枚举相机列表和 `CAMERA_COM_MAP` 新建所有镜头条目。
3. 如果 JSON 已存在，控制台提示用户选择：
   - `reuse`：沿用旧 JSON 中已有条目，只为新增相机补默认条目。
   - `reset`：覆盖旧 JSON，所有相机条目重新写入默认值。
   - `back`：不进入本次标定命令。
4. 新建或补齐条目时，每个默认条目使用 `angle_start=0.0`、`angle_end=2900.0`、`calibrated=false`、`source="pending"`、`samples=[]`。
5. 标定成功某台相机后，只覆盖该相机对应 JSON 条目。
6. command 控制台模式中，后续命令读取同一路径文件，作为当前会话的 Lens map 状态。

运行时实例结构建议：

```python
calibration_state = {
    "total_count": len(nodes),
    "completed_count": 0,
    "completed_camera_ids": set(),
    "results": {},
}
```

结果结构建议：

```python
results[camera_id] = {
    "camera_id": camera_id,
    "serial_port": com_port,
    "angle_start": min(angle_a, angle_b),
    "angle_end": max(angle_a, angle_b),
    "calibrated": True,
    "source": "measured",
    "samples": [angle_a, angle_b],
}
```

## `--focus_calibration` 总流程

入口：`run_focus_range_calibration(args)`

1. 枚举当前连接相机。
2. 检查固定 JSON 状态文件 `configs/lens_range_map.json` 是否存在。
3. 如果 JSON 存在，提示用户选择 `reuse/reset/back`；如果不存在，则直接新建。
4. 根据枚举结果和 `CAMERA_COM_MAP` 创建当前 Lens map 的运行实例；`reset` 或新建时所有镜头先写入默认范围 `0.0` 和 `2900.0`，`reuse` 时沿用已有完成结果并补齐缺失相机。
5. 不打开相机，不启动拉流。
6. 显示已识别相机列表、`camera_id -> serial_port` 映射；当前标定 command 不需要输出 `software/hardware` 坐标模式。
7. 提示用户输入当前已经在外部相机软件中打开并拉流的相机 name。
8. 校验用户输入规范：
   - 输入相机必须在枚举相机列表中。
   - 输入相机必须存在 `CAMERA_COM_MAP` 串口映射。
   - 输入相机不得已经完成标定。
9. 普通流程不强制执行 SDK 占用校验；可保留注释调用点，调试时打印 `IMV_GIGE_GetAccessPermission()` 和 `IMV_ECameraAccessPermission` 状态。
10. 检查通过后进入该镜头的两点标定流程；检查失败则提示原因并要求重新输入。
11. 单个镜头完成后：
   - `completed_count += 1`
   - 设置完成标志位
   - 记录 `ANGLE_START/ANGLE_END`
   - 覆盖写入 `configs/lens_range_map.json` 中该相机条目
   - 当前镜头执行上电找 0、关闭串口，并从镜头注册表注销
   - 输出当前进度
12. 如果 `completed_count == total_count`：
   - 显示全部标定结束
   - 输出完整日志
   - 确认镜头注册表为空；如仍有残留项，执行兜底清理
   - 不直接退出，进入完成后命令循环，允许用户执行 `status`、`redo 相机号` 或 `back`
13. 如果还没完成全部标定，继续等待用户输入下一台相机 name。
14. 如果用户在相机 name 输入阶段或完成后命令循环中输入 `back`：
   - 结束当前标定命令
   - 不再单独保留顶层 `cancel`，避免与 `back` 语义重叠
   - 已写入 JSON 的结果保持当前文件状态；下次重新启动标定时可选择 `reuse` 沿用或 `reset` 重建
   - 清理镜头注册表中仍残留的镜头：上电找 0、关闭串口并注销
   - 输出“用户退出本次标定命令”日志
   - 退出当前 `--focus_calibration` 命令会话
15. 如果用户在单个镜头标定中输入 `cancel`：
   - 只取消当前相机本轮标定
   - 回退当前 JSON 条目为默认值
   - 当前镜头执行上电找 0、关闭串口并注销
   - 不终止 `--focus_calibration` 会话，返回相机 name 输入阶段
16. 如果程序被意外退出或进程中止：
   - 如果是 `KeyboardInterrupt`、普通异常、`SystemExit`、命令内 `back` 等可捕获退出，先执行退出保护，清理镜头注册表中残留的镜头。
   - 如果是用户直接关闭控制台窗口、任务管理器强杀、系统关机、断电等硬终止，Python 可能没有机会执行清理逻辑，不能保证镜头一定回到 0 点。
   - 本次标定会话结束。
   - 下次重新执行 `python main.py --focus_calibration` 时，提示用户选择沿用旧 JSON 或重建 JSON。

## 退出保护与意外终止

可以做的保护：

1. `run_focus_range_calibration()` 顶层维护 `lens_registry`，只登记本程序本次已经打开串口、且可能被移动过的镜头。
2. 每次打开某个镜头串口后，立即登记到 `lens_registry`。
3. 每次镜头正常执行“上电找 0 + 关闭串口”后，从 `lens_registry` 移除。
4. 在 `try/finally` 中调用 `cleanup_lens_registry(lens_registry)`，作为异常退出兜底。
5. 捕获 `KeyboardInterrupt` 时，先执行同一个清理函数，再返回 `130`。
6. 顶层输入 `back` 时，先执行同一个清理函数，再退出当前 `--focus_calibration` 命令。
7. 完成后命令循环中用户输入 `back` 准备退出前，原则上 `lens_registry` 应为空；如果不为空，说明某个流程未完成注销，必须执行兜底清理。
8. 可额外注册 `atexit`，在普通解释器退出时尽力清理。
9. Windows 下可考虑注册控制台关闭事件处理，例如 `SetConsoleCtrlHandler`，用于捕获 `CTRL_C_EVENT`、`CTRL_CLOSE_EVENT` 等事件；但该回调执行时间有限，不能执行过长阻塞动作。

建议函数：

```python
def cleanup_lens_registry(lens_registry):
    for lens in reversed(lens_registry):
        try:
            lens.retract_lens_for_shutdown()  # 上电找 0
        finally:
            lens.close()
            lens_registry.remove(lens)
```

注意事项：

1. `cleanup_lens_registry()` 应串行执行，避免多个串口清理日志交错。
2. 清理时即使某个镜头上电找 0 失败，也要继续处理其他镜头。
3. 清理函数必须吞掉单个镜头异常并记录日志，不能因为一个镜头失败而跳过后续镜头。
4. 清理范围仅限 `lens_registry` 中登记过的镜头；未被本程序打开、未登记的镜头不做处理。
5. 如果用户直接关闭控制台窗口，Windows 可能只给进程很短清理时间；如果上电找 0 需要数秒，无法保证全部完成。
6. 如果进程被强杀或设备断电，任何 Python 层保护都无法执行。
7. 因此，下次启动 `--focus_calibration` 时仍应先执行新的初始化/归零流程，不依赖上一次退出时一定清理成功。

## 人工选择流程

提示用户输入当前标定焦距的相机号，例如：

```text
请先在外部相机软件中打开需要标定的相机并开始拉流。
请输入该相机 name，默认：1号 / 2号；输入 status 查看进度；输入 back 结束当前标定命令：
```

校验规则：

1. 输入必须在枚举到的相机列表中。
2. 输入相机必须存在 `CAMERA_COM_MAP` 映射。
3. 如果该相机已完成标定，提示：

```text
相机 1号 已完成焦距范围标定，请关闭或切换外部软件中的相机，继续处理其他相机。
```

4. 输入 `back` 结束当前 `--focus_calibration` 命令；退出前必须清理 `lens_registry` 中残留镜头，执行上电找 0、关闭串口并注销。已写入 JSON 的结果保留到文件中，下次重新启动标定时可选择 `reuse` 沿用或 `reset` 重建。
5. 顶层不再保留 `cancel` 命令，避免与 `back` 退出语义重叠。
6. 输入 `status` 显示已完成数量、未完成相机列表和已记录范围。
7. 如果输入不满足 1、2，则提示用户输入正确相机名。
8. 输入规范通过后，先在控制台输出当前相机/镜头参考信息，供用户人工核对：
   - 用户输入的相机 name。
   - SDK 枚举得到的 `cameraName`。
   - SDK 枚举得到的 `cameraKey`，如果当前结构可读。
   - 相机对应的 `CAMERA_COM_MAP` 串口号。
   - 当前 JSON 中该相机的 `angle_start/angle_end/calibrated`。
9. 上述信息只用于人工参考核对，不影响后续标定流程。
10. 输出参考信息后，提示用户确认下一步：
   - 输入 `ok` 进入镜头实测标定。
   - 输入 `default` 直接保留默认范围 `0.0/2900.0`，并把该相机 JSON 条目标记为可用标定结果。
   - 输入 `cancel` 返回相机 name 输入阶段，不打开镜头、不写 JSON、不改变完成状态。
11. 用户确认 `ok` 后，进入镜头标定；用户输入 `default` 后，不打开镜头串口，直接写入默认可用结果并返回/继续等待下一台相机。
12. 可保留 SDK 占用调试代码，但默认注释：
   - 找到该相机的枚举设备信息。
   - 创建临时 handle。
   - 调用 `IMV_GIGE_GetAccessPermission()`。
   - 根据 `IMV_ECameraAccessPermission` 判断该相机是否已被外部软件占用。
   - 销毁临时 handle。
13. 调试代码只打印权限状态，不作为普通流程是否进入标定的决定条件。

建议新增函数：

```python
def validate_manual_camera_selection(camera_name, devices, completed_camera_ids):
    ...

def debug_camera_access_permission(device_info):
    ...

def print_camera_calibration_reference(camera_name, device_info, com_port, lens_range):
    ...
```

行为：

1. `validate_manual_camera_selection()` 负责输入规范、串口映射和完成状态校验。
2. `debug_camera_access_permission()` 只负责 SDK 权限查询和日志输出，不做人工输入解析。
3. `print_camera_calibration_reference()` 负责输出人工核对信息，不改变状态、不写 JSON、不阻塞流程。
4. 该流程不得调用会抢占拉流的 `open_node()`。
5. 若为了查询权限必须创建 handle，使用后必须销毁 handle。
6. `debug_camera_access_permission()` 的调用点默认注释，现场调试需要时再手动启用。

## 顶层 back 退出流程

该流程只由相机 name 输入阶段的 `back` 触发，用于用户主动结束当前 `--focus_calibration` 命令。顶层不再保留 `cancel` 命令，避免两个退出命令语义重叠。

`back` 行为：

1. 不进入任何新的相机/镜头标定流程。
2. 不额外回退整份 JSON；已完成并写入的镜头结果保持在当前文件中。
3. 下次重新执行 `python main.py --focus_calibration` 时，仍按初始化规则覆盖整份 JSON 为默认值并重新标定。
4. 调用 `cleanup_lens_registry(lens_registry)`，只清理本程序本次已打开、已登记、但尚未正常注销的镜头。
5. 对每个残留镜头执行上电找 0、关闭串口并注销。
6. 输出用户主动退出本次标定命令的控制台日志和文件日志。
7. 退出当前 `--focus_calibration` 命令会话。该退出属于用户主动退出，建议返回 `0`；如果后续需要脚本区分“完成”和“中途退出”，可再定义专用返回码。

建议新增函数：

```python
def exit_focus_calibration_by_back(lens_registry):
    cleanup_lens_registry(lens_registry)
```

注意：

1. 该流程和单镜头调焦阶段的 `cancel` 不同；单镜头 `cancel` 只回退当前相机条目并返回相机 name 输入阶段。
2. 该流程不清空整份 JSON，因为 `back` 的语义是“结束当前命令”，不是“撤销全部已完成结果”。
3. 该流程仍然受到普通退出保护限制：如果控制台被强制关闭、进程被强杀或设备断电，Python 可能无法执行镜头归零。

## 启动 JSON 选择流程

进入 `--focus_calibration` 后，程序不再无条件清空 `configs/lens_range_map.json`。更合理的现场流程是先检查旧 JSON，并让用户决定是否沿用。

启动行为：

1. 枚举当前连接相机和 `CAMERA_COM_MAP`。
2. 检查 `configs/lens_range_map.json`：
   - 文件不存在：创建新 JSON，所有相机条目写入默认 pending 状态。
   - 文件存在：读取并校验结构。
3. 如果旧 JSON 可读，输出摘要：
   - 已完成相机列表。
   - 未完成相机列表。
   - 使用默认范围的相机列表。
   - 当前连接但旧 JSON 缺失的相机列表。
   - 旧 JSON 中存在但当前未连接的相机列表。
4. 提示用户选择：

```text
检测到已有 configs/lens_range_map.json。
输入 reuse 沿用已有结果并补齐缺失相机；
输入 reset 清空旧结果并重新创建；
输入 back 退出本次标定命令：
```

5. `reuse` 行为：
   - 保留旧 JSON 中 `calibrated=true` 的条目。
   - 保留旧 JSON 中 `source="default"` 的可用默认条目。
   - 对当前连接但 JSON 缺失的相机补 `pending` 默认条目。
   - 对 JSON 中存在但当前未连接的相机输出警告；实现时可选择保留但不参与本轮 `total_count`。
   - 根据本轮参与相机重新计算 `completed_count` 和 `completed_camera_ids`。
6. `reset` 行为：
   - 覆盖旧 JSON。
   - 当前连接且有 COM 映射的相机全部写为 `pending` 默认条目。
   - 所有镜头都需要重新标定或显式选择 `default`。
7. `back` 行为：
   - 不修改 JSON。
   - 清理 `lens_registry` 残留项后退出命令；正常此时注册表应为空。

注意：

1. 如果用户在中途多次尝试某台相机都失败，可以 `back` 退出检查原因；修复后再次启动时选择 `reuse`，只继续处理失败或未完成条目。
2. 如果旧 JSON 结构损坏或无法解析，提示用户选择 `reset` 或 `back`，不要静默覆盖。
3. `reuse` 模式下，已完成相机默认防重复；需要重做时使用 `redo 相机name`。

## 相机核对阶段命令流程

用户输入相机 name 并通过基础校验后，程序会先输出该相机对应的参考信息，供用户人工核对外部拉流相机是否一致。此时还没有打开镜头串口，也不应该写入任何新的标定结果。

核对阶段行为：

1. 输出相机 name、可读 `cameraKey`、COM 口、当前 JSON 条目等参考信息。
2. 提示用户输入 `ok` 继续进入镜头实测标定，输入 `default` 直接使用默认范围，或输入 `cancel` 返回相机 name 输入阶段。
3. 如果输入 `cancel`：
   - 不打开镜头串口。
   - 不修改 JSON。
   - 不改变 `completed_count` 和 `completed_camera_ids`。
   - 返回相机 name 输入阶段，等待用户重新选择相机。
4. 如果输入 `default`：
   - 不打开镜头串口。
   - 不登记 `lens_registry`。
   - 将该相机 JSON 条目保持/写入默认范围：

```json
{
  "angle_start": 0.0,
  "angle_end": 2900.0,
  "calibrated": true,
  "samples": [0.0, 2900.0],
  "source": "default"
}
```

   - `completed_count += 1`。
   - 将该相机加入 `completed_camera_ids`。
   - 输出“相机 X 已使用默认焦距范围并标记为可用”的日志。
   - 如果完成数量等于相机数量，则进入全部完成流程；否则返回相机 name 输入阶段，等待下一台相机。
5. 如果输入 `ok`：
   - 进入单个镜头实测标定流程。
   - 打开镜头串口后才登记 `lens_registry`。

注意：

1. `default` 的语义是用户明确接受默认范围作为可用标定结果，不等同于未标定。
2. 后续 `--full_shot` 读取到 `calibrated=true` 且 `source="default"` 的条目时，可以直接使用默认范围；建议输出一条提示说明该相机使用默认标定范围。
3. 为了和实测标定结果区分，建议实测成功条目写入 `source="measured"`。


## 单个镜头标定流程

函数建议：

```python
def calibrate_single_lens(camera_id, com_port, lens_registry):
    ...
```

步骤：

1. 创建 `LensController(port=com_port, coordinate_mode="hardware")`；如果底层构造参数后续调整，标定 command 仍必须以电机内部真实角度为唯一记录来源。
2. 打开串口，并立即登记到 `lens_registry`。
3. 执行 `initialize_lens()`，完成归零准备：
   - 上电找 0
   - 点击回 0
   - 等待镜头停稳
4. 在点击回 0 和停稳之后，保留硬件清零调用点，但默认注释，供现场调试时手动启用：

```python
# 调试用途：回 0 停稳后，将当前位置写成电机内部 0 点。
# lens.set_current_position_as_hardware_zero()
```

5. 读取并输出当前电机内部角度，用它校验 0 点是否可信：
   - 如果内部角度在 `[-1.0°, +1.0°]` 内，认为归零可信，并把当前标定角度规范化为 `0.0°`。
   - 如果内部角度小于 `-1.0°` 或大于 `+1.0°`，提示归零异常，直接退出当前相机标定，让用户重新输入相机 name 后再处理。
6. 进入第 1 个焦距端点调整。
7. 用户输入相对当前位置的调整角度，程序根据“当前绝对角度 + 相对调整角度”计算目标位置并移动镜头。
8. 每次移动后读取电机内部真实角度，经过边界规范化后作为当前绝对角度。
9. 每次移动后提示用户是否满意。
10. 用户满意后记录当前绝对角度为第 1 个端点。
11. 不强制执行上电找 0 或点击回 0；继续以当前内部绝对角度作为当前位置，让用户在当前位置基础上继续输入相对角度，记录第 2 个端点。
12. 将较小角度写为 `ANGLE_START`，较大角度写为 `ANGLE_END`。
13. 将标定结果写入 JSON 后，当前镜头必须执行上电找 0 回到初始位置。
14. 上电找 0 完成后关闭串口。
15. 关闭串口后，从 `lens_registry` 注销当前镜头。
16. 只要当前镜头流程结束，无论是正常完成、用户 `cancel`，还是异常退出到相机 name 输入阶段，都必须尽力执行“上电找 0 + 关闭串口 + 注销”。

## 相对角度调整交互

每次调整提示：

```text
请输入相对旋转角度，正数前进(镜头收缩)、负数后退(镜头扩展)。
推荐调试步长：1000°、100°、10°。
输入 ok 接受当前焦点；输入 home 重新回 0；输入 cancel 结束本轮标定并安全关闭镜头。
```

输入解析规则：

1. `ok`：接受当前内部绝对角度，缓存为当前端点。
2. `home`：执行回 0 并重新校验 0 点。
3. `cancel`：中止当前相机本轮标定，执行本轮标定中止流程。
4. 其他输入先尝试解析为 `float` 相对角度。
5. 如果输入不是合法 `float`，例如空字符串、普通文本或格式错误数字，只提示“请输入合法相对角度或命令”，不退出当前端点调整。
6. 如果输入数值合法但移动失败，根据失败类型进入边界提示或异常退出流程。

标定坐标记录规则：

1. 标定流程不再区分 `software` 或 `hardware` 记录口径，统一以电机内部真实角度作为最终记录值。
2. 这个规则成立的前提是：上电找 0 和点击回 0 后，内部角度已经被确认在 0 点附近。
3. `software/hardware` 坐标模式只对后续多轮旋转拍摄流程有意义；当前 `--focus_calibration` 不需要在控制台打印该模式，也不使用软件变量作为最终真值。
4. 用户输入的是相对当前位置的调整角度，正负都允许。
5. 每一次输入都在当前实际位置基础上调整：
   - 用户输入 `1000`，表示从当前位置向正方向调整约 `1000°`。
   - 用户随后输入 `100`，表示继续从新的当前位置再向正方向调整约 `100°`。
   - 用户输入 `-10`，表示从当前位置向反方向调整约 `10°`。
6. 当前绝对角度只来自电机内部读数，并在每次移动完成后刷新。
7. 程序内部可以用 `expected_target_angle = current_internal_angle + relative_delta` 作为下一次移动的目标值或调试参考；它不是最终标定值。
8. `expected_target_angle` 的意义：
   - 用于把用户输入的相对角度转换成一次绝对移动命令。
   - 用于移动完成后和新读取的内部真实角度做一致性对比，辅助发现电机未到位、碰壁、读数异常或通信异常。
   - 用于在下发前判断目标是否明显越界，例如小于 `0.0°` 时给出提示或确认。
9. 最终缓存端点和写入 JSON 的角度必须来自移动后再次读取到的电机内部真实角度，而不是 `expected_target_angle`、用户输入值或软件推算值。
10. 一致性诊断规则：
   - 如果移动后内部真实角度与 `expected_target_angle` 偏差在允许阈值内，认为本次移动验证通过，并用内部真实角度更新 `current_internal_angle`。
   - 如果偏差较大，但检测到零点/远端边界状态，则认为这是碰壁导致的正常偏差；仍以内部真实角度为准，进入边界提示流程。
   - 如果设备状态正常、没有边界状态，但内部真实角度与 `expected_target_angle` 偏差过大，认为本轮当前相机标定异常失败。
11. 上述异常失败的处理等价于自动执行当前相机本轮 `cancel`：
   - 输出错误日志，包含 `expected_target_angle`、内部真实角度、偏差值和状态码。
   - 当前相机 JSON 条目回退为 `pending` 默认值。
   - 当前镜头执行上电找 0、关闭串口并注销。
   - 返回相机 name 输入阶段，允许用户重新尝试该相机或输入 `back` 退出检查原因。
12. 进入第 1 个端点调整前，镜头必须位于可信 0 点。
13. 第 1 个端点确认后，开始第 2 个端点前不再强制回 0；继续从当前位置调整，更符合现场连续调焦习惯。
14. 如果用户输入 `home`，才执行回 0 并重新校验 0 点；校验通过后，当前位置重新规范化为 `0.0°`，用户可继续从 0 点开始调整。
15. 如果读取失败、状态异常或归零校验失败，当前相机标定失败；程序必须先让当前镜头执行上电找 0、关闭串口并注销，再退出到相机 name 输入阶段。
16. 每次旋转完成后，都要在控制台输出当前内部绝对角度，例如：

```text
当前内部绝对角度: 1234.56°
```

17. 用户输入 `ok` 完成一个端点时，要在控制台输出缓存提示，例如：

```text
已缓存端点 1: 1234.56°
```

## 本轮标定中止流程

如果用户人为调焦时发现外部画面没有变化，通常说明外部软件打开的相机和程序输入的相机不一致。此时用户应输入 `cancel` 结束本轮标定。

`cancel` 行为：

1. 当前相机本轮标定结果不保存。
2. 当前相机对应的 JSON 条目回退为默认值：

```json
{
  "angle_start": 0.0,
  "angle_end": 2900.0,
  "calibrated": false,
  "samples": []
}
```

3. 当前相机不计入 `completed_count`。
4. 当前相机不加入 `completed_camera_ids`。
5. 当前镜头执行上电找 0 指令，使镜头回到安全收缩/零位准备状态。
6. 等待上电找 0 动作完成，并尽量读取内部状态用于日志。
7. 关闭镜头串口。
8. 从 `lens_registry` 注销当前镜头。
9. 返回 `--focus_calibration` 的相机 name 输入阶段，等待用户重新打开正确相机并再次输入。

建议函数：

```python
def abort_current_lens_calibration(lens, camera_id, reason):
    ...
```

注意：

1. 该流程只处理当前镜头，不影响其他已完成相机的标定结果。
2. 如果上电找 0 或状态读取失败，也必须尝试关闭串口、注销注册表条目并输出错误日志。
3. 该流程不同于“完成标定后的正常关闭”；它是发现人为选择错误后的安全退出。
4. JSON 回退只覆盖当前相机条目，不清空整份文件。
5. 用户取消当前相机标定不等同于程序退出；会话继续，其他已完成相机保持完成状态。

## 边界与碰壁规则

调焦时可能因为相对当前位置的调整角度过大而碰到前后两个物理边界；也可能在回退到 0 点附近时因误差读到极小负数。`--focus_calibration` 必须显式处理这些情况。

角度规范化规则：

1. 如果内部角度在 `[-1.0°, 0.0°)`，认为是 0 点附近的读数误差，直接规范化为 `0.0°`。
2. 如果内部角度小于 `-1.0°`，认为是异常负角度，报错并退出当前相机标定。
3. 如果内部角度为正常非负值，按实际值记录。
4. 如果内部角度读数缺失，不允许记录端点。
5. 如果 `expected_target_angle = current_internal_angle + relative_delta` 得到的目标角度小于 `0.0°`，允许下发前给出确认提示；执行后若碰到 0 点边界且内部角度规范化为 `0.0°`，则该结果可被接受为 0 点端点。
6. 如果移动后的内部真实角度和 `expected_target_angle` 偏差明显，且没有边界状态解释该偏差，应输出异常提示并要求用户检查镜头状态；该偏差检查只作为安全诊断，不改变最终以内部真实角度为准的记录规则。

碰壁识别规则：

1. `LensController._wait_until_stopped()` 目前把状态 `0x0B` 和 `0xF5` 识别为触碰物理边界。
2. 当前先按现场临时约定映射边界：

```python
LENS_BOUNDARY_ZERO = 0x0B
LENS_BOUNDARY_FAR = 0xF5
```

3. 该映射目前只影响控制台提示文本和推荐调整方向，不改变最终记录规则；最终端点仍以电机内部真实绝对角度为准。
4. 实际调试时如果发现方向相反，可直接交换 `LENS_BOUNDARY_ZERO` 和 `LENS_BOUNDARY_FAR` 的定义。
5. 即使使用临时映射，也要在日志中输出 `boundary_status=0x0B/0xF5`、相对角度方向、碰壁前后内部角度，方便后续人工确认。
6. 碰壁后不自动把该点记为满意端点，必须通知用户已到达哪个边界，并提示下一次应该输入哪个方向的相对角度：

```text
检测到零点边界，当前内部绝对角度: 0.00°。
请继续输入正的相对角度离开零点边界。
```

```text
检测到远端边界，当前内部绝对角度: xxxx.xx°。
请继续输入负的相对角度离开远端边界。
```

7. 如果碰壁后内部角度小于 `-1.0°` 或无法读取，不能继续该端点调整，必须执行当前镜头上电找 0、关闭串口并注销后退出到相机 name 输入阶段。
8. 如果碰壁后内部角度可读取，保留当前规范化角度作为新的当前位置，但不缓存为端点；等待用户输入正确方向的相对角度继续调整。

0 点校验规则：

1. 每次上电找 0 和点击回 0 后，必须读取内部角度并输出日志。
2. 硬件清零函数调用点位于“归零动作完成”和“输出并校验内部角度”之间，默认注释：

```python
# 调试用途：回 0 停稳后，将当前位置写成电机内部 0 点。
# lens.set_current_position_as_hardware_zero()
```

3. 清零调用点默认不启用；现场调试确认需要时再手动取消注释。
4. 如果最终读取值在 `[-1.0°, +1.0°]`，归零通过，并将当前角度规范化为 `0.0°`。
5. 如果最终读取值超出该范围，归零失败，提示异常并退出当前相机标定。

## 完成状态与防重复

`--focus_calibration` 内部维护：

```python
completed_camera_ids = set()
completed_count = 0
```

行为：

1. 用户再次输入已完成相机时，只提示已完成，不重新进入标定。
2. 可通过 `redo 相机号` 明确要求重做某台相机，重做成功后覆盖该相机结果，但 `completed_count` 不重复增加。
3. 全部完成后输出：

```text
全部镜头焦距范围标定完成。
已完成数量：N/N
```

4. 全部完成后不直接退出命令，而是进入完成后命令循环：

```text
全部镜头焦距范围标定完成。
可输入 status 查看结果，输入 redo 相机name 重做某台相机，输入 back 保存当前 JSON 并退出。
```

5. 完成后命令循环支持：
   - `status`：打印全部相机的 `angle_start/angle_end/calibrated/source`。
   - `redo 相机name`：将该相机从完成状态临时移除，重新进入该相机标定流程；重做成功后覆盖原 JSON 条目，`completed_count` 不重复增加。
   - `back`：确认 `lens_registry` 为空或执行兜底清理后退出 `--focus_calibration`。
6. 这样最后一台相机完成后仍然可以被 `redo`，不会因为自动退出而失去重做机会。

## 日志与结果输出

command 控制台模式下，建议固定使用同一份 JSON 文件记录当前标定状态，并另外输出日志：

1. 固定 JSON 状态文件：供当前 command 会话和后续拍摄流程读取。
2. 控制台摘要：方便现场确认。
3. 文件日志：方便排查每次标定过程。
4. JSON 配置文件和过程日志分目录保存，避免状态文件、拍摄输出和调试日志混在一起。

文件建议：

```text
configs/lens_range_map.json
log/focal_cali/lens_range_calibration_result_YYYYMMDD_HHMMSS.log
```

日志目录约定：

1. 焦距标定日志统一放在 `log/focal_cali/`。
2. 后续项目中其他功能日志也统一放在 `log/<feature_name>/` 这类独立子目录下，例如：

```text
log/focal_cali/
log/full_shot/
log/rotation/
log/camera/
```

3. 不同功能不要共用同一个日志目录，避免后续排查时混淆。
4. 如果某个功能已有独立日志目录，后续重构时也按 `log/<feature_name>/` 规范迁移或兼容。

JSON 示例：

```json
{
  "generated_at": "2026-07-02 16:00:00",
  "default_angle_start": 0.0,
  "default_angle_end": 2900.0,
  "lens_ranges": {
    "1号": {
      "serial_port": "COM7",
      "angle_start": 120.0,
      "angle_end": 2480.0,
      "calibrated": true,
      "source": "measured",
      "samples": [120.0, 2480.0]
    },
    "2号": {
      "serial_port": "COM9",
      "angle_start": 0.0,
      "angle_end": 2900.0,
      "calibrated": false,
      "source": "pending",
      "samples": []
    }
  }
}
```

写入规则：

1. 启用标定前，不再无条件覆盖 `configs/lens_range_map.json`；如果旧文件存在，先由用户选择 `reuse/reset/back`。
2. 单个相机实测标定成功后，覆盖该相机条目为实际 `angle_start/angle_end`，并设置 `calibrated=true`、`source="measured"`。
3. 相机核对阶段输入 `default` 后，覆盖该相机条目为默认范围 `0.0/2900.0`，并设置 `calibrated=true`、`source="default"`、`samples=[0.0, 2900.0]`。
4. 单个相机标定 `cancel` 后，覆盖该相机条目为默认范围，并设置 `calibrated=false`、`source="pending"`、`samples=[]`。
5. 相机核对阶段 `cancel` 不写 JSON，不改变任何条目。
6. 相机 name 输入阶段 `back` 不额外回退整份 JSON；已完成相机不会因为其他相机的单镜头 `cancel` 或顶层 `back` 而回退。
7. 如果程序异常退出，下次重新启用标定时提示用户选择 `reuse` 或 `reset`；选择 `reuse` 可保留之前已经正确完成的条目。

## 对 `--full_shot` 的同步影响

当前 `run_rotation_multi_shot(args)` 在函数开头调用：

```python
target_angles = generate_step_angles(args.lens_steps)
```

而当前 `generate_step_angles(steps)` 使用全局 `ANGLE_START/ANGLE_END`。完成每镜头 Lens map 后，`--full_shot` 必须改为读取 `configs/lens_range_map.json`，按每个相机/镜头自己的范围生成步进角度。

生成函数需要改为：

```python
def generate_step_angles(angle_start, angle_end, steps):
    ...
```

建议新增配置读取函数：

```python
def load_lens_range_map(path="configs/lens_range_map.json"):
    ...
```

读取规则：

1. `--full_shot` 启动时读取 `configs/lens_range_map.json`。
2. 如果文件不存在，可使用默认范围 `0.0/2900.0` 并输出明显警告；后续也可增加 strict 模式要求必须先标定。
3. 如果某个相机缺少 JSON 条目，使用默认范围并输出警告。
4. 如果某个相机条目 `calibrated=false`，仍可按默认范围运行，但必须输出“该镜头未完成标定，使用默认范围”的警告。
5. 如果某个相机条目存在且 `calibrated=true`，使用该相机自己的 `angle_start/angle_end`。
6. 如果 `calibrated=true` 且 `source="default"`，仍视为可用标定条目，但建议输出“该镜头使用默认焦距范围”的提示，便于现场追踪。

后续拍摄流程要从“所有镜头移动到同一个角度”改成“每个镜头移动到自己的当前步进角度”。例如第 `step_idx` 次：

```python
per_lens_step_angles = {}
for node in sys_dev.nodes:
    lens_range = lens_range_map[node.camera.m_userId]
    per_lens_step_angles[node.camera.m_userId] = generate_step_angles(
        lens_range["angle_start"],
        lens_range["angle_end"],
        args.lens_steps,
    )

for node in sys_dev.nodes:
    cam_name = node.camera.m_userId
    target_angle = per_lens_step_angles[cam_name][step_idx]
    node.lens.move_to_absolute_angle(target_angle)
```

为了保留串行/并行切换能力，建议在 `ImageNode.py` 或 `ImagingSystem` 中新增按相机名下发目标角度的方法：

```python
sys_dev.serial_move_lenses_by_camera(target_angles_by_camera)
sys_dev.parallel_move_lenses_by_camera(target_angles_by_camera)
```

其中 `target_angles_by_camera` 示例：

```python
{
    "1号": 120.0,
    "2号": 95.0
}
```

输出目录命名也需要调整。由于每个镜头同一步的角度不同，不能再用单个 `Angle_{target_angle}` 表示所有镜头。建议改为：

```text
Output/Position_{current}/Step_{step_idx + 1}
```

每张图片仍按相机名保存，具体角度写入该 step 目录的 metadata JSON 或控制台日志中。

## 实施步骤

1. 封装 SDK 外层调试辅助：
   - 不修改 `./MVSDK` 下任何文件。
   - 新增一个只枚举、不打开相机的设备列表 helper。
   - 使用 `IMV_ECameraAccessPermission` 定义外部占用状态的判定规则。
   - 编写注释态的调试函数，用于打印用户输入相机的访问权限状态。
   - 编写相机标定参考信息输出函数，用于打印 `cameraName/cameraKey/COM/JSON` 当前范围。
2. 新增 Lens map 数据模型：
   - 定义运行时 `LensCalibrationRecord` 或字典结构。
   - 将 `ANGLE_START/ANGLE_END` 从全局单值迁移到按 `camera_id` 管理。
   - 新增固定 JSON 状态文件 `configs/lens_range_map.json`。
   - 每次启用焦距标定前，检查旧 JSON 并提示用户选择 `reuse/reset/back`。
   - `reuse` 时沿用已有完成结果并补齐缺失相机；`reset` 时清空/覆盖该 JSON，并按默认范围 `0.0/2900.0` 实例化所有镜头条目。
3. 实现 `--focus_calibration` 主循环：
   - 枚举相机。
   - 创建标定状态。
   - 按 `reuse/reset` 选择初始化或载入 `configs/lens_range_map.json`。
   - 只保留人工输入相机 name。
   - 校验输入相机是否存在、是否有串口映射、是否已完成标定。
   - 输入规范通过后，在控制台输出当前相机/镜头参考信息，供人工核对。
   - SDK 占用权限查询只保留为默认注释的调试调用点。
   - 已完成相机防重复提示。
   - 支持相机核对阶段的 `cancel`：不打开镜头、不写 JSON，直接返回相机 name 输入阶段。
   - 支持相机核对阶段的 `default`：不打开镜头、不登记注册表，直接写入默认范围并标记 `calibrated=true`、`source="default"`。
   - 支持相机 name 输入阶段的 `back`：清理 `lens_registry` 中残留镜头后退出当前命令。
   - 完成全部相机标定后，输出完整日志并进入完成后命令循环，支持 `status/redo/back`；正常情况下所有单镜头流程已经完成上电找 0、关闭串口并注销，顶层只检查注册表是否为空。
   - 适配 `handle_focus_calibration_cli(argv)`，保持 `--focus_calibration --help` 只输出帮助。
   - 实现分阶段 help 文本：说明程序用途、启动准备、JSON 选择、相机 name 输入、相机核对、单镜头调焦、完成后命令和输出路径，不暴露实现细节。
4. 实现单镜头标定函数：
   - 打开串口后立即登记到 `lens_registry`。
   - 上电找 0、点击回 0、等待停稳。
   - 保留硬件清零注释。
   - 归零后读取内部角度，超过 `[-1.0°, +1.0°]` 则退出当前相机标定。
   - 两次交互式相对角度调整。
   - 用户每次输入都解释为相对当前位置的调整角度。
   - 非法 float 输入只提示重新输入，不退出端点调整。
   - 第 1 个端点调整前从可信 0 点开始；第 1 个端点确认后不强制回 0，继续从当前位置调整第 2 个端点。
   - 每次移动后以内部真实角度记录当前绝对角度。
   - 使用 `expected_target_angle = current_internal_angle + relative_delta` 作为移动目标和诊断参考，但不作为最终标定结果。
   - 移动后比较内部真实角度与 `expected_target_angle`：偏差小则通过，碰壁导致偏差则按边界处理，无边界且偏差过大则本轮标定失败并自动执行当前相机 cancel 清理。
   - 每次移动完成后在控制台输出当前内部绝对角度。
   - 用户 `ok` 缓存端点时输出“已缓存端点 N: xxx°”。
   - 将 `[-1.0°, 0.0°)` 的微小负角规范化为 `0.0°`。
   - 识别 `0x0B` 和 `0xF5` 的碰壁状态，并按零点/远端边界提示用户输入正确方向的相对角度。
   - 支持用户输入 `cancel` 中止本轮标定，当前 JSON 条目回退为默认 `0.0/2900.0`，执行上电找 0、关闭串口并注销。
   - 正常完成、用户取消、异常退出到相机输入阶段时，都执行当前镜头上电找 0、关闭串口并注销。
   - 保存小值为 START，大值为 END。
5. 实现结果输出：
   - 控制台摘要。
   - 固定 JSON 状态文件。
   - 时间戳 log 文件，保存到 `log/focal_cali/`。
   - 按 `log/<feature_name>/` 规范整理后续其他功能日志目录。
6. 接入 `--full_shot`：
   - 保留当前 `--full_shot` 命令名，不再改名。
   - 实现 `--full_shot --help`，说明该命令如何读取 Lens map、如何处理默认/未标定条目、如何生成每镜头步进角度以及输出目录规则。
   - `--full_shot` 启动时读取 `configs/lens_range_map.json`。
   - 让 `generate_step_angles()` 支持传入每镜头范围。
   - 为每个相机单独生成步进角度列表。
   - 读取到 `source="default"` 的可用条目时使用默认范围，并输出提示。
   - 新增按相机名下发不同目标角度的串行/并行移动函数。
   - 缺失标定结果时给出明确警告并使用默认安全范围。
   - 输出目录不再用单一 `Angle_{target_angle}` 表示所有镜头角度。

## 风险与回退

1. SDK 无法判断用户输入相机是否被外部软件占用：
   - 不阻断普通人工流程；只在调试日志中提示权限状态未知。
2. 用户输入相机和外部拉流相机不一致：
   - 程序会先输出相机参考信息供人工核对；如果此时发现不一致，用户输入 `cancel` 直接返回相机 name 输入阶段；如果进入标定后才发现画面没有变化，用户输入 `cancel`，程序执行当前镜头上电找 0、关闭串口并注销，然后返回相机 name 输入阶段。
3. 归零后内部角度不在 0 点附近：
   - 当前相机标定失败；先执行当前镜头上电找 0、关闭串口并注销，再让用户重新输入相机 name 后处理。
4. 硬件清零指令现场未验证：
   - 继续保持调用注释，不在正式流程自动执行。
5. 碰壁状态方向临时映射可能与真实方向相反：
   - 当前先按 `0x0B=零点边界`、`0xF5=远端边界` 处理；该设置主要影响控制台提示文本和建议调整方向。现场测试发现相反时，直接交换两个常量定义。
6. 内部角度出现微小负数：
   - `[-1.0°, 0.0°)` 规范化为 `0.0°`；小于 `-1.0°` 视为异常并退出当前相机标定。
7. 用户重复打开已完成相机：
   - 根据 `completed_camera_ids` 提示已完成，避免覆盖结果。
8. 用户需要重做：
   - 后续实现显式 `redo` 命令，避免误触发。
9. JSON 状态文件写入失败：
   - 当前标定结果不应视为完成；提示文件写入错误，保持内存状态与文件状态一致后再继续。
10. command 模式下重新启用标定：
   - 不再直接清空 JSON；先提示用户选择 `reuse/reset/back`。现场修复问题后可选择 `reuse`，只继续处理失败或未完成相机。
11. 顶层 `back` 或异常退出时镜头清理失败：
   - 只对 `lens_registry` 中已登记镜头执行清理；单个镜头上电找 0 失败时记录错误并继续处理其他已登记镜头。
12. 正常完成后注册表仍有残留：
   - 视为流程缺陷并执行兜底清理；日志中记录残留镜头信息，便于排查哪条路径没有注销。
13. `--full_shot` 找不到 JSON 或发现未标定条目：
   - 输出明显警告并使用默认范围 `0.0/2900.0`，避免直接崩溃；后续可增加 strict 模式。
14. 用户输入非法相对角度：
   - 不改变镜头位置，不退出当前端点调整，只提示重新输入。
15. 预期角度与内部真实角度偏差过大：
   - 如果存在边界状态，按碰壁流程处理；如果状态正常且无边界状态，视为当前相机本轮标定失败，输出错误日志并自动执行当前相机 cancel 清理。
16. 用户关闭控制台窗口：
   - 如果控制台关闭事件能被捕获，则尝试执行 `cleanup_lens_registry()`；但由于系统给出的清理时间有限，不能保证所有已登记镜头完成上电找 0。
17. 进程被强杀、系统断电或设备断电：
   - Python 无法执行任何清理逻辑；下次启动标定时必须重新执行镜头初始化/归零，并提示用户选择 `reuse` 沿用旧 JSON 或 `reset` 重建 JSON。

## 验收清单

- [ ] `--focus_calibration` 进入后不调用 `open_all()`，不会抢占相机拉流。
- [ ] 不修改 `./MVSDK` 目录下任何文件，所有 SDK 调用以当前 SDK 文件为基准。
- [ ] 能枚举当前连接相机数量并创建对应 Lens map 实例。
- [ ] 能显示 `camera_id -> COM` 映射。
- [ ] 每次启用焦距标定前会检查 `configs/lens_range_map.json`，存在旧文件时提示用户选择 `reuse/reset/back`。
- [ ] 选择 `reuse` 时会沿用已有完成结果并补齐缺失相机；选择 `reset` 时才覆盖旧 JSON。
- [ ] JSON 初始化时每个镜头默认 `angle_start=0.0`、`angle_end=2900.0`、`calibrated=false`。
- [ ] `--focus_calibration` 只通过人工输入相机 name 进入标定流程。
- [ ] 输入相机 name 不存在时会提示重新输入。
- [ ] 输入相机无串口映射时会提示无法控制镜头。
- [ ] 顶层相机 name 输入阶段只保留 `back` 作为退出命令，不再保留顶层 `cancel`。
- [ ] 在相机 name 输入阶段输入 `back` 时，会清理 `lens_registry` 中残留镜头并退出当前 `--focus_calibration` 命令。
- [ ] 输入规范通过后，会输出当前相机/镜头参考信息，包括相机 name、可读 cameraKey、COM 口和 JSON 当前范围。
- [ ] 相机参考信息只供人工核对，不作为阻断标定流程的强制校验。
- [ ] 相机核对阶段输入 `cancel` 时，不打开镜头、不写 JSON、不改变完成状态，并返回相机 name 输入阶段。
- [ ] 相机核对阶段输入 `default` 时，不打开镜头、不登记 `lens_registry`，直接把默认范围写为可用标定条目。
- [ ] 使用默认范围的条目会设置 `calibrated=true`、`source="default"`、`samples=[0.0, 2900.0]`。
- [ ] 当前标定 command 不在控制台打印 `software/hardware` 坐标模式。
- [ ] SDK 权限查询调用点默认注释，仅作为调试辅助。
- [ ] SDK 权限查询不可用时不会阻断普通人工标定流程。
- [ ] 已完成相机再次被选择时会提示已完成。
- [ ] 每个镜头标定前会执行上电找 0 和点击回 0，并等待停稳。
- [ ] 归零后会读取并输出内部角度，且只有在 `[-1.0°, +1.0°]` 内才继续标定。
- [ ] 硬件清零函数调用点保留注释。
- [ ] 第 1 个端点调整从可信 0 点开始；第 1 个端点确认后不会强制回 0，而是继续从当前位置调整第 2 个端点。
- [ ] `expected_target_angle = current_internal_angle + relative_delta` 仅用于移动目标和诊断参考，不写入最终标定结果。
- [ ] 内部真实角度与 `expected_target_angle` 偏差小时会更新 `current_internal_angle`；有边界状态时按碰壁处理；无边界且偏差过大时本轮标定失败并自动执行当前相机 cancel 清理。
- [ ] 用户可用正/负相对角度多次调整，直到满意。
- [ ] 用户输入非法 float 时只提示重新输入，不退出当前端点调整。
- [ ] 每次移动完成后会输出当前内部绝对角度。
- [ ] 用户 `ok` 缓存端点后会输出“已缓存端点 N: xxx°”。
- [ ] 用户输入 `cancel` 时会结束本轮标定，不保存结果，当前 JSON 条目回退为默认值，并让镜头执行上电找 0、关闭串口并注销。
- [ ] 当前镜头无论正常完成、用户取消还是异常退出到相机输入阶段，都会执行上电找 0、关闭串口并注销。
- [ ] 正常完成全部标定后，`lens_registry` 应为空；如果仍有残留，会执行兜底清理并记录日志。
- [ ] `KeyboardInterrupt` 或普通异常退出时，会对 `lens_registry` 中残留镜头执行统一清理：上电找 0、关闭串口并注销。
- [ ] 已打开镜头会登记到 `lens_registry`，正常关闭后从注册表移除。
- [ ] 单个镜头清理失败不会阻止后续镜头继续清理。
- [ ] 计划明确说明关闭控制台、强杀进程、断电等硬终止无法可靠保证执行上电找 0。
- [ ] 移动后的端点记录统一来自电机内部真实角度。
- [ ] `[-1.0°, 0.0°)` 的微小负角会被规范化为 `0.0°`。
- [ ] 小于 `-1.0°` 的负角会被视为异常并退出当前相机标定。
- [ ] 碰壁时按临时映射识别边界：`0x0B=零点边界`、`0xF5=远端边界`，并记录原始状态码。
- [ ] 到达零点边界时提示用户输入正的相对角度。
- [ ] 到达远端边界时提示用户输入负的相对角度。
- [ ] 两个满意点会保存为 `ANGLE_START=min(...)`、`ANGLE_END=max(...)`。
- [ ] 单个相机标定成功后，会覆盖写入该相机 JSON 条目，并设置 `calibrated=true`。
- [ ] 完成数量等于相机数量时，输出全部完成日志但不直接退出，进入完成后命令循环。
- [ ] 完成后命令循环支持 `status`、`redo 相机name` 和 `back`，因此最后一台相机也可以被重做。
- [ ] 完成后输入 `back` 才会清理注册表残留并让 `--focus_calibration` 返回 `0`。
- [ ] `--focus_calibration --help` 只输出说明并返回 `0`，不会打开相机、打开串口、读写 JSON 或创建日志。
- [ ] `--focus_calibration --help` 会说明程序功能、启动准备、`reuse/reset/back`、相机 name 输入、相机核对、单镜头调焦、完成后命令和输出路径。
- [ ] `--full_shot --help` 只输出说明并返回 `0`，不会打开相机、打开串口、读写 JSON 或创建日志。
- [ ] `--full_shot --help` 会说明读取 `configs/lens_range_map.json`、默认范围提示、按镜头生成步进角度和输出目录规则。
- [ ] `--full_shot` 会读取 `configs/lens_range_map.json`。
- [ ] `--full_shot` 会按每个镜头自己的范围生成步进角度。
- [ ] `--full_shot` 遇到 `source="default"` 的可用条目时会使用默认范围并输出提示。
- [ ] `--full_shot` 支持同一步中不同相机移动到不同目标角度。
