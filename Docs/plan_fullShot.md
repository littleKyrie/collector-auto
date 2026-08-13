# `full_shot` 自定义输出路径与配置路径改造计划

## 1. 改造目标

本计划只调整 `--full_shot` 模式，为其增加两个命令行参数：

- `--output_path`：指定本次拍摄的图片输出根目录。
- `--config_path`：指定本次拍摄读取的镜头范围配置文件。

同时改变图片输出目录的初始化规则：每次 `full_shot` 正式开始写入图片前，都要先清空本次选定的输出目录，避免旧图片残留，也不再依赖同名文件覆盖。

本计划不改变图片命名、图片格式、日志根目录、焦距标定流程和 PLC 控制流程。

## 2. 当前实现

相关代码主要位于：

- `main.py`
  - `build_full_shot_parser()`：定义 `full_shot` 参数和帮助信息。
  - `handle_full_shot_cli()`：解析参数并调用拍摄流程。
  - `run_rotation_multi_shot(args)`：读取镜头范围配置、初始化设备并执行旋转拍摄。
  - `LENS_RANGE_MAP_PATH`：当前固定为 `configs/lens_range_map.json`。
- `ImageNode.py`
  - `serial_snap_rotation_step()` 和 `parallel_snap_rotation_step()` 已接收 `output_root`，并在其下按相机名创建目录。

当前图片输出根目录在 `run_rotation_multi_shot()` 中硬编码为：

```python
output_root = os.path.abspath("./Output")
```

当前镜头范围配置通过以下调用读取：

```python
lens_range_map = load_lens_range_map() or {}
```

由于未传入路径，实际使用全局默认值 `configs/lens_range_map.json`。

当前图片结构已经是：

```text
Output/{camera_user_id}/Position{i}_Step{j}.bmp
```

但运行开始前不会清空 `Output`，因此同名图片会被覆盖，不同参数运行遗留的其他图片仍可能保留。

## 3. 参数设计

### 3.1 `--output_path`

参数定义：

```text
--output_path PATH
```

语义：

- `PATH` 是图片输出根目录，而不是某一台相机的目录。
- 未提供时，默认使用项目根目录下的 `Output`。
- 提供相对路径时，以启动脚本所在的项目根目录为基准解析，避免结果随当前工作目录变化。
- 提供绝对路径时直接使用该路径，并进行规范化。
- 路径不存在时创建目录。
- 路径已经存在且不为空时，在本次运行写入第一张图片前清空其全部内容，但保留输出根目录本身。
- 路径存在但不是目录时，输出明确错误并终止，不进入拍摄。

示例：

```powershell
python main.py --full_shot --output_path C://results
```

假设当前读取到的相机名为 `1`、`2`，图片分别保存到：

```text
C://results/1/Position1_Step1.bmp
C://results/2/Position1_Step1.bmp
```

后续阵位和镜头步进继续使用现有命名规则：

```text
{output_path}/{camera_user_id}/Position{i}_Step{j}.bmp
```

### 3.2 `--config_path`

参数定义：

```text
--config_path FILE
```

本计划将 `config_path` 明确定义为“具体配置文件路径”，而不是配置目录。这样可以避免一个目录中存在多个 JSON 文件时产生歧义。

语义：

- 未提供时，默认读取项目根目录下的 `configs/lens_range_map.json`，保持现有行为。
- 提供相对路径时，以启动脚本所在的项目根目录为基准解析。
- 提供绝对路径时直接使用该文件。
- `full_shot` 只读取该文件，不创建或改写该文件。
- 自定义文件不存在时，沿用当前 `full_shot` 的行为：输出包含实际路径的告警，并为相关相机使用默认焦距范围；不得静默改回默认配置文件。
- 路径存在但不是普通文件、无读取权限或 JSON 格式错误时，输出包含实际路径和原因的错误并终止本次拍摄，避免把损坏配置静默当成默认配置继续执行。
- JSON 可以正常读取、但缺少某台相机条目或该条目未完成标定时，继续沿用现有的逐相机默认焦距范围逻辑。

示例：

```powershell
python main.py --full_shot --config_path C://camera-configs/lens_range_map.json
```

该参数只影响 `full_shot`。`--focus_calibration` 继续使用其现有配置路径和交互流程，本次不扩展其参数。

### 3.3 完整示例

```powershell
python main.py --full_shot `
  --output_path C://results `
  --config_path C://camera-configs/lens_range_map.json `
  --rotations 12 `
  --lens-steps 5
```

`--full_shot --help` 中应展示两个参数的用途、默认值和路径解析规则。执行帮助命令不得创建、读取或清空任何业务目录。

## 4. 输出目录清理设计

### 4.1 清理时机

输出目录只能在一次 `full_shot` 运行中清理一次，不能放入阵位循环、镜头步进循环或单相机保存函数中。

建议时序：

1. 解析并规范化 `output_path`、`config_path`。
2. 校验路径安全性和命令行参数。
3. 初始化相机、镜头和 PLC，并确认可以进入旋转序列。
4. 在调用 `controller.run_rotation_sequence(...)` 前，调用一次输出目录准备函数。
5. 准备成功后才允许进入第一轮拍摄。

这样既满足“写入前清空”，也能减少设备初始化或连接失败时提前删除历史结果的概率。如果清理失败，必须终止本次运行，并通过现有 `finally` 流程关闭硬件资源。

### 4.2 清理函数

在 `main.py` 中增加职责单一、可独立测试的辅助函数，例如：

```python
def prepare_output_directory(output_root, project_root):
    """校验、创建或清空 full_shot 图片输出根目录。"""
```

行为要求：

- 目录不存在：递归创建。
- 空目录：直接使用。
- 非空目录：删除目录内所有文件和子目录，然后继续使用同一个根目录。
- 普通文件和符号链接：删除链接或文件本身。
- 目录链接或 junction：只移除链接，不跟随到链接目标中递归删除。
- 任一内容删除失败：停止处理，报告失败对象，不得带着“部分旧文件 + 部分新图片”的状态继续拍摄。
- 控制台输出最终使用的绝对路径，并说明目录是新建、原本为空还是已清理。

### 4.3 危险路径保护

由于该参数会触发递归清理，必须在删除前拒绝明显危险的目标：

- Windows 盘符根目录，例如 `C:\`。
- 文件系统根目录。
- 项目根目录本身。
- 空字符串、`.` 或规范化后等同于项目根目录的路径。
- 无法解析为稳定绝对路径的目标。

清理范围只能是最终解析出的 `output_root` 的直接内容。日志目录和 `config_path` 不属于清理范围。自定义 `output_path` 与配置文件或其父目录发生危险重叠时也应拒绝运行，避免清理配置文件。

## 5. 代码改动计划

### 5.1 统一项目根路径

在 `main.py` 中增加基于 `__file__` 的项目根路径，例如：

```python
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_PATH = os.path.join(PROJECT_ROOT, "Output")
DEFAULT_CONFIG_PATH = os.path.join(PROJECT_ROOT, "configs", "lens_range_map.json")
```

保留 `LENS_RANGE_MAP_PATH` 给现有焦距标定流程使用，或将其指向 `DEFAULT_CONFIG_PATH`，但不能再让 `full_shot` 拍摄流程直接依赖该全局固定路径。

### 5.2 扩展 `full_shot` 参数解析器

在 `build_full_shot_parser()` 中增加：

```python
parser.add_argument(
    "--output_path",
    default=DEFAULT_OUTPUT_PATH,
    help="图片输出根目录（默认: 项目根目录/Output）",
)

