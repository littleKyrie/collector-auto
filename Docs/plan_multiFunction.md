# 多功能入口模式设计计划

## 目标

在 `main.py` 启动后，先进入“程序模式选择”阶段，由控制台展示可用模式的选择 id 和模式名称，再根据用户输入执行对应功能。

本阶段设计三个模式：

- `id0`：退出程序
- `id1`：对焦范围标定，目前先保留函数签名和占位实现
- `id2`：旋转多轮拍摄，也就是当前 `main.py` 中已有的完整自动化流程

设计重点是把入口调度与具体业务流程拆开，让后续新增其它模式时，只需要注册新的模式函数，不需要继续堆叠 `main()`。

程序整体使用“顶层模式选择 + 模式内流程”的结构。用户在顶层选择 `id0` 时直接退出；用户选择 `id1` 执行对焦范围标定占位流程后返回顶层模式选择；用户选择 `id2` 后进入模式 2 的参数确认界面，在该界面选择 `0` 会返回上一层模式选择界面；如果选择使用默认参数或手动修改参数并完成旋转多轮拍摄，则直接退出整个程序。

## 当前入口现状

当前 `main.py` 的 `main()` 同时负责：

1. 定义和解析命令行参数。
2. 生成镜头步进角度。
3. 初始化成像系统。
4. 连接 PLC 转台。
5. 定义转台到位后的拍摄回调。
6. 创建 `RotationController` 并执行 `run_rotation_sequence()`。
7. 在 `finally` 中断开 PLC 和关闭相机系统。

这些逻辑整体上就是“旋转多轮拍摄”模式，后续应封装为 `id2` 对应的模式函数。

## 模式定义

建议在 `main.py` 中定义模式表，集中管理模式 id、模式名称和处理函数。

```python
MODE_EXIT = 0
MODE_FOCUS_RANGE_CALIBRATION = 1
MODE_ROTATION_MULTI_SHOT = 2

PROGRAM_MODES = {
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
```

控制台展示效果建议为：

```text
可选择的程序模式如下:
  0. 退出程序
  1. 对焦范围标定
  2. 旋转多轮拍摄
请输入模式 id:
```

## 函数拆分方案

### 1. 保留 `main()` 作为总入口

`main()` 只负责：

1. 解析命令行参数。
2. 展示模式菜单。
3. 读取并校验用户输入。
4. 根据模式 id 分发到对应处理函数。
5. 返回进程退出码。

建议结构：

```python
def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    while True:
        selected_mode = prompt_program_mode(PROGRAM_MODES)
        if selected_mode == MODE_EXIT:
            print("已选择退出程序。")
            return 0

        mode = PROGRAM_MODES[selected_mode]
        result = mode["handler"](args)

        if result == MODE_BACK_TO_MODE_SELECT:
            continue

        return result
```

### 2. 新增 `parse_args()`

把当前 `main()` 中的 `argparse.ArgumentParser` 创建和 `parser.parse_args()` 迁移到独立函数。

```python
def parse_args():
    parser = argparse.ArgumentParser(...)
    ...
    return parser.parse_args()
```

收益：

- `main()` 更短，只保留入口流程。
- 所有模式可以共享命令行参数。
- 后续如果某些模式需要专属参数，可以继续在同一个 parser 中扩展。

### 3. 新增 `prompt_program_mode()`

负责控制台展示和输入校验。

建议行为：

1. 打印所有模式 id 和模式名称。
2. 使用 `input()` 读取用户输入。
3. 去掉首尾空格。
4. 校验是否为整数。
5. 校验整数是否存在于模式表。
6. 输入非法时提示错误并重新输入。
7. 用户按 `Ctrl+C` 时向外抛出或返回退出码，由 `main()` 统一处理。

示例结构：

```python
def prompt_program_mode(modes):
    while True:
        print("\n请选择程序模式:")
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
```

### 4. 新增 `run_focus_range_calibration(args)`

该函数是 `id1` 的处理函数，用于后续实现“对焦范围标定”。目前还没有具体业务逻辑，先生成函数签名和占位输出。

建议函数边界：

```python
def run_focus_range_calibration(args):
    print("\n已选择模式 1：对焦范围标定")
    print("当前功能尚未实现，已保留入口。")
    return MODE_BACK_TO_MODE_SELECT
```

行为要求：

