# 多功能命令行入口设计计划

## 目标

将 `main.py` 设计为类似 `git` 的分层命令行入口，但当前阶段继续使用功能 flag 风格：

```powershell
python main.py --full_shot
python main.py --focus_calibration
```

新的帮助行为要求：

- 执行 `python main.py` 时，默认等价于 `python main.py -h` 或 `python main.py --help`，输出顶层帮助信息。
- 顶层帮助只展示当前支持的两种模式及其说明：
  - `--full_shot`：旋转多轮拍摄。
  - `--focus_calibration`：对焦范围标定。
- 顶层帮助不展开两种模式的全部业务参数。
- 执行 `python main.py --full_shot` 时，按 `full_shot` 默认参数执行旋转多轮拍摄。
- 执行 `python main.py --full_shot -h` 或 `python main.py --full_shot --help` 时，才输出 `full_shot` 的参数说明，不执行拍摄。
- 执行 `python main.py --focus_calibration` 时，执行对焦范围标定占位流程。
- 执行 `python main.py --focus_calibration -h` 或 `python main.py --focus_calibration --help` 时，才输出 `focus_calibration` 的说明信息，不执行占位流程。

## 顶层帮助设计

### 触发方式

以下命令都只输出顶层帮助：

```powershell
python main.py
python main.py -h
python main.py --help
```

顶层帮助只说明功能模式，不说明每个功能的详细参数。

建议输出结构：

```text
usage: main.py [-h] [--full_shot | --focus_calibration]

多功能采集控制脚本

可用模式:
  --full_shot          旋转多轮拍摄。查看参数: python main.py --full_shot --help
  --focus_calibration  对焦范围标定。查看说明: python main.py --focus_calibration --help

帮助:
  -h, --help           显示本帮助信息
```

### 顶层解析原则

顶层只识别第一个参数：

- 无参数：显示顶层帮助。
- `-h` 或 `--help`：显示顶层帮助。
- `--full_shot`：进入 `full_shot` 功能级解析。
- `--focus_calibration`：进入 `focus_calibration` 功能级解析。

顶层不直接定义 `--rotations`、`--speed`、`--delay` 等业务参数。

原因：

- 保持顶层帮助简洁。
- 让不同模式拥有自己的参数说明。
- 避免用户在不清楚模式的情况下看到过长参数列表。

## 功能级帮助设计

### `--full_shot` 帮助

以下命令输出 `full_shot` 的参数说明，不启动硬件：

```powershell
python main.py --full_shot -h
python main.py --full_shot --help
```

以下命令会执行真实旋转多轮拍摄流程：

```powershell
python main.py --full_shot
python main.py --full_shot --rotations 24 --speed 5 --delay 2
```

`full_shot` 帮助中展示原模式 2 的全部参数：

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--rotations`, `-r` | `int` | `12` | 转台旋转次数 |
| `--speed`, `-s` | `float` | `10.0` | 转台旋转速度，单位 `°/s`，范围 `0-30` |
| `--delay`, `-d` | `float` | `3.0` | 转台拍照后等待时间，单位秒 |
| `--error-signal` | `bool` | `true` | 是否屏蔽红外感应信号 |
| `--error-continue-model` | `int` | `2` | 异常处理模式：`0=继续运行`，`1=回到起始位置`，`2=默认` |
| `--host` | `str` | `192.168.1.88` | PLC IP 地址 |
| `--port`, `-p` | `int` | `502` | Modbus 端口 |
| `--lens-steps` | `int` | `5` | 镜头变焦步进次数 |
| `--lens-coordinate-mode` | `str` | `software` | 镜头坐标模式：`software` 或 `hardware` |
| `--verbose`, `-v` | flag | `False` | 显示详细日志 |

建议输出示例：

```text
usage: main.py --full_shot [options]