parser.add_argument(
    "--config_path",
    default=DEFAULT_CONFIG_PATH,
    help="镜头范围配置文件（默认: 项目根目录/configs/lens_range_map.json）",
)
```

参数名称以需求指定的下划线形式为准，不新增不必要的缩写。

### 5.3 解析和校验路径

新增路径解析辅助函数，将相对路径统一转换为相对于 `PROJECT_ROOT` 的绝对路径。解析完成后把规范化结果保存回 `args.output_path` 和 `args.config_path`，保证后续帮助信息、日志、metadata 和错误信息使用同一个实际路径。

路径校验应在任何清理操作前完成。`config_path` 只允许作为文件读取；`output_path` 只允许作为目录使用。

### 5.4 替换配置读取路径

将 `run_rotation_multi_shot(args)` 中的：

```python
lens_range_map = load_lens_range_map() or {}
```

调整为显式使用：

```python
lens_range_map = load_lens_range_map(args.config_path) or {}
```

同时补充配置读取异常的定向处理，使错误信息包含 `args.config_path`。配置文件不存在时保留现有的默认焦距范围行为；文件不可读或 JSON 非法时明确终止。不能使用宽泛异常让路径或 JSON 错误直接变成难以定位的“程序异常”。

现有“配置缺失或相机条目不可用时使用默认焦距范围”的逐相机逻辑保持不变。

### 5.5 替换图片输出根目录

删除拍摄流程中的硬编码：

```python
output_root = os.path.abspath("./Output")
```

改为使用已经规范化的：

```python
output_root = args.output_path
```

在进入 `run_rotation_sequence()` 前调用一次 `prepare_output_directory()`。`ImageNode.py` 的两个 rotation step 保存函数已经接收 `output_root`，因此预计无需修改其图片目录生成逻辑。

### 5.6 同步 metadata

当前 step metadata 中有两个硬编码字段：

```json
{
  "lens_range_map_path": "configs/lens_range_map.json",
  "image_path_pattern": "Output/{camera_user_id}/Position{i}_Step{j}.bmp"
}
```

改为记录本次运行实际解析后的路径：

```json
{
  "lens_range_map_path": "<resolved config_path>",
  "image_path_pattern": "<resolved output_path>/{camera_user_id}/Position{i}_Step{j}.bmp"
}
```

路径字段应使用统一的字符串转换方式，避免 Windows 分隔符在日志和 JSON 中表现不一致。

### 5.7 更新用户文档

实施功能时同步更新：

- `main.py --full_shot --help` 的示例、镜头范围说明和输出说明。
- `README.md` 的参数表、图片输出结构和“同名覆盖”说明。

README 中现有图片目录示例与当前代码已经不一致，更新时应直接采用实际结构：

```text
{output_path}/{camera_user_id}/Position{i}_Step{j}.bmp
```

## 6. 预计影响文件

- `main.py`
  - 新增路径默认值、解析/校验和输出目录准备函数。
  - 新增两个 CLI 参数。
  - 使用动态输出路径和配置路径。
  - 同步帮助与 metadata。
- `README.md`
  - 增加参数说明和新的清理规则。
- `tests/test_full_shot_paths.py`（如当前项目引入测试目录）
  - 增加不依赖相机、镜头和 PLC 的路径单元测试。

`ImageNode.py` 预计不需要修改，因为现有 rotation step 保存接口已经支持外部传入图片根目录。

## 7. 验证计划

### 7.1 参数解析

- `python main.py --full_shot --help` 能看到 `--output_path` 和 `--config_path`。
- 两个参数都省略时，解析结果分别为项目根目录下的 `Output` 和 `configs/lens_range_map.json`。
- 相对路径基于项目根目录解析，不受启动命令当前目录影响。
- 包含空格、中文和 Windows 正反斜杠的路径可被正确解析。
- 未知参数仍由 `argparse` 拒绝。

### 7.2 输出目录准备

使用临时目录进行无硬件测试：

- 目标不存在时创建成功。
- 目标为空目录时不报错。
- 目标包含旧图片、嵌套子目录和非同名文件时，所有旧内容都被删除。
- 清理后输出根目录仍存在。
- 目标为普通文件时拒绝运行。
- 目标为盘符根目录、项目根目录或配置目录时拒绝运行。
- 删除权限不足时返回失败，拍摄流程不会启动。
- 符号链接或 junction 不会导致清理越过输出根目录。

### 7.3 配置读取

- 默认参数读取项目内 `configs/lens_range_map.json`。
- 自定义绝对路径读取指定 JSON，不再读取默认文件。
- 自定义相对路径读取项目根目录下对应文件。
- 自定义文件不存在时，告警包含实际路径，并按既有规则使用默认焦距范围。
- 自定义文件不可读或 JSON 非法时，错误包含实际路径和原因，并且不会启动拍摄。
- `full_shot` 不修改自定义配置文件。

### 7.4 拍摄集成

通过硬件联调或 mock 验证：

- `--output_path C://results` 且相机名为 `1`、`2` 时，会生成 `C://results/1/` 和 `C://results/2/`。
- 图片名称仍为 `Position{i}_Step{j}.bmp`。
- 运行前遗留文件已全部清除，不只覆盖同名图片。
- 输出目录在整轮任务中仅清理一次，多阵位和多 step 图片不会互相清除。
- 指定自定义输出目录时，默认 `Output` 不会被创建或清理。
- metadata 中的配置路径和图片路径模式与实际参数一致。
- `--full_shot --help` 不初始化硬件，不读取配置，也不清空输出目录。

## 8. 验收标准

- [ ] `full_shot` 支持 `--output_path`，默认行为仍输出到项目根目录 `Output`。
- [ ] `full_shot` 支持 `--config_path`，默认行为仍读取项目根目录 `configs/lens_range_map.json`。
- [ ] 自定义输出目录下按当前相机名建立 `1/`、`2/` 等子目录。
- [ ] 每次正式拍摄前只清理一次所选输出根目录的旧内容。
- [ ] 清理失败或目标路径危险时不会启动拍摄。
- [ ] 自定义配置路径不会静默回退到默认配置文件。
- [ ] 图片命名和 BMP 格式保持不变。
- [ ] step metadata 记录本次运行真实使用的配置路径和输出路径。
- [ ] `focus_calibration` 的现有配置读写行为不受影响。
- [ ] 帮助命令无文件系统和硬件副作用。

## 9. 实施顺序

1. 增加 `PROJECT_ROOT`、默认路径常量和路径解析函数。
2. 为 `build_full_shot_parser()` 增加两个参数并更新帮助文本。
3. 增加输出目录安全校验和一次性清理函数。
4. 在 `run_rotation_multi_shot()` 中接入 `args.config_path`。
5. 在正式旋转序列开始前接入 `args.output_path` 的一次性准备逻辑。
6. 将 metadata 中的硬编码路径改为本次运行的实际路径。
7. 增加路径与清理逻辑的无硬件测试。
8. 更新 README，并执行帮助、单元测试和硬件联调验收。

## 10. 镜头到位判定、坐标系重建与故障隔离计划

### 10.1 问题复盘与设计目标

在 `full_shot` 模式下，每个阵位拍摄完成后，所有镜头都会执行官方“点击回 0”指令。现有日志中，2 号相机对应的 COM9 在回零阶段出现：

```text
状态=255，最后内部角度=-2.6477°，软件角度=2110.02°
```