- 当前阶段不初始化相机、镜头或 PLC。
- 当前阶段不创建输出目录。
- 执行占位逻辑后返回上一层模式选择界面。
- 后续实现真实标定流程时，如果该流程只是单次标定工具，建议执行结束后仍返回 `MODE_BACK_TO_MODE_SELECT`，方便用户继续选择其它模式。

### 5. 新增 `run_rotation_multi_shot(args)`

把当前 `main()` 中从“生成镜头角度”开始，到硬件初始化、PLC 连接、回调定义、旋转序列执行和资源清理的现有逻辑整体移动到该函数。

该函数就是 `id2` 的处理函数。

建议函数边界：

```python
def run_rotation_multi_shot(args):
    target_angles = generate_step_angles(args.lens_steps)
    sys_dev = None
    client = None

    try:
        # 初始化成像系统
        # 连接 PLC
        # 定义 progress_callback
        # 创建 RotationController
        # validate_parameters()
        # run_rotation_sequence()
        return 0 or 1
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        logger.error(...)
        return 1
    finally:
        # 仅在对象已创建时清理
        if client:
            client.disconnect()
        if sys_dev:
            sys_dev.close_all()
```

注意：因为模式选择发生在硬件初始化之前，所以用户选择 `id0` 或执行当前占位版 `id1` 时，不应创建 `ImagingSystem`、不应连接 PLC，也不应执行任何镜头归零动作。

## 程序流程设计

### 启动后选择退出

```text
python main.py
  -> parse_args()
  -> 展示模式菜单
  -> 用户输入 0
  -> 打印退出提示
  -> return 0
```

预期行为：

- 不初始化相机。
- 不初始化镜头。
- 不连接 PLC。
- 不创建输出目录。
- 进程正常退出。

### 启动后选择对焦范围标定

```text
python main.py
  -> parse_args()
  -> 展示模式菜单
  -> 用户输入 1
  -> run_focus_range_calibration(args)
  -> 打印占位提示
  -> return MODE_BACK_TO_MODE_SELECT
  -> 回到上一层模式选择界面
```

预期行为：

- 控制台展示“对焦范围标定”模式已选择。
- 当前阶段只输出“功能尚未实现/入口已保留”的提示。
- 不初始化相机。
- 不初始化镜头。
- 不连接 PLC。
- 执行结束后返回上一层模式选择界面，而不是退出程序。

### 启动后选择旋转多轮拍摄

```text
python main.py
  -> parse_args()
  -> 展示模式菜单
  -> 用户输入 2
  -> 展示模式 2 可配置参数及说明
  -> 展示模式 2 子菜单: 0=退出当前模式, 1=使用默认参数, 2=手动修改参数
  -> 用户输入 0: 返回上一层模式选择界面
  -> 用户输入 1: 直接使用默认/命令行已有参数
  -> 用户输入 2: 一次性输入需要覆盖的参数
  -> 手动修改时，已输入参数使用输入值，未输入参数使用默认/命令行已有值
  -> run_rotation_multi_shot(args)
  -> 执行当前已有完整流程
  -> return 0/1/130
```

预期行为：

- 当前命令行参数继续生效，例如 `--rotations`、`--speed`、`--delay`、`--lens-steps`。
- 模式 2 启动硬件前，先让用户选择退出当前模式、使用默认参数或手动修改参数。
- 用户在模式 2 子菜单选择 `0` 时，不初始化相机、不连接 PLC，直接返回上一层模式选择界面。
- 用户选择 `1` 时，直接使用当前默认值或命令行传入值执行。
- 用户选择 `2` 时，才进入参数覆盖输入；输入的参数覆盖当前值，未输入的参数继续使用当前默认值或命令行传入值。
- 当前镜头步进、拍摄、PLC 转台控制逻辑保持不变。
- 当前异常处理和资源释放语义保持不变。
- 模式 2 只要进入实际旋转拍摄流程，执行结束后直接退出程序，不返回模式选择菜单。

## 模式 2 参数输入设计

### 交互目标

选择 `id2` 后，先展示当前可配置参数、当前值、默认含义和输入格式。随后显示模式 2 的子菜单，让用户选择：

- `0`：退出当前模式，返回上一层模式选择界面。
- `1`：使用默认参数，也就是当前 `args` 中的参数值，直接进入旋转拍摄流程。
- `2`：手动修改参数。此时才提示用户按 `参数名 参数值` 的格式输入想修改的参数。