旋转多轮拍摄参数:
  -r, --rotations ROTATIONS     转台旋转次数，默认 12
  -s, --speed SPEED             转台旋转速度，默认 10.0，范围 0-30
  -d, --delay DELAY             拍照后等待时间，默认 3.0 秒
  --error-signal BOOL           是否屏蔽红外感应信号，默认 true
  --error-continue-model {0,1,2}异常处理模式，默认 2
  --host HOST                   PLC IP 地址，默认 192.168.1.88
  -p, --port PORT               Modbus 端口，默认 502
  --lens-steps LENS_STEPS       镜头变焦步进次数，默认 5
  --lens-coordinate-mode {software,hardware}
                                镜头坐标模式，默认 software
  -v, --verbose                 显示详细日志
  -h, --help                    显示 full_shot 参数说明

示例:
  python main.py --full_shot
  python main.py --full_shot --rotations 24 --speed 5 --delay 2
```

### `--focus_calibration` 帮助

以下命令输出 `focus_calibration` 的说明信息，不执行占位流程：

```powershell
python main.py --focus_calibration -h
python main.py --focus_calibration --help
```

执行：

```powershell
python main.py --focus_calibration
```

当前阶段执行占位逻辑：

```text
已选择：对焦范围标定
当前功能尚未实现，已保留入口。
已完成所有镜头焦距范围标定。
```

当前占位阶段不触发硬件动作。

## 参数解析结构

### 顶层解析

建议不要只用一个 `ArgumentParser` 放所有参数，而是拆成两个阶段：

1. 顶层解析：判断用户选择了哪个功能，或者是否请求顶层帮助。
2. 功能级解析：根据功能解析该功能自己的参数。

示意：

```python
def main(argv=None):
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
```

注意：这种写法要求功能 flag 是第一个参数，例如：

```powershell
python main.py --full_shot --rotations 24
```

不推荐：

```powershell
python main.py --rotations 24 --full_shot
```

原因是顶层需要先识别功能，再进入对应功能的 parser。

### 顶层帮助函数

建议新增：

```python
def print_top_level_help():
    print("""usage: main.py [-h] [--full_shot | --focus_calibration]

多功能采集控制脚本

可用模式:
  --full_shot          旋转多轮拍摄。查看参数: python main.py --full_shot --help
  --focus_calibration  对焦范围标定。查看说明: python main.py --focus_calibration --help

帮助:
  -h, --help           显示本帮助信息
""")
```

### `full_shot` parser

建议新增：

```python
def build_full_shot_parser():
    parser = argparse.ArgumentParser(
        prog="main.py --full_shot",
        description="旋转多轮拍摄",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--rotations", "-r", type=int, default=12, help="转台旋转次数")
    parser.add_argument("--speed", "-s", type=float, default=10.0, help="转台旋转速度")
    parser.add_argument("--delay", "-d", type=float, default=3.0, help="拍照后等待时间")
    parser.add_argument("--error-signal", type=parse_bool, default=True, help="是否屏蔽红外感应信号")
    parser.add_argument("--error-continue-model", type=int, default=2, choices=[0, 1, 2])
    parser.add_argument("--host", type=str, default="192.168.1.88")
    parser.add_argument("--port", "-p", type=int, default=502)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--lens-steps", type=int, default=5)
    parser.add_argument("--lens-coordinate-mode", choices=["software", "hardware"], default="software")
    return parser
```

### `full_shot` 处理函数

建议新增：

```python
def handle_full_shot_cli(argv):
    parser = build_full_shot_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    return run_rotation_multi_shot(args)
```

说明：

- `argparse` 默认支持 `-h` 和 `--help`。
- 当用户执行 `python main.py --full_shot -h` 或 `python main.py --full_shot --help` 时，`parser.parse_args(argv)` 会自动打印帮助并退出。
- 当用户执行 `python main.py --full_shot` 时，`argv` 为空，parser 会使用默认参数并进入 `run_rotation_multi_shot(args)`。

### `focus_calibration` 处理函数

建议新增：

```python
def print_focus_calibration_help():
    print("""usage: main.py --focus_calibration [-h]

对焦范围标定

当前功能尚未实现，已保留入口。

帮助:
  -h, --help           显示 focus_calibration 说明信息
""")


def handle_focus_calibration_cli(argv):
    if argv and argv[0] in ("-h", "--help"):
        print_focus_calibration_help()
        return 0

    if argv:
        print(f"未知参数: {' '.join(argv)}")
        print_focus_calibration_help()
        return 2

    return run_focus_range_calibration(None)
```

也可以为 `focus_calibration` 建一个简单 parser，但当前占位功能没有参数，手写帮助更轻。

## 原业务函数保留

### `run_focus_range_calibration(args)`

继续保留占位实现：

```python
def run_focus_range_calibration(args):
    print("\n已选择：对焦范围标定")
    print("当前功能尚未实现，已保留入口。")
    print("已完成所有镜头焦距范围标定。")
    return 0
```

### `run_rotation_multi_shot(args)`

继续保留当前旋转多轮拍摄流程。

要求：

- 不在函数内部解析命令行。
- 不在函数内部打印帮助。
- 只接收已经解析好的 `args` 并执行真实硬件流程。

## 程序流程

### 顶层帮助

```text
python main.py
  -> argv 为空
  -> print_top_level_help()
  -> return 0
```

```text
python main.py -h
  -> print_top_level_help()
  -> return 0
```

```text
python main.py --help
  -> print_top_level_help()
  -> return 0
```

### `full_shot` 帮助

```text
python main.py --full_shot -h
  -> handle_full_shot_cli(["-h"])
  -> build_full_shot_parser().print_help()
  -> return 0
```

```text
python main.py --full_shot --help
  -> handle_full_shot_cli(["--help"])
  -> build_full_shot_parser().print_help()
  -> return 0
```

### `full_shot` 执行

```text
python main.py --full_shot
  -> handle_full_shot_cli([])
  -> parse args with defaults
  -> run_rotation_multi_shot(args)
  -> return 0/1/130
```

```text
python main.py --full_shot --rotations 24 --speed 5
  -> handle_full_shot_cli(["--rotations", "24", "--speed", "5"])
  -> parse args
  -> run_rotation_multi_shot(args)
  -> return 0/1/130
```

### `focus_calibration`

```text
python main.py --focus_calibration
  -> handle_focus_calibration_cli([])
  -> run_focus_range_calibration(None)
  -> return 0
```

```text
python main.py --focus_calibration --help
  -> handle_focus_calibration_cli(["--help"])
  -> print_focus_calibration_help()
  -> return 0
```

## 需要删除或调整的旧设计

以下旧设计不再适用：

- 顶层 `ArgumentParser` 中同时放所有业务参数。
- 顶层 `--help` 展开 `--full_shot` 的所有业务参数。
- 旧版“`python main.py --full_shot` 默认只显示参数说明”的设计。
- 旧版 `--run` 确认参数设计。

以下函数建议调整或新增：

- 新增 `print_top_level_help()`。
- 新增 `build_full_shot_parser()`。
- 新增 `handle_full_shot_cli(argv)`。
- 新增 `print_focus_calibration_help()`。
- 新增 `handle_focus_calibration_cli(argv)`。
- 重写 `main(argv=None)`，按第一个参数分发。

## 实施步骤

1. 修改 `main(argv=None)`，让其直接读取 `sys.argv[1:]`。
2. 当 `argv` 为空、`-h` 或 `--help` 时，调用 `print_top_level_help()`。
3. 当第一个参数是 `--full_shot` 时，把剩余参数传给 `handle_full_shot_cli(argv[1:])`。
4. 当第一个参数是 `--focus_calibration` 时，把剩余参数传给 `handle_focus_calibration_cli(argv[1:])`。
5. 新增 `print_top_level_help()`，只展示 `--full_shot` 和 `--focus_calibration` 两种模式说明。
6. 新增 `build_full_shot_parser()`，把现有拍摄参数迁入该 parser。
7. 新增 `handle_full_shot_cli(argv)`：
   - `-h` 或 `--help` 交给 parser 自动输出帮助。
   - 无参数时解析默认参数并调用 `run_rotation_multi_shot(args)`。
   - 有业务参数时解析用户传入值并调用 `run_rotation_multi_shot(args)`。
8. 新增 `print_focus_calibration_help()` 和 `handle_focus_calibration_cli(argv)`：
   - `-h` 或 `--help` 打印 focus calibration 帮助。
   - 无参数时执行占位函数。
   - 其它未知参数返回错误。
9. 保留 `parse_bool()` 并继续用于 `--error-signal`。
10. 保留 `run_rotation_multi_shot(args)`。
11. 保留 `run_focus_range_calibration(args)`。
12. 执行静态检查和帮助路径验证。

## 验证计划

### 静态检查

```powershell
python -m py_compile main.py
```

检查点：

- 没有语法错误。
- `main()` 不再依赖顶层 parser 展示所有业务参数。
- `python main.py --full_shot -h` 不会进入真实硬件流程。
- `python main.py --full_shot` 会进入真实硬件流程。

### 顶层帮助验证

```powershell
python main.py
python main.py -h
python main.py --help
```

检查点：

- 三个命令都输出顶层帮助。
- 顶层帮助只展示 `--full_shot` 和 `--focus_calibration`。
- 顶层帮助不展示 `--rotations`、`--speed`、`--delay` 等 full_shot 参数。
- 不初始化相机、镜头或 PLC。

### `full_shot` 帮助验证

```powershell
python main.py --full_shot -h
python main.py --full_shot --help
```

检查点：

- 两个命令都输出 `full_shot` 参数帮助。
- 帮助中包含 `--rotations`、`--speed`、`--delay`、`--host`、`--lens-steps` 等参数。
- 不初始化相机、镜头或 PLC。

### `full_shot` 默认执行验证

```powershell
python main.py --full_shot
```

检查点：

- 使用默认参数进入 `run_rotation_multi_shot(args)`。
- 该命令会进入真实硬件流程，只有在确认相机、镜头和 PLC 可安全运行时才执行。

### `full_shot` 参数执行验证

```powershell
python main.py --full_shot --rotations 2 --lens-steps 2 --delay 0 --speed 5
```

检查点：

- `args.rotations == 2`。
- `args.lens_steps == 2`。
- `args.delay == 0`。
- `args.speed == 5.0`。
- 随后进入旋转多轮拍摄流程。

注意：该命令会进入真实硬件流程，只有在确认相机、镜头和 PLC 可安全运行时才执行。

### `focus_calibration` 占位验证

```powershell
python main.py --focus_calibration
```

检查点：

- 输出“已选择：对焦范围标定”。
- 输出“当前功能尚未实现，已保留入口。”。
- 输出“已完成所有镜头焦距范围标定。”。
- 不初始化相机、镜头或 PLC。

### `focus_calibration` 帮助验证

```powershell
python main.py --focus_calibration -h
python main.py --focus_calibration --help
```

检查点：

- 输出 focus calibration 帮助。
- 不执行占位函数。
- 不初始化相机、镜头或 PLC。

### 布尔参数解析验证

可以用纯解析方式验证，避免进入硬件流程：

```powershell
python -c "import main; p=main.build_full_shot_parser(); a=p.parse_args(['--error-signal','false']); print(a.error_signal)"
```

检查点：

- 输出 `False`。

## 风险与注意事项

1. 新设计中 `python main.py --full_shot` 会直接进入真实拍摄流程。
2. 查看 `full_shot` 参数说明必须使用 `python main.py --full_shot -h` 或 `python main.py --full_shot --help`。
3. 顶层只按第一个参数识别模式，所以功能参数必须放在第一个位置。
4. `--error-signal` 必须使用 `parse_bool`，避免 `false` 被解析为 `True`。
5. `ImageNode` 继续延迟导入，避免帮助命令依赖相机环境。
6. 如果后续要完全接近 `git`，可以升级为子命令形式：

```powershell
python main.py full-shot --rotations 12
python main.py focus-calibration
```