这里的 `255` 是十六进制 `0xFF`，并不是 `0xF5`（`0xF5` 的十进制值为 245）。当前 `_wait_until_stopped()` 把 `0xFF` 当作运行状态，所以即使镜头已经停在零点附近，也会一直等待到超时。回零失败后又保留了旧的 `current_angle=2110.02°`；下一阵位目标仍为约 `2110°` 时，`move_to_absolute_angle()` 因 `abs(delta_angle) < 0.1` 直接返回成功，没有重新下发移动指令，最终导致焦距错误。

结合后续普通绝对角度移动中出现的“物理位置看似正确，但停稳读数突然回到零点附近”的现象，本计划将问题模型扩展为：镜头内部坐标系可能发生瞬时失效、累计偏移或状态/角度反馈不一致。软件无法仅凭一次异常读数区分具体硬件原因，但可以通过厂商认可的“上电找 0 → 点击回 0”重新建立坐标基准，并重放原始任务来验证是否已经恢复。

本次重新设计目标如下：

1. `_wait_until_stopped()` 只负责一次指令的状态观测、角度校验和失败分类，不在函数内部下发恢复或重试指令。
2. 状态码仍作为首要证据，同时使用“有效目标角度 + 角度误差 + 连续角度稳定”辅助判断到位。
3. 正常停稳、碰壁、持续运动、未知状态和读取异常均形成结构化结果；不能仅输出日志后继续使用旧坐标。
4. `0x01`/`0xFF` 不能因为单次角度落入容差就直接视为成功；正常等待期结束后仍显示运动，才进入只检查角度的稳定窗口。
5. `full_shot` 中无论原始任务是回零还是普通绝对角度移动，只要一次任务最终失败且串口仍可下发指令，就进入统一坐标系重建流程：“上电找 0 → 点击回 0 → 校验零点 → 必要时重放原绝对角度任务”。
6. 坐标系重建按全局次数上限执行；每次重建中的回零或任务重放任一步失败，都算本次重建失败。达到上限仍失败才隔离相机。
7. 单个镜头最终失败时，保留目前的相机隔离策略，其他相机继续完成整段拍摄；程序不主动中断，用户可使用 `Ctrl+C` 停止整段任务。
8. 软件坐标必须带有效性和质量状态。初次异常后立即失效，只有坐标重建及任务重放确认成功后才重新设为有效。
9. 所有并发操作必须收集并向上传播每台镜头的结构化结果，同时输出可定位初始失败、每次恢复和最终隔离原因的详细日志。
10. `focus_calibration` 当前运行正常，必须保持其现有调用链、等待逻辑、返回结构和边界判定不变；所有增强只由 `wait_policy="full_shot"` 接入。

#### 10.1.1 `focus_calibration` 现有共享调用链

`focus_calibration` 确实会共享当前 `_wait_until_stopped()`，调用链如下：

```text
calibrate_single_lens()
├─ initialize_lens()
│  └─ _wait_until_stopped()              # 初始化回零检查
├─ return_to_home()                       # 交互命令 home
│  └─ _wait_until_stopped()
└─ move_relative_for_calibration()        # 标定相对移动
   └─ _wait_until_stopped()
```

因此不能改变 `_wait_until_stopped()` 的默认语义或三元组返回结构；否则即使 `full_shot` 修复正确，也可能造成焦距标定流程回归。

本次继续共享 `_wait_until_stopped()`、`return_to_home()` 和 `move_to_absolute_angle()`，不复制一套 `*_for_full_shot` 方法。共享函数通过可选策略参数区分行为，默认值始终是原有策略：

```python
def _wait_until_stopped(
    self,
    timeout_s=10.0,
    interval_s=0.5,
    expected_angle=None,
    angle_tolerance=None,
    wait_policy="legacy",
    normal_timeout_s=None,
    hard_timeout_s=None,
    operation=None,
):
```

- `wait_policy="legacy"`：执行当前判断顺序、日志和等待行为，继续返回 `(stopped, status, real_angle)`；
- `wait_policy="full_shot"`：在同一函数中启用目标误差、连续稳定、正常超时和硬超时，返回结构仍是同一个三元组；
- full-shot 分支的详细诊断结果写入 `full_shot_last_wait_result`，供上一层读取；
- `focus_calibration` 的现有调用代码不传新参数，因此自动进入 `legacy` 分支。

以下 focus_calibration 代码段保持原样，不增加策略参数，也不修改返回值处理：

```python
# initialize_lens()
stopped, status, real_angle = self._wait_until_stopped(
    timeout_s=2.0, interval_s=0.5,
)

# return_to_home() 在默认 legacy 策略下的现有调用
stopped, status, real_angle = self._wait_until_stopped(
    timeout_s=10.0,
    interval_s=0.5,
    expected_angle=0.0,
    angle_tolerance=BOUNDARY_ANGLE_TOLERANCE,
)

# move_relative_for_calibration()
stopped, status, real_angle = self._wait_until_stopped(
    timeout_s=4.0, interval_s=0.2,
)
```

`return_to_home()` 和 `move_to_absolute_angle()` 只增加默认值为 `legacy` 的可选 `wait_policy` 参数，并继续返回现有布尔值。focus_calibration 仍按原代码调用 `return_to_home()`；ImageNode 的 full-shot 并发路径显式调用 `move_to_absolute_angle(..., wait_policy="full_shot")`。目标角度为 0 时，`move_to_absolute_angle()` 必须把策略继续传给 `return_to_home(wait_policy=wait_policy)`，避免意外回落到 legacy 分支。

full-shot 启动时仍复用原 `initialize_lens()` 完成硬件初始化。初始化成功后，由 `ImageNode.parallel_global_homing()` 上层根据已经校准的 `current_angle` 建立 `full_shot_position_valid=True` 和 `full_shot_position_quality="confirmed"`；初始化失败则隔离对应相机。`initialize_lens()` 本身及其 focus_calibration 行为保持不变。

### 10.2 状态码与成功条件

当前已知状态按以下方式处理：

| 状态 | 当前含义 | 处理原则 |
|---|---|---|
| `0x00` | 正常停稳 | 只有实测角度也在有效目标容差内才确认成功；角度不符时先做有限次串行复核，持续不符才返回 `stopped_position_mismatch` 并由上层触发坐标重建 |
| `0x01` | 运行中 | 正常等待期内继续轮询；正常等待期结束时仍为运行状态，且角度位于目标容差内，才进入稳定窗口 |
| `0xFF` | 当前代码视为运行中；现场可能出现状态滞留 | 与 `0x01` 相同；不能在正常等待期内因为短暂稳定就提前推断成功 |
| `0xF5` | 当前映射为零点边界 | 结合运动方向、有效目标和实测角度判断；任何一项不匹配均返回边界错误并触发坐标重建 |
| `0x0B` | 当前映射为远端边界 | 结合运动方向、有效目标和实测角度判断；任何一项不匹配均返回边界错误并触发坐标重建 |
| `-1` | 未读取到有效帧 | 记录串口读取失败次数；达到单次等待截止时间后返回读取故障。若串口仍可写，可尝试坐标重建；否则直接进入最终失败处理 |
| 其他状态 | 未知 | 详细记录原始状态；达到策略规定条件后返回未知状态故障，由上层决定能否恢复 |

所有状态日志统一同时输出十六进制和十进制，例如：

```text
status=0xFF (255)
status=0xF5 (245)
```

负角度本身不能直接等价为回零成功。它只能说明编码器实测位置已经接近或越过内部零点。回零成功仍需满足以下任一条件：

- `0x00` 且实测角度在回零容差内；
- `0xF5` 且实测角度在回零容差内；
- `0x01`/`0xFF` 且实测角度在回零容差内，并在稳定窗口内连续稳定。

#### 10.2.1 有效目标角度与边界钳位