这里的“当前值”建议定义为：

1. 如果启动命令行传入了参数，则当前值为命令行传入值。
2. 如果启动命令行没有传入参数，则当前值为 `argparse` 中配置的默认值。

这样可以同时兼容命令行运行和控制台交互运行。

注意：模式 2 子菜单中的 `0` 不是退出整个程序，而是退出当前模式并返回顶层模式选择界面。顶层模式选择中的 `id0` 才是退出整个程序。

### 展示内容

建议在模式 2 内部新增函数 `print_rotation_multi_shot_params(args)`，输出类似：

```text
已选择模式 2：旋转多轮拍摄

当前可配置参数：
  rotations              旋转次数，整数，当前值: 12
  speed                  转台旋转速度，0-30，当前值: 10.0
  delay                  拍照后等待时间，单位秒，当前值: 3.0
  error_signal           是否屏蔽红外感应信号，true/false，当前值: true
  error_continue_model   异常处理模式，0=继续运行, 1=回到起始位置, 2=默认，当前值: 2
  host                   PLC IP 地址，当前值: 192.168.1.88
  port                   Modbus 端口，当前值: 502
  lens_steps             镜头变焦步进次数，整数，当前值: 5
  lens_coordinate_mode   镜头坐标模式，software/hardware，当前值: software

输入格式：
  参数名 参数值, 参数名 参数值

示例：
  rotations 24, speed 5, delay 2
  host 192.168.1.100，port 502，lens_steps 3

请选择操作：
  0. 退出当前模式，返回模式选择
  1. 使用默认参数
  2. 手动修改参数
请输入操作 id：
```

当用户选择 `2` 后，再展示参数输入提示：

```text
请输入需要修改的参数：
  格式：参数名 参数值, 参数名 参数值
  示例：rotations 24, speed 5, delay 2
  未输入的参数将继续使用当前值。
请输入参数：
```

### 输入格式

用户输入由多个参数分组组成：

- 每个分组表示一个参数修改。
- 分组内部用空格分隔参数名和参数值。
- 不同分组之间用英文逗号 `,` 或中文逗号 `，` 分隔。
- 参数名建议先使用 `argparse` 中的英文参数名，避免中文别名带来歧义。
- 参数名不需要带 `--` 前缀；如果用户输入了 `--rotations`，可以兼容去掉前缀。

有效输入示例：

```text
rotations 24, speed 5, delay 2
```

```text
host 192.168.1.100，port 502，lens_steps 3
```

```text
error_signal false, error_continue_model 0, lens_coordinate_mode software
```

手动修改参数时，空输入：

```text

```

表示不覆盖任何参数，等价于使用当前参数执行。为了交互语义更清晰，推荐用户想直接使用默认参数时在模式 2 子菜单选择 `1`。

### 参数解析方案

建议新增函数：

```python
MODE2_BACK_TO_MODE_SELECT = "back"
MODE2_RUN = "run"

def configure_rotation_multi_shot_args(args):
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


def prompt_rotation_multi_shot_overrides(args):
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
```

其中：

- `configure_rotation_multi_shot_args(args)` 负责模式 2 子菜单。
- 返回 `MODE2_BACK_TO_MODE_SELECT` 表示回到顶层模式选择，不执行模式 2。
- 返回 `MODE2_RUN` 表示参数已确定，可以执行模式 2。
- `prompt_rotation_multi_shot_overrides(args)` 只在用户选择 `2` 时调用，负责手动输入参数。
- `parse_param_overrides(raw_value)` 负责把用户输入解析为 `{参数名: 参数值字符串}`。
- `apply_rotation_multi_shot_overrides(args, overrides)` 负责类型转换、合法性校验和写回 `args`。

### 分组解析规则

建议解析流程：

1. 把中文逗号 `，` 替换为英文逗号 `,`。
2. 按 `,` 拆分多个分组。
3. 对每个分组执行 `strip()`。
4. 跳过空分组。
5. 用空白字符拆分分组，最多拆成两段：参数名、参数值。
6. 如果某个分组不是两段，则抛出参数格式错误，由手动参数输入流程提示用户重新输入。
7. 参数名去掉可选的 `--` 前缀。
8. 参数名统一转换为下划线形式，例如 `lens-steps` 转为 `lens_steps`。