`full_shot` 的每台镜头必须有明确的**电机物理角度边界**。当前默认值可沿用 `ANGLE_START=0.0`、`ANGLE_END=2900.0` 的数值，但实现时应在 `LensController.py` 定义语义独立的 `MOTOR_ANGLE_MIN`、`MOTOR_ANGLE_MAX`，避免与 `lens_range_map` 中用于生成拍摄焦距步进的 `angle_start`、`angle_end` 混淆。

镜头标定拍摄范围可以小于电机物理范围，但它不是 `0xF5`/`0x0B` 的机械碰壁位置，不能用来验证边界状态。除非未来厂商明确给出某台电机不同的物理限位，否则所有镜头使用同一组 `MOTOR_ANGLE_MIN=0.0`、`MOTOR_ANGLE_MAX=2900.0`。计算规则统一为：

```python
requested_angle = float(target_angle)
effective_target = min(max(requested_angle, MOTOR_ANGLE_MIN), MOTOR_ANGLE_MAX)
target_was_clamped = effective_target != requested_angle
```

虽然需求提出可在实际碰壁后再检查越界，以避免每次移动前增加耗时，但一次数值比较不会产生可观测的硬件等待成本。为了避免主动发送越界运动、减少撞击限位和无意义的恢复，计划采用双层保护：

1. **下发前钳位**：普通绝对角度移动在发报文前将越界请求钳位到最近边界，并输出醒目警告，包含请求值、有效值和边界来源。
2. **碰壁后复核**：即使目标未被钳位，读取到 `0xF5`/`0x0B` 时仍再次检查方向、有效目标、实测角度和边界类型，防止内部坐标错误或状态码异常。
3. 等待函数始终使用 `effective_target` 计算偏差，不能拿已越界的 `requested_angle` 与边界实测值比较。
4. 钳位后的边界到位属于“按安全边界完成”，结果标记 `target_clamped=True` 并允许继续拍摄；但必须保留警告和 metadata，提醒用户修正配置。
5. 若电机物理边界配置本身无效，例如最小值大于最大值或不是有限数值，则不下发运动指令，直接返回配置错误；这类错误不通过重建坐标系解决。

碰壁成功条件为：边界类型与运动方向一致，`effective_target` 位于对应边界容差内，实测角度也位于该边界容差内。否则返回 `boundary_direction_mismatch` 或 `boundary_position_mismatch`，进入统一坐标重建流程。

#### 10.2.2 终态异常的有限确认

现场已经出现过“物理镜头停在目标附近，但某一次 `0x00` 帧中的角度突然变成零点附近”的情况。若仅凭这一帧立即执行上电找 0，会把瞬时通信/解析异常放大为一次耗时且有机械动作的恢复。因此 full-shot 对**终态正确但角度/方向不符**增加有限确认：

1. 第一次读到 `0x00` 或边界终态且校验失败时，不立即判定最终失败，记录为 `terminal_mismatch_candidate`。
2. 继续使用同一串口、同一轮询线程，最多读取 `TERMINAL_MISMATCH_CONFIRM_SAMPLES` 个终态确认样本；不创建额外读取线程，也不与其他操作并发访问同一镜头。
3. 后续任一有效样本显示状态和角度已符合目标，则按最新证据成功，并记录此前出现过瞬时不一致警告。
4. 达到确认样本数后仍连续不符，才返回 `stopped_position_mismatch`、`boundary_position_mismatch` 或 `boundary_direction_mismatch`。
5. 确认期间状态重新变为 `0x01`/`0xFF`，则回到当前等待阶段继续处理，但总时间仍受原 hard timeout 限制。
6. 多个样本互相冲突且到达 hard timeout，返回 `inconsistent_terminal_feedback`，由上层执行坐标重建。

这会增加极少量、仅在异常终态出现时发生的读取，目的是防止单帧误报触发重型恢复；正常任务不会付出额外等待成本。

### 10.3 全局可调参数

所有到位判定和恢复参数统一放在 `LensController.py` 顶部、现有 `BOUNDARY_ANGLE_TOLERANCE` 附近，并逐项写明单位、用途和现场调参影响。建议初始值如下，最终数值以硬件联调为准：

```python
# 物理边界角度容差；用于判断边界状态对应的位置是否合理。
BOUNDARY_ANGLE_TOLERANCE = 3.0  # 度

# 回零目标角度容差；允许编码器在机械零点附近存在负值、回差或轻微偏移。
HOME_ANGLE_TOLERANCE = 3.0  # 度

# 普通绝对角度移动的到位容差；应比回零容差更严格，避免影响焦距精度。
MOVE_ANGLE_TOLERANCE = 2.0  # 度

# 电机内部坐标允许的物理范围；用于目标钳位和 0xF5/0x0B 碰壁校验。
# 不等同于 lens_range_map 中生成拍摄步进的每相机焦距范围。
MOTOR_ANGLE_MIN = 0.0  # 度
MOTOR_ANGLE_MAX = 2900.0  # 度

# 连续角度稳定所需时间；候选样本必须覆盖至少该时长才能推断到位。
POSITION_STABLE_DURATION = 1.0  # 秒

# 稳定窗口内允许的最大角度极差：max(samples) - min(samples)。
POSITION_STABLE_ANGLE_SPAN = 0.5  # 度

# 状态轮询间隔；回零和普通移动默认共用，调用方仍可按需覆盖。
POSITION_POLL_INTERVAL = 0.5  # 秒

# 终态与目标不符时，需要连续得到的异常确认样本数；用于排除单帧读数跳变。
TERMINAL_MISMATCH_CONFIRM_SAMPLES = 2

# 单次等待内允许的连续无效串口读取次数；超过后返回 serial_read_failed。
SERIAL_READ_FAILURE_LIMIT = 3

# 回零正常状态等待时间；到期仍显示运动时才进入稳定窗口。
HOME_NORMAL_TIMEOUT = 10.0  # 秒

# 回零单次尝试的绝对截止时间；包括跨过正常超时后的稳定观察时间。
HOME_HARD_TIMEOUT = 15.0  # 秒

# 普通移动正常等待时间和绝对截止时间。
MOVE_NORMAL_TIMEOUT = 5.0  # 秒
MOVE_HARD_TIMEOUT = 10.0  # 秒

# 单台镜头、单次原始运动失败后允许执行的完整坐标系重建次数。
# full-shot 启动时的初始上电找0/回零、原始运动本身均不计入该值。
# 当前失败恢复成功后计数立即作废；后续阵位再次失败时重新从 0 计数。
HOME_RECOVERY_MAX_ATTEMPTS = 2

# 厂商“上电找 0”指令后的静默等待时间，再执行“点击回 0”。
POWER_ON_HOMING_WAIT = 10.0  # 秒
```

为兼容当前命名，计划暂时保留 `HOME_RECOVERY_MAX_ATTEMPTS`，但其语义扩展为 full-shot 的“单次失败事件允许执行的坐标系重建次数上限”，不再只代表回零函数的恢复次数，也绝不是整段拍摄期间的全局累计次数。后续若允许破坏性重命名，可改为更准确的 `COORDINATE_RECOVERY_MAX_ATTEMPTS`。无论采用哪个名称，代码和日志必须使用同一计数语义：

- full-shot 启动初始化阶段原本就会执行的上电找 0 和回零不计入该值；
- 每个阵位中原始的绝对移动或拍摄结束回零也不计入恢复次数；只有它们失败后额外启动的坐标系重建才计数；
- `HOME_RECOVERY_MAX_ATTEMPTS=2` 表示某台镜头的某一次原始运动失败后，最多连续执行两轮完整的坐标重建事务；
- 每轮事务从“上电找 0”开始，以“原始任务校验成功”结束；中间任一步失败即消耗这一轮；
- 不允许回零函数和普通移动函数各自再套一层相同次数，避免出现平方级重试。
- 当前失败事件恢复成功后，该局部计数器及恢复历史随本次 motion result 封存，不保留为下一阵位的可用次数扣减；
- 同一相机到后续任意阵位再次发生新的移动或回零失败时，建立新的失败事件，并重新获得完整的 `HOME_RECOVERY_MAX_ATTEMPTS` 次恢复预算；
- 各相机独立计数，相机 2 的恢复尝试不消耗相机 1、3、4 的恢复次数。

实现上应使用运动协调器调用栈内的局部变量，例如 `recovery_attempts_used = 0`，而不是在 `LensController` 实例中维护从任务开始持续累加的 `total_recovery_attempts`。实例状态可以保存“最近一次运动使用了几次恢复”用于日志，但下一次新的原始运动必须从 0 开始。

不增加速度阈值或速度计算。稳定性只由以下两个条件确定：

```python
sample_duration >= POSITION_STABLE_DURATION
max(sample_angles) - min(sample_angles) <= POSITION_STABLE_ANGLE_SPAN
```

### 10.4 `_wait_until_stopped()` 双策略到位判定

`_wait_until_stopped()` 继续作为唯一状态等待入口。内部按 `wait_policy` 分为两个明确分支：

```python
if wait_policy == "legacy":
    # 保留当前实现的判断顺序、日志、等待和三元组返回值。
    ...

# wait_policy == "full_shot"
# 执行下述增强到位判定，仍返回相同三元组。
```

实施时可以将原代码提取到私有辅助函数以减少嵌套，但这只是机械重排，legacy 分支的可观察行为必须保持一致。不得让 full-shot 的稳定推断或硬超时规则泄漏到默认分支。

#### 10.4.1 输入参数

full-shot 调用共享方法时显式传入以下参数：

```python
stopped, status, real_angle = self._wait_until_stopped(
    expected_angle=effective_target,
    angle_tolerance=target_tolerance,
    operation="home",  # 或 "absolute_move"
    wait_policy="full_shot",
    normal_timeout_s=normal_timeout,
    hard_timeout_s=hard_timeout,
    interval_s=POSITION_POLL_INTERVAL,
)
```

其中：

- `operation` 只需区分 `home` 和 `absolute_move`，不接收 `calibration_move`；
- `expected_angle` 和 `angle_tolerance` 用于统一验证实测位置；
- `normal_timeout_s` 表示普通状态等待期限；
- `hard_timeout_s` 是本次调用不可突破的绝对截止时间。

#### 10.4.2 正常等待与稳定窗口

每次下发指令后分为两个连续但职责不同的阶段。两阶段都由同一个 `_wait_until_stopped()` 调用完成，不创建额外线程，也不增加并发串口读取者。

**阶段 A：正常状态等待**

1. 从指令下发后开始，按 `POSITION_POLL_INTERVAL` 持续读取状态和角度，直到 `normal_timeout_s`。这仍是轮询等待，不是只在超时瞬间读取一次。
2. 任意一次读到 `0x00` 或边界终态，立即按有效目标、角度容差、运动方向执行终态校验；成功则立即返回。首次校验失败时按 10.2.2 做有限确认，连续不符才返回明确原因并交给上层恢复编排。
3. `0x01`/`0xFF` 在阶段 A 内只表示继续运动。即使某几次角度已接近目标，也不在正常等待期内启动或完成稳定推断，避免短暂读数平台被过早当成停稳。
4. 串口帧无效和未知状态记录计数及最后样本；达到可配置阈值或阶段截止时返回对应故障。

**阶段 B：持续运动稳定窗口**

仅当阶段 A 到期时，最后有效状态仍是 `0x01`/`0xFF`，才进入稳定窗口：

1. 继续按相同轮询间隔读取状态和角度，直到 `hard_timeout_s`；`hard_timeout_s` 是包含阶段 A 的单次等待总截止时间。
2. 若状态转为 `0x00` 或边界终态，立即改用对应终态规则验证，不再要求填满稳定窗口。
3. 状态仍为 `0x01`/`0xFF` 时，只有角度处于有效目标容差内的样本才进入候选集合。
4. 任一样本离开目标容差，或窗口角度极差超过 `POSITION_STABLE_ANGLE_SPAN`，清空候选并重新计时。
5. 候选覆盖 `POSITION_STABLE_DURATION` 且角度极差不超限，返回 `inferred_stable_at_target`。
6. 到达硬截止仍不能确认，按最终样本区分 `timeout_still_moving`、`timeout_unstable_at_target` 或读取故障，并交给上层坐标系重建。

此设计的目的不是减少状态读取次数，而是将“协议报告的明确停稳”与“协议持续报告运动但位置看似稳定”的证据分层。轮询频率仍由全局参数控制；同一串口在任一时刻只能由该镜头的当前操作读取，禁止稳定窗口另起读取线程。

#### 10.4.3 结构化返回值

为兼容所有既有调用，函数仍返回 `(stopped, status, real_angle)`。仅在 `wait_policy="full_shot"` 时，额外把统一诊断字典写入 `self.full_shot_last_wait_result`：

```python
{
    "ok": True,
    "reason": "inferred_stable_at_target",
    "operation": "absolute_move",
    "status": 0xFF,
    "real_angle": 1699.82,
    "requested_angle": 1699.97,
    "effective_target": 1699.97,
    "target_clamped": False,
    "deviation": -0.15,
    "position_quality": "inferred",
    "stable_duration": 1.6,
    "stable_span": 0.08,
    "elapsed": 8.2,
    "boundary": None,
    "motion_direction": "positive",
}
```

`reason` 至少覆盖：

- `confirmed_stopped_at_target`
- `confirmed_zero_boundary`
- `confirmed_far_boundary`
- `inferred_stable_at_target`
- `stopped_position_mismatch`
- `boundary_position_mismatch`
- `boundary_direction_mismatch`
- `inconsistent_terminal_feedback`
- `timeout_still_moving`
- `timeout_unstable_at_target`
- `serial_read_failed`
- `unexpected_status`

这些 `reason` 只描述**本次等待**，不使用 `home_recovery_failed` 等跨多条指令的原因。坐标重建次数、重放结果和最终隔离原因由上层运动事务写入 `full_shot_last_motion_result`。

### 10.5 软件坐标状态模型

在 `LensController` 中增加以下状态。这些字段只在 `wait_policy="full_shot"` 时读取和更新，不改变 `focus_calibration` 当前依赖的 `current_angle` 更新规则：

```python
self.current_angle = 0.0            # 兼容现有逻辑，保留
self.last_measured_angle = None
self.full_shot_position_valid = False
self.full_shot_position_quality = "unknown"
self.full_shot_last_wait_result = None
self.full_shot_last_motion_result = None
```

字段含义：

- `current_angle`：只有在位置可信时才可用于下一次相对位移计算；
- `last_measured_angle`：最后一次读取到的真实角度，即使失败也保留用于日志和诊断；
- `full_shot_position_valid`：当前软件坐标是否允许被 full-shot 作为运动基准；
- `full_shot_position_quality`：`confirmed`、`inferred` 或 `unknown`；
- `full_shot_last_wait_result`：共享等待函数最近一次 full-shot 分支的详细诊断结果；
- `full_shot_last_motion_result`：full-shot 最近一次回零或移动的完整结构化结果，供上层决策和日志输出。