示意代码：

```python
def parse_param_overrides(raw_value):
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
        overrides[name] = value.strip()

    return overrides
```

如果同一个参数重复输入，建议以最后一次输入为准，并打印提示：

```text
参数 rotations 重复输入，使用最后一次值: 24
```

### 支持的参数白名单

为了避免用户误输入覆盖任意对象属性，必须维护参数白名单。

建议定义：

```python
ROTATION_MULTI_SHOT_PARAM_SPECS = {
    "rotations": {
        "label": "旋转次数",
        "type": int,
        "min": 1,
    },
    "speed": {
        "label": "转台旋转速度",
        "type": float,
        "min": 0,
        "max": 30,
    },
    "delay": {
        "label": "拍照后等待时间",
        "type": float,
        "min": 0,
    },
    "error_signal": {
        "label": "是否屏蔽红外感应信号",
        "type": bool,
    },
    "error_continue_model": {
        "label": "异常处理模式",
        "type": int,
        "choices": [0, 1, 2],
    },
    "host": {
        "label": "PLC IP 地址",
        "type": str,
    },
    "port": {
        "label": "Modbus 端口",
        "type": int,
        "min": 1,
        "max": 65535,
    },
    "lens_steps": {
        "label": "镜头变焦步进次数",
        "type": int,
        "min": 1,
    },
    "lens_coordinate_mode": {
        "label": "镜头坐标模式",
        "type": str,
        "choices": ["software", "hardware"],
    },
}
```

### 类型转换规则

建议新增 `convert_param_value(name, value, spec)`：

- `int`：使用 `int(value)`，失败时提示参数必须是整数。
- `float`：使用 `float(value)`，失败时提示参数必须是数字。
- `str`：保留字符串。
- `bool`：支持大小写不敏感输入：
  - 真：`true`、`1`、`yes`、`y`、`on`
  - 假：`false`、`0`、`no`、`n`、`off`

中文布尔值可选支持：

- 真：`是`、`真`
- 假：`否`、`假`

### 校验规则

写回 `args` 前需要校验：

1. 参数名必须存在于 `ROTATION_MULTI_SHOT_PARAM_SPECS`。
2. 参数值必须能转换为目标类型。
3. 如果配置了 `min`，则值不能小于最小值。
4. 如果配置了 `max`，则值不能大于最大值。
5. 如果配置了 `choices`，则值必须在可选范围内。

如果任意参数解析失败，不启动硬件，而是回到手动参数输入界面，提示用户重新输入参数。

这样可以避免输入错误后仍然继续初始化相机或 PLC。

### 写回与确认

用户选择 `1` 使用默认参数，或选择 `2` 且参数输入成功解析并写回后，建议打印最终参数确认：

```text
最终运行参数：
  rotations: 24
  speed: 5.0
  delay: 2.0
  error_signal: True
  error_continue_model: 2
  host: 192.168.1.88
  port: 502
  lens_steps: 5
  lens_coordinate_mode: software
```

然后直接执行旋转多轮拍摄流程，不再二次确认。

### 模式 2 函数接入位置

`run_rotation_multi_shot(args)` 的第一步应是参数配置。只有当用户在模式 2 子菜单选择 `1` 或选择 `2` 且参数输入成功后，才生成镜头角度、初始化相机和连接 PLC。

```python
def run_rotation_multi_shot(args):
    status, args = configure_rotation_multi_shot_args(args)
    if status == MODE2_BACK_TO_MODE_SELECT:
        return MODE_BACK_TO_MODE_SELECT

    target_angles = generate_step_angles(args.lens_steps)

    # 后面继续执行原有硬件初始化和旋转拍摄逻辑
```

如果用户在参数输入阶段按 `Ctrl+C`，建议返回 `130`，并且不初始化任何硬件。

### 顶层模式选择接入

因为模式 1 占位流程执行结束后需要返回上一层模式选择，并且模式 2 子菜单的 `0` 也需要返回上一层模式选择，所以 `main()` 需要允许“返回顶层菜单”这一种特殊结果。

建议定义：

```python
MODE_BACK_TO_MODE_SELECT = "back_to_mode_select"
```

`main()` 可以写成有限循环：