采用 `full_shot_` 前缀是为了避免标定模式无意依赖新增状态。`focus_calibration` 仍按当前逻辑读取和更新 `current_angle`，不检查这些新字段。

更新规则：

1. `0x00`/合理边界状态确认到位：使用实测角度更新 `current_angle`，设置 `full_shot_position_valid=True`、`full_shot_position_quality="confirmed"`。
2. `0x01`/`0xFF` 在目标附近连续稳定：使用稳定窗口最后值或平均值更新 `current_angle`，设置 `full_shot_position_valid=True`、`full_shot_position_quality="inferred"`。
3. 超时但存在真实角度：只更新 `last_measured_angle`。如果最后一段角度已经满足完整稳定条件，应归入第 2 类；否则不能把不断变化的角度作为可信基准，设置 `full_shot_position_valid=False`、`full_shot_position_quality="unknown"`。
4. 串口读取失败或没有真实角度：保持最后测量记录，设置 `full_shot_position_valid=False`。
5. 禁止在失败后继续保留旧的有效坐标状态。

`move_to_absolute_angle(..., wait_policy="full_shot")` 的小位移快捷返回必须增加有效性约束；legacy 分支继续保留原行为：

```python
if self.full_shot_position_valid and abs(delta_angle) < 0.1:
    # 跳过前再读取并核对一次真实角度；只有仍在 MOVE_ANGLE_TOLERANCE 内才成功。
```

如果 `full_shot_position_valid=False`，普通绝对移动不能依赖旧 `current_angle`，也不能因一次新的角度读取看似合理就恢复坐标有效性；应直接进入 10.6 的坐标系重建，再从已确认的零点重放目标。该约束不加入原 `move_relative_for_calibration()`。

### 10.6 统一坐标系重建与原任务重放

厂商建议的“上电找 0 → 点击回 0”在 `full_shot` 中提升为统一坐标系重建模块。它既服务于回零失败，也服务于普通绝对角度移动的停稳读数不符、碰壁方向/位置不符、持续运动稳定失败等可恢复异常。

关键职责边界如下：

- `_wait_until_stopped()`：只观测一条已下发指令并返回分类结果，不发送上电找 0、回 0 或目标重试指令。
- 单次执行函数：负责下发一次 home 或 absolute move，再调用一次等待函数；本身不递归恢复。
- full-shot 运动协调器：负责初始任务、坐标系重建计数、原任务重放和最终失败汇总。
- `ImageNode`：只根据最终结构化结果决定成功、拍照或隔离，不参与电机内部恢复步骤。

建议以私有辅助函数分层，名称可按现有代码风格调整：

```python
_execute_home_once(...)                 # 点击回0 + wait，不自动恢复
_execute_absolute_move_once(...)        # 单次目标移动 + wait，不自动恢复
_rebuild_coordinate_system_once(...)    # 上电找0 + 点击回0 + 零点校验
_execute_full_shot_motion_with_recovery(operation, target, ...)
```

必须使用非递归编排，禁止 `move_to_absolute_angle()` 的恢复分支调用一个会再次自动恢复的 `return_to_home()`，也禁止重放目标时再次从零开始创建完整恢复循环。

#### 10.6.1 初始任务

1. `operation="home"`：下发一次点击回 0，并使用 home 超时和容差等待。
2. `operation="absolute_move"`：先计算 `effective_target`，再依据当前可信软件坐标计算相对角度、下发一次移动，并使用 move 超时和容差等待。
3. 初始任务成功：同步实测坐标，返回成功，`recovery_attempts_used=0`。
4. 初始任务失败：保存完整等待结果，立即设置 `full_shot_position_valid=False`，然后判断该原因是否允许软件恢复。

以下情况默认进入坐标重建：

- `stopped_position_mismatch`；
- `boundary_position_mismatch`；
- `boundary_direction_mismatch`；
- `inconsistent_terminal_feedback`；
- `timeout_still_moving`；
- `timeout_unstable_at_target`；
- 仍具备串口写能力时的 `unexpected_status` 或有限次读取异常。

以下情况不应盲目恢复：无串口连接、写指令失败、镜头边界配置非法、用户 `KeyboardInterrupt`、已知不可恢复的程序参数错误。它们直接进入最终失败处理，其中 `KeyboardInterrupt` 必须继续向上传播而不是隔离后吞掉。

#### 10.6.2 单轮坐标系重建

对第 `n` 轮恢复执行以下原子流程：

1. 输出恢复开始日志，包含原始操作、原始目标、首次失败原因和 `n/HOME_RECOVERY_MAX_ATTEMPTS`。
2. 下发 `LENS_POWER_ON_HOMING_COMMAND`，等待 `POWER_ON_HOMING_WAIT`。
3. 下发一次 `LENS_RETURN_HOME_COMMAND`。
4. 调用 `_wait_until_stopped(... operation="home", wait_policy="full_shot")` 完整校验零点。
5. 回零失败：记录本轮 `recovery_home_failed`，保持软件坐标无效，本轮结束；若尚有次数则重新从步骤 1 开始下一轮。
6. 回零成功：用实测角度校准 `current_angle`，暂时标记坐标有效，质量为本次回零的 `confirmed` 或 `inferred`。
7. 若原任务就是 `home`，本轮在步骤 6 即完成，返回 `recovered=True`。
8. 若原任务是普通绝对角度移动，则重新使用回零后的可信实测坐标计算到同一 `effective_target` 的相对角度，下发一次目标移动，并再次调用完整等待判定。
9. 目标重放成功：同步最终实测坐标，返回 `recovered=True`，并记录本轮的 home 与 replay 两段结果。
10. 目标重放失败：记录 `recovery_replay_failed`，重新将软件坐标设为无效，本轮结束；尚有次数时再次从上电找 0 开始，不能只重复目标移动。

`HOME_RECOVERY_MAX_ATTEMPTS=2` 的准确执行序列为：

```text
初始任务
  └─ 失败
      ├─ 坐标重建 1/2：上电找0 → 回0校验 → [普通移动时]重放目标
      │   └─ 任一步失败
      └─ 坐标重建 2/2：上电找0 → 回0校验 → [普通移动时]重放目标
          └─ 任一步失败 → 最终失败并隔离
```

这意味着普通绝对移动最多下发 1 次初始移动和 2 次恢复后的目标重放；home 最多下发 1 次初始回零和 2 次恢复回零。次数有限，避免在错误坐标系下无休止旋转或反复撞击边界。

上述次数只约束图中这一棵“初始任务失败 → 连续恢复”的调用链。例如相机 4 在阵位 3 使用一次恢复后成功，则阵位 3 的计数结果记录为 `recovery_attempts_used=1` 并结束；相机 4 到阵位 20 再次出现一个新的调焦失败时，仍可重新执行最多 `2` 轮坐标重建，而不是只剩 `1` 轮。只有同一失败事件连续两轮均未恢复时才隔离相机，因此被隔离后自然不会进入后续阵位并再次创建恢复预算。

#### 10.6.3 恢复成功与最终失败

恢复成功时，最终运动结果至少包含：

```python
{
    "ok": True,
    "operation": "absolute_move",
    "requested_angle": 1699.97,
    "effective_target": 1699.97,
    "recovered": True,
    "recovery_attempts_used": 1,
    "initial_failure": {...},
    "recovery_history": [
        {"attempt": 1, "home": {...}, "replay": {...}, "ok": True}
    ],
    "final_wait": {...},
}
```

达到上限仍失败时：

1. `full_shot_position_valid=False`、`full_shot_position_quality="unknown"`；
2. 返回 `coordinate_recovery_exhausted`，并保留初始失败及每轮 home/replay 结果；
3. 上层隔离该相机，本阵位不再拍摄该相机，后续阵位跳过；
4. 其他相机、转台和上传流程继续；
5. 输出醒目日志，说明软件恢复已用尽，建议用户检查硬件厂商问题，并提示可用 `Ctrl+C` 中断整段任务。

恢复流程中的每一次 wait 都受对应硬超时约束，整个事务还应能计算总耗时用于日志。`Ctrl+C` 必须正常传播，不能被宽泛的 `except Exception` 吞掉。

### 10.7 单相机隔离与持续运行策略

本项目默认采用“持续运行优先”策略。单个镜头最终失败不主动中断整段 `full_shot`，而是隔离对应相机；其他相机继续移动和拍摄。用户如需停止整段任务，可使用 `Ctrl+C`。

在 `ImagingNode` 中增加运行状态，例如：

```python
self.enabled = True
self.failure_reason = None
self.failed_at_position = None
self.failed_at_step = None
```

隔离规则：

1. 普通角度移动初次判定失败后先执行统一坐标系重建；仅在恢复次数用尽仍失败时隔离该相机，本阵位不触发该相机拍照，后续阵位也跳过其镜头移动和拍照。
2. 阵位拍摄结束后的回零初次失败后执行同一坐标系重建；恢复次数用尽仍失败时隔离该相机，从下一阵位开始跳过；已经成功保存的历史图片保留。
3. 相机拍照本身失败或抛出异常：记录并隔离该相机，其他相机继续。
4. 隔离不关闭整个系统、不终止转台循环，也不自动重试无上限次数。
5. 任务结束时输出隔离相机汇总，明确哪些阵位缺图或可能存在焦距风险。

需要特别区分回零结果：

- `0xFF + 零点附近连续稳定` 属于可继续运行的“推断成功”，不隔离相机，但输出警告级日志；
- 初始 home/absolute move 失败、坐标重建及原任务重放成功属于“恢复成功”，不隔离相机；
- 所有允许的软件恢复次数用尽后仍无法确认原始任务到位，才执行隔离。

### 10.8 并发结果传播与拍照过滤

`parallel_move_lenses()`、`parallel_move_lenses_by_camera()`、`parallel_snap_rotation_step()` 不能只调用 `concurrent.futures.wait()` 后无条件输出成功。必须逐个调用 `future.result()`，收集相机名、串口和结构化结果，并返回汇总：

```python
{
    "ok": False,
    "successful": ["1", "3", "4"],
    "failed": {
        "2": {
            "port": "COM9",
            "reason": "coordinate_recovery_exhausted",
            "status": 0xFF,
            "real_angle": -2.65,
            "recovery_attempts_used": 2,
        }
    },
}
```

上层行为：

1. 移动成功的相机进入本阵位拍照集合。
2. 移动失败或已隔离的相机不拍照，避免生成焦距错误但文件名正常的误导性图片。
3. 其余相机正常拍照，转台流程继续。
4. “所有镜头均已同步到位”只能在全部活动镜头成功时输出。
5. 存在失败时输出“部分镜头到位，已隔离 N 台，其余继续”，不能再输出全成功文案。
6. 每个 step 的 metadata 增加活动相机、跳过相机、各镜头移动结果、位置质量和隔离原因，便于后续识别缺图及异常图。

### 10.9 日志规范

每次移动或回零至少记录：

- 相机名和串口；
- 操作类型、尝试序号、是否属于坐标系重建；
- 请求目标、有效目标、是否发生边界钳位、边界来源、软件起始角度、软件坐标是否有效、坐标质量；
- 原始状态码的十六进制与十进制形式；
- 实测角度、目标偏差；
- 稳定候选开始/取消原因、样本数、覆盖时间、角度极差；
- 正常超时和硬超时各自是否到达；
- 初始失败原因、每轮坐标重建中的回零结果和目标重放结果；
- 最终 `reason`、是否推断成功、是否恢复成功、恢复次数、是否隔离。

建议关键日志示例：

```text
⚠️ [相机2/COM9] 回零正常等待已到期，状态仍为 0xFF (255)，实测=-2.65°，偏差=2.65°；进入稳定窗口。
✅ [相机2/COM9] 连续稳定 1.60s，角度极差=0.08°；推断已回到零点，position_quality=inferred。
⚠️ [相机4/COM10] 初始绝对移动失败 reason=stopped_position_mismatch，目标=1699.97°，实测=-0.03°；执行坐标重建 1/2。
✅ [相机4/COM10] 坐标重建 1/2 回零成功，实测=0.02°；正在重新计算相对角度并重放目标 1699.97°。
✅ [相机4/COM10] 目标重放成功，实测=1700.11°，本次移动已恢复。
❌ [相机2/COM9] 坐标重建 2/2 后原任务仍失败，reason=coordinate_recovery_exhausted；已隔离该相机，其余相机继续。请检查镜头硬件，或按 Ctrl+C 中断整段任务。
```

轮询日志需要限频或仅在状态变化、候选状态变化、固定时间间隔时输出，避免每 0.2 秒刷屏，但内部结果应保留最后状态和关键统计值。

### 10.10 预计影响文件

- `LensController.py`
  - 在顶部集中增加全部可调阈值及注释。
  - `_wait_until_stopped()` 保留默认值为 `legacy` 的策略参数；legacy 分支保持原逻辑和三元组返回值，full-shot 分支按“正常状态等待 → 必要时稳定窗口”执行并返回详细诊断。
  - `initialize_lens()` 和 `move_relative_for_calibration()` 的代码及调用方式保持不变。
  - `return_to_home()`、`move_to_absolute_angle()` 增加默认值为 `legacy` 的可选策略参数，默认调用行为不变。
  - 增加带 `full_shot_` 前缀的软件坐标有效性、质量、等待结果和操作结果字段。
  - 将单次 home、单次 absolute move、单轮坐标重建和带次数上限的运动协调拆分为非递归辅助函数。
  - `return_to_home()` 和 `move_to_absolute_angle()` 的 full-shot 分支统一接入坐标系重建；普通移动恢复成功后必须重放原有效目标。
  - `move_to_absolute_angle()` 的 full-shot 分支增加下发前边界钳位、碰壁后方向/位置复核，并修复无效坐标下的小位移跳过。

- `ImageNode.py`
  - 为 `ImagingNode` 增加启用/隔离状态和失败上下文。
  - `parallel_global_homing()` 收集每台镜头的初始化结果；成功时只在上层初始化 full-shot 坐标质量状态，失败时隔离对应相机，不修改 `initialize_lens()`。
  - full-shot 并发移动显式调用 `move_to_absolute_angle(..., wait_policy="full_shot")`；目标为 0 时该方法继续把策略传给共享 `return_to_home()`。
  - 所有并发移动与拍摄函数读取 `future.result()`、返回汇总并过滤隔离相机。
  - 日志中同时带相机名和串口。

- `main.py`
  - `full_shot` 根据移动汇总只拍摄成功相机，不因单相机失败终止整个循环。
  - 任一 full-shot 镜头运动在坐标重建次数用尽后隔离对应相机，其余相机和转台继续。
  - step metadata 记录镜头状态、跳过原因和位置质量。
  - 保持 `KeyboardInterrupt` 可由用户主动中止整段任务。

- 测试文件
  - 增加无硬件的状态序列测试、稳定窗口测试、边界钳位测试、普通移动/回零统一恢复测试、隔离与并发汇总测试。

### 10.11 验证计划