```python
def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    while True:
        selected_mode = prompt_program_mode(PROGRAM_MODES)
        if selected_mode == MODE_EXIT:
            print("已选择退出程序。")
            return 0

        mode = PROGRAM_MODES[selected_mode]
        result = mode["handler"](args)

        if result == MODE_BACK_TO_MODE_SELECT:
            continue

        return result
```

这个循环只用于支持“退出当前模式并返回上一层”。当前 `id1` 占位函数会返回上一层；实际执行完 `id2` 后，handler 返回正常退出码，`main()` 立即退出程序，不会再次展示模式菜单。

## 实施步骤

1. 在 `main.py` 顶部常量区新增模式 id 常量。
2. 将当前 `main()` 中的 argparse 代码抽出为 `parse_args()`。
3. 新增 `prompt_program_mode(modes)`。
4. 新增 `MODE_BACK_TO_MODE_SELECT` 特殊返回值。
5. 新增 `run_focus_range_calibration(args)` 占位函数，打印“对焦范围标定尚未实现”，并返回 `MODE_BACK_TO_MODE_SELECT`。
6. 将当前 `main()` 中的业务流程移动到 `run_rotation_multi_shot(args)`。
7. 新增 `ROTATION_MULTI_SHOT_PARAM_SPECS`，集中描述模式 2 支持的参数、类型、说明和校验规则。
8. 新增 `print_rotation_multi_shot_params(args)`，在模式 2 启动后展示可输入参数。
9. 新增 `parse_param_overrides(raw_value)`，支持空格分隔参数名和值，支持中英文逗号分隔多个参数组。
10. 新增 `apply_rotation_multi_shot_overrides(args, overrides)`，完成参数白名单校验、类型转换、范围校验和写回。
11. 新增 `prompt_rotation_multi_shot_overrides(args)`，只在用户选择手动修改参数时循环处理参数输入，直到解析成功。
12. 新增 `configure_rotation_multi_shot_args(args)`，负责模式 2 子菜单：`0` 返回上一层、`1` 使用默认参数、`2` 手动修改参数。
13. 在 `run_rotation_multi_shot(args)` 第一行调用 `configure_rotation_multi_shot_args(args)`，如果返回 `MODE_BACK_TO_MODE_SELECT` 则不初始化硬件并返回上一层。
14. 在 `run_rotation_multi_shot(args)` 内保留原有 `progress_callback()` 内部函数。
15. 重写 `main()`，让它只做参数解析、模式选择和模式分发，并通过有限循环支持从模式 1 或模式 2 返回上一层模式选择。
16. 确保顶层 `id0` 直接退出程序；`id1` 占位流程结束后返回上一层；`id2` 如果实际执行旋转拍摄，则执行结束后直接返回退出码，不再进入循环菜单。
17. 在文件底部继续保留 `if __name__ == '__main__': sys.exit(main())`。

## 验证计划

### 静态检查

```powershell
python -m py_compile main.py
```

检查点：

- 没有语法错误。
- `PROGRAM_MODES` 中 `id1` 的 handler 能引用到已定义的 `run_focus_range_calibration()`。
- `PROGRAM_MODES` 中 `id2` 的 handler 能引用到已定义的 `run_rotation_multi_shot()`。
- `main()` 返回整数退出码。

### 退出模式验证

```powershell
python main.py
```

输入：

```text
0
```

检查点：

- 控制台展示 `0. 退出程序`、`1. 对焦范围标定` 和 `2. 旋转多轮拍摄`。
- 输入 `0` 后直接退出。
- 没有出现成像系统初始化、PLC 连接、镜头归零等日志。
- 退出码为 `0`。

### 对焦范围标定占位模式验证

```powershell
python main.py
```

输入：

```text
1
0
```

检查点：

- 输入 `1` 后进入“对焦范围标定”模式。
- 控制台提示当前功能尚未实现或入口已保留。
- 模式 1 结束后返回上一层模式选择界面。
- 返回上一层后输入 `0`，程序退出。
- 整个过程中不初始化相机、镜头或 PLC。

### 旋转多轮拍摄模式验证

```powershell
python main.py --rotations 2 --lens-steps 2 --delay 0 --verbose
```

输入：

```text
2
2
speed 5, delay 1
```

检查点：

- 控制台展示模式菜单。
- 输入 `2` 后先展示模式 2 的可配置参数清单。
- 在模式 2 子菜单输入 `2` 后进入手动参数输入。
- 输入 `speed 5, delay 1` 后，`speed` 和 `delay` 使用用户输入值。
- 未输入的 `rotations` 和 `lens_steps` 仍按命令行传入值生效。
- 原有相机、镜头、PLC、转台、拍摄流程不因入口拆分而改变。
- 异常或用户中断时仍能清理 PLC 和相机资源。
- 模式 2 执行结束后直接退出程序。

### 模式 2 使用默认参数验证

```powershell
python main.py --rotations 2 --lens-steps 2 --delay 0
```

输入：

```text
2
1
```

检查点：

- 输入 `2` 后展示参数清单。
- 在模式 2 子菜单输入 `1`。
- 程序使用当前参数执行，其中 `rotations=2`、`lens_steps=2`、`delay=0`。

### 模式 2 退出当前模式验证

```powershell
python main.py
```

输入：

```text
2
0
0
```

检查点：

- 第一次输入 `2` 后进入模式 2 参数说明和子菜单。
- 在模式 2 子菜单输入 `0` 后返回上一层模式选择。
- 返回上一层后再次输入 `0`，程序退出。
- 整个过程中没有初始化相机、镜头或 PLC。

### 模式 2 中文逗号输入验证

```powershell
python main.py
```

输入：

```text
2
2
rotations 3，speed 5，lens_steps 2
```

检查点：

- 中文逗号可以被正确识别为分组分隔符。
- 最终参数中 `rotations=3`、`speed=5.0`、`lens_steps=2`。

### 模式 2 非法参数验证

```powershell
python main.py
```

输入：

```text
2
2
speed abc
speed 5
```

检查点：

- 在模式 2 子菜单输入 `2` 后进入手动参数输入。
- `speed abc` 提示 `speed` 必须是数字。
- 参数错误时不初始化相机、不连接 PLC。
- 重新输入 `speed 5` 后才继续执行模式 2。

### 模式 2 未知参数验证

输入：

```text
2
2
foo 123
rotations 2
```

检查点：

- 在模式 2 子菜单输入 `2` 后进入手动参数输入。
- `foo 123` 提示未知参数。
- 程序回到手动参数输入阶段。
- 重新输入合法参数后继续执行。

### 非法输入验证

依次输入：

```text
abc
99
2
1
```

检查点：

- `abc` 提示输入必须为数字。
- `99` 提示未知模式 id。
- 输入 `2` 后进入模式 2。
- 在模式 2 子菜单输入 `1` 后使用默认参数正常进入旋转多轮拍摄。

## 风险与注意事项

1. 当前所有命令行参数仍会在模式选择前解析；这意味着即使用户最终选择退出，错误的命令行参数也会先被 argparse 拦截。这是合理行为。
2. `input()` 会让程序从原来的“启动即执行”变为“启动后等待顶层模式选择，并在模式 2 内等待参数操作选择”，如果未来需要自动化运行，可以再新增 `--mode 2` 和 `--no-interactive` 参数跳过交互。
3. `PROGRAM_MODES` 必须在 `run_focus_range_calibration()` 和 `run_rotation_multi_shot()` 定义之后创建，或者只保存 handler 名称后延迟解析，否则 Python 会在模块加载时遇到未定义函数。
4. `finally` 中需要判断 `client` 和 `sys_dev` 是否已经创建，避免初始化中途失败时清理逻辑访问未定义变量。
5. 模式选择阶段不要提前初始化硬件，否则 `id0` 退出模式就失去意义。
6. 模式 2 子菜单和参数输入阶段都不要提前初始化硬件，否则用户选择返回上一层或参数输错时会产生不必要的硬件动作。
7. 参数解析必须使用白名单，不能把用户输入的任意参数名直接 `setattr(args, name, value)`。
8. 参数值中如果未来需要支持带空格的路径或名称，当前“空格分隔参数名和值”的格式需要扩展；本阶段参数值都不需要包含空格。

## 后续扩展建议

后续新增模式时，建议只新增独立函数并注册到 `PROGRAM_MODES`，例如：

```python
MODE_LENS_TEST = 3

PROGRAM_MODES[MODE_LENS_TEST] = {
    "name": "镜头单独调试",
    "handler": run_lens_test,
}
```

这样主入口不需要反复修改 `if/elif` 分支，模式列表也会自动反映最新功能。