1. **正常移动**：`0x01 → 0x00`，最终角度在目标容差内，返回 `confirmed`。
2. **正常回零**：`0xFF → 0x00`，零点角度符合容差，不触发坐标重建。
3. **明确零点边界**：`0xF5/-2.5°`，回零确认成功并用实测角度校准软件坐标。
4. **0xFF 稳定推断**：正常等待期内持续 `0xFF`；正常等待到期后才建立稳定窗口，角度在目标附近且覆盖稳定时间、极差不超限，返回 `inferred`，不触发恢复、不隔离。
5. **0xFF 仍在变化**：角度虽在目标容差内但稳定窗口极差持续超限，不能推断成功；达到硬超时后进入坐标重建。
6. **正常等待期不提前推断**：在正常等待期内 `0xFF` 角度短暂稳定，仍继续等待明确终态；只有正常等待到期仍显示运动才允许启动稳定窗口。
7. **停稳但位置错误**：`0x00` 但角度超出目标容差，返回 `stopped_position_mismatch`，不能误判成功。
8. **单帧终态读数跳变**：第一个 `0x00` 角度明显错误，后续确认帧恢复到目标范围；记录警告但不触发坐标重建。
9. **连续终态位置错误**：连续达到 `TERMINAL_MISMATCH_CONFIRM_SAMPLES` 个 `0x00` 异常角度，才返回 `stopped_position_mismatch` 并进入坐标重建。
10. **边界方向错误**：回零时读到 `0x0B`，不得按零点成功处理；确认方向错误后触发坐标重建。
11. **普通移动稳定推断**：正常等待到期后，`0xFF` 在普通目标附近连续稳定，按 `MOVE_ANGLE_TOLERANCE` 返回 `inferred`。
12. **越界目标钳位到零点**：请求角度低于 `MOTOR_ANGLE_MIN`，下发前改为物理起点，日志及结果保留请求值与钳位值；边界状态按物理起点判断。
13. **越界目标钳位到远端**：请求角度高于 `MOTOR_ANGLE_MAX`，下发前改为物理终点；`0x0B`、方向和实测位置均合理时成功。
14. **拍摄标定范围不充当物理边界**：镜头 `lens_range_map` 的范围小于 0–2900° 时，范围内外的普通目标不会被错误钳位到拍摄范围；只有物理边界参与碰壁校验。
15. **普通移动恢复成功**：初始 `0x00` 且连续确认读数与目标明显不符，执行“上电找 0 → 回 0 校验 → 重放原有效目标”后成功，不隔离相机。
16. **普通移动首次恢复失败、第二次成功**：第一轮可在回零或目标重放阶段失败，第二轮必须重新从上电找 0 开始；最终成功且 `recovery_attempts_used=2`。
17. **回零恢复成功**：初始回零失败，执行坐标重建后零点校验成功；不额外重放目标，不隔离相机。
18. **恢复次数用尽**：两轮坐标重建在回零或重放阶段仍失败，返回 `coordinate_recovery_exhausted`，只隔离故障相机，其余相机继续后续阵位。
19. **计数不嵌套**：验证 `HOME_RECOVERY_MAX_ATTEMPTS=2` 时最多只有两轮“上电找 0”，不会因为 home 与 move 各自重试而放大次数。
20. **跨阵位恢复预算重置**：某相机在阵位 3 第一次恢复成功，到阵位 20 发生新的失败时，`recovery_attempts_used` 必须重新从 0 开始，并再次允许最多两轮恢复。
21. **相机间计数独立**：多台相机在同一阵位分别失败时，每台相机都拥有完整恢复预算，互不扣减。
22. **启动初始化不占预算**：full-shot 启动阶段既有的上电找 0 和回零不影响任何后续阵位失败事件的恢复次数。
23. **软件坐标失效**：初始失败后 `full_shot_position_valid=False`；即使旧 `current_angle` 与目标之差小于 `0.1°`，也不得快捷返回，更不能用一次角度读取重新设为有效。只有完整坐标重建验证成功后才能恢复有效。
24. **不可恢复错误**：非法物理边界配置或串口写失败不进行无意义坐标重建，返回明确原因并隔离；`KeyboardInterrupt` 不得被转成隔离结果。
25. **并发部分失败**：四台相机中一台恢复用尽，另外三台仍移动和拍照；失败相机不生成本阵位图片，并写入 metadata。
26. **拍照失败隔离**：某相机拍照抛异常后被隔离，其余相机和转台继续。
27. **用户中断**：任意等待或恢复阶段按 `Ctrl+C`，`KeyboardInterrupt` 正常向上传播并执行既有资源清理。
28. **焦距标定零改动兼容**：确认 `focus_calibration` 中 `initialize_lens()`、`return_to_home()`、`move_relative_for_calibration()` 和 `_wait_until_stopped()` 的调用代码段不增加新参数；现有返回结构、边界识别、交互输出和配置结果不变。
29. **策略隔离检查**：通过静态搜索或单元测试确认只有 ImageNode 的 full-shot 移动路径传入 `wait_policy="full_shot"`，`calibrate_single_lens()` 及其调用链始终使用默认 legacy 策略且不读取 `full_shot_*` 状态字段。
30. **初始化隔离检查**：full-shot 初始化成功后由 `ImageNode` 上层建立专用坐标状态；初始化失败只隔离对应相机；`initialize_lens()` 本身代码和 focus_calibration 行为不变。

### 10.12 实施顺序

1. 保留 `_wait_until_stopped()` 的默认 legacy 策略，并用回归测试继续锁定原行为和三元组返回值。
2. 更新 full-shot 等待分支，使稳定窗口只在正常等待到期且状态仍为 `0x01`/`0xFF` 时启动；保留单读取者和可调轮询间隔。
3. 在结构化等待结果中加入请求目标、有效目标、边界钳位、方向、正常/硬超时和稳定统计。
4. 为 full-shot 普通绝对角度移动接入每镜头运动边界；下发前钳位越界目标，并在碰壁后复核方向、目标和实测角度。
5. 将现有 home 与 absolute move 拆出“不自动恢复”的单次执行辅助函数，防止恢复递归。
6. 实现统一坐标系重建辅助函数：“上电找 0 → 等待 → 点击回 0 → full-shot wait 校验”。
7. 实现带恢复次数上限的 full-shot 运动协调器：初始任务失败后执行坐标重建；普通移动在回零成功后重新计算相对角度并重放原有效目标。
8. 明确 `HOME_RECOVERY_MAX_ATTEMPTS` 是“每台相机、每次原始运动失败事件”的局部预算；使用协调器局部变量计数，并以测试保证不在 home/move 层重复嵌套、不跨阵位累计、相机间互不影响。
9. 更新软件坐标有效性规则：初始失败立即失效，只有恢复及原任务最终验证成功后恢复有效；小位移快捷返回必须先验证。
10. 保持 `ImageNode.py` 当前并发结果传播、相机隔离和拍照过滤机制，并扩展 motion result/metadata 记录恢复历史。
11. 保持 `main.py` 的持续运行、metadata 异常记录和最终隔离汇总，更新控制台日志文案以区分初始失败、恢复中和恢复用尽。
12. 增加 mock 状态序列测试，覆盖 10.11 中全部非硬件场景和接口隔离检查。
13. 回归执行现有 `focus_calibration`，确认调用代码段、交互、等待语义和标定结果零行为变化。
14. 使用真实镜头调节顶部阈值，验证普通移动读数突变、`0xFF` 稳定、`0xF5`/`0x0B` 边界、两轮坐标重建以及单相机隔离行为。
