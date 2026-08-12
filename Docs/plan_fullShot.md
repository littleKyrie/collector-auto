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

## 10. 镜头到位判定、回零恢复与故障隔离计划

### 10.1 问题复盘与设计目标

在 `full_shot` 模式下，每个阵位拍摄完成后，所有镜头都会执行官方“点击回 0”指令。现有日志中，2 号相机对应的 COM9 在回零阶段出现：

```text
状态=255，最后内部角度=-2.6477°，软件角度=2110.02°
```

这里的 `255` 是十六进制 `0xFF`，并不是 `0xF5`（`0xF5` 的十进制值为 245）。当前 `_wait_until_stopped()` 把 `0xFF` 当作运行状态，所以即使镜头已经停在零点附近，也会一直等待到超时。回零失败后又保留了旧的 `current_angle=2110.02°`；下一阵位目标仍为约 `2110°` 时，`move_to_absolute_angle()` 因 `abs(delta_angle) < 0.1` 直接返回成功，没有重新下发移动指令，最终导致焦距错误。

本次改造目标如下：

1. 状态码仍作为首要证据，同时使用“目标角度误差 + 连续角度稳定”辅助判断到位。
2. `0xFF` 不能因为单次角度落入容差就直接视为成功；只有连续稳定后才允许推断到位。
3. 稳定性判断同时用于 `full_shot` 的回零和普通绝对角度移动，不检测速度，只检测连续角度样本。
4. 回零阶段第一次失败后，按照厂商建议尝试一次“上电找 0 → 点击回 0”恢复；该恢复策略不得用于普通角度移动。
5. 单个镜头最终失败时，默认隔离该相机及镜头，其他相机继续完成整段拍摄；程序不主动中断，用户需要停止整段任务时使用 `Ctrl+C`。
6. 软件坐标必须带有效性和质量状态，禁止使用失败前的旧坐标静默跳过下一次移动。
7. 所有并发操作必须收集并向上传播每台镜头的结构化结果，同时输出可定位问题的详细日志。
8. `focus_calibration` 当前运行正常，必须保持其现有调用链、等待逻辑、返回结构和边界判定不变；本次增强只接入 `full_shot`。

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
| `0x00` | 正常停稳 | 只有实测角度也在目标容差内才确认成功；角度不符时返回“停稳但位置不符” |
| `0x01` | 运行中 | 继续轮询；若角度已进入目标容差，可以进入稳定候选，但不能单次成功 |
| `0xFF` | 当前代码视为运行中；现场可能出现状态滞留 | 继续轮询；目标附近连续稳定后允许标记为“推断到位” |
| `0xF5` | 当前映射为零点边界 | 回零时角度在零点容差内则确认成功；普通移动时必须结合目标和运动方向判断 |
| `0x0B` | 当前映射为远端边界 | 普通移动时必须结合目标和运动方向判断；回零阶段原则上视为异常 |
| `-1` | 未读取到有效帧 | 记录串口读取失败次数；连续失败或达到绝对超时后返回故障 |
| 其他状态 | 未知 | 详细记录原始状态，达到策略规定条件后返回未知状态故障 |

所有状态日志统一同时输出十六进制和十进制，例如：

```text
status=0xFF (255)
status=0xF5 (245)
```

负角度本身不能直接等价为回零成功。它只能说明编码器实测位置已经接近或越过内部零点。回零成功仍需满足以下任一条件：

- `0x00` 且实测角度在回零容差内；
- `0xF5` 且实测角度在回零容差内；
- `0x01`/`0xFF` 且实测角度在回零容差内，并在稳定窗口内连续稳定。

### 10.3 全局可调参数

所有到位判定和恢复参数统一放在 `LensController.py` 顶部、现有 `BOUNDARY_ANGLE_TOLERANCE` 附近，并逐项写明单位、用途和现场调参影响。建议初始值如下，最终数值以硬件联调为准：

```python
# 物理边界角度容差；用于判断边界状态对应的位置是否合理。
BOUNDARY_ANGLE_TOLERANCE = 5.0  # 度

# 回零目标角度容差；允许编码器在机械零点附近存在负值、回差或轻微偏移。
HOME_ANGLE_TOLERANCE = 5.0  # 度

# 普通绝对角度移动的到位容差；应比回零容差更严格，避免影响焦距精度。
MOVE_ANGLE_TOLERANCE = 2.0  # 度

# 连续角度稳定所需时间；候选样本必须覆盖至少该时长才能推断到位。
POSITION_STABLE_DURATION = 1.0  # 秒

# 稳定窗口内允许的最大角度极差：max(samples) - min(samples)。
POSITION_STABLE_ANGLE_SPAN = 0.5  # 度

# 状态轮询间隔；回零和普通移动默认共用，调用方仍可按需覆盖。
POSITION_POLL_INTERVAL = 0.5  # 秒

# 回零正常等待时间；超过后不立即失败，若已进入稳定候选则继续观察。
HOME_NORMAL_TIMEOUT = 10.0  # 秒

# 回零单次尝试的绝对截止时间；包括跨过正常超时后的稳定观察时间。
HOME_HARD_TIMEOUT = 15.0  # 秒

# 普通移动正常等待时间和绝对截止时间。
MOVE_NORMAL_TIMEOUT = 4.0  # 秒
MOVE_HARD_TIMEOUT = 8.0  # 秒

# 厂商恢复流程的最大执行次数。为避免反复撞击或持续堵转，默认只恢复一次。
HOME_RECOVERY_MAX_ATTEMPTS = 1

# 厂商“上电找 0”指令后的静默等待时间，再执行“点击回 0”。
POWER_ON_HOMING_WAIT = 10.0  # 秒
```

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
    expected_angle=target_angle,
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

#### 10.4.2 两阶段时间策略

稳定判断不会先等待到正常超时才开始，而是在任意一次轮询进入目标容差时立即开始收集候选样本：

1. 在正常等待时间内，如果返回明确终态并且角度符合目标，立即成功。
2. 当 `0x01` 或 `0xFF` 的实测角度进入目标容差时，立即建立稳定候选窗口。
3. 候选角度离开目标容差，或者稳定窗口极差超限时，清空候选并继续等待。
4. 候选样本覆盖 `POSITION_STABLE_DURATION` 且角度极差不超限时，返回“推断到位”。
5. 如果候选窗口跨过 `normal_timeout_s`，允许继续完成稳定观察，但不得超过 `hard_timeout_s`。
6. 达到 `hard_timeout_s` 仍无法确认到位，返回故障，不无限等待 `0xF5`。

该策略可以处理“第 6 秒进入目标范围、第 7.5 秒稳定成功”，也可以处理“第 9.8 秒才进入目标范围、跨过 10 秒继续观察”，同时保证最迟在硬超时截止。

#### 10.4.3 结构化返回值

为兼容所有既有调用，函数仍返回 `(stopped, status, real_angle)`。仅在 `wait_policy="full_shot"` 时，额外把统一诊断字典写入 `self.full_shot_last_wait_result`：

```python
{
    "ok": True,
    "reason": "inferred_stable_at_target",
    "operation": "home",
    "status": 0xFF,
    "real_angle": -2.65,
    "expected_angle": 0.0,
    "deviation": 2.65,
    "position_quality": "inferred",
    "stable_duration": 1.6,
    "stable_span": 0.08,
    "elapsed": 8.2,
    "boundary": "zero",
}
```

`reason` 至少覆盖：

- `confirmed_stopped_at_target`
- `confirmed_zero_boundary`
- `confirmed_far_boundary`
- `inferred_stable_at_target`
- `stopped_position_mismatch`
- `boundary_position_mismatch`
- `timeout_still_moving`
- `timeout_unstable_at_target`
- `serial_read_failed`
- `unexpected_status`

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

如果 `full_shot_position_valid=False`，则必须先读取并稳定确认真实位置；无法确认时返回当前相机移动失败，不能使用旧的 `current_angle` 跳过指令。该约束不加入原 `move_relative_for_calibration()`。

### 10.6 回零专用恢复策略

厂商建议的“上电找 0 → 点击回 0”只应用于 `return_to_home(wait_policy="full_shot")`，不应用于普通绝对角度移动、系统初始化或 `focus_calibration` 默认调用的 `return_to_home()`，也不在 `_wait_until_stopped()` 内自动触发。等待函数只负责观测和分类，恢复动作由 `return_to_home()` 的 full-shot 策略分支编排。

回零流程分为两个阶段：

#### 第一阶段：正常点击回 0

1. 下发 `LENS_RETURN_HOME_COMMAND`。
2. 使用 `HOME_NORMAL_TIMEOUT`、`HOME_HARD_TIMEOUT`、`HOME_ANGLE_TOLERANCE` 等待。
3. 如果得到明确成功或 `0xFF` 零点附近连续稳定，则同步实测坐标并返回成功。

#### 第二阶段：厂商恢复流程

仅当第一阶段最终失败时执行，默认最多一次：

1. 记录第一次失败的完整结果。
2. 输出醒目的恢复日志，说明即将执行厂商恢复方案。
3. 下发 `LENS_POWER_ON_HOMING_COMMAND`。
4. 静默等待 `POWER_ON_HOMING_WAIT`，供硬件完成找零标定。
5. 下发 `LENS_RETURN_HOME_COMMAND`。
6. 再次使用完整的回零到位判定等待结果。
7. 第二次成功：同步实测角度，将结果标记为 `recovered=True`，继续参与后续拍摄。
8. 第二次仍失败：将该镜头标记为隔离，不再反复执行恢复指令。

恢复期间同样受硬超时约束。`Ctrl+C` 必须能够正常传播，不能被宽泛的 `except Exception` 吞掉。

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

1. 普通角度移动经过判定仍失败：立即隔离该相机，本阵位不触发该相机拍照，后续阵位也跳过其镜头移动和拍照。
2. 阵位拍摄结束后的回零经过厂商恢复仍失败：隔离该相机，从下一阵位开始跳过；已经成功保存的历史图片保留。
3. 相机拍照本身失败或抛出异常：记录并隔离该相机，其他相机继续。
4. 隔离不关闭整个系统、不终止转台循环，也不自动重试无上限次数。
5. 任务结束时输出隔离相机汇总，明确哪些阵位缺图或可能存在焦距风险。

需要特别区分回零结果：

- `0xFF + 零点附近连续稳定` 属于可继续运行的“推断成功”，不隔离相机，但输出警告级日志；
- 第一次回零失败、厂商恢复成功属于“恢复成功”，不隔离相机；
- 厂商恢复后仍无法确认到位，才执行隔离。

### 10.8 并发结果传播与拍照过滤

`parallel_move_lenses()`、`parallel_move_lenses_by_camera()`、`parallel_snap_rotation_step()` 不能只调用 `concurrent.futures.wait()` 后无条件输出成功。必须逐个调用 `future.result()`，收集相机名、串口和结构化结果，并返回汇总：

```python
{
    "ok": False,
    "successful": ["1", "3", "4"],
    "failed": {
        "2": {
            "port": "COM9",
            "reason": "home_recovery_failed",
            "status": 0xFF,
            "real_angle": -2.65,
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
- 操作类型、尝试序号、是否属于厂商恢复；
- 目标角度、软件起始角度、软件坐标是否有效、坐标质量；
- 原始状态码的十六进制与十进制形式；
- 实测角度、目标偏差；
- 稳定候选开始/取消原因、样本数、覆盖时间、角度极差；
- 正常超时和硬超时各自是否到达；
- 最终 `reason`、是否推断成功、是否恢复成功、是否隔离。

建议关键日志示例：

```text
⚠️ [相机2/COM9] 回零状态仍为 0xFF (255)，实测=-2.65°，偏差=2.65°；进入稳定候选。
✅ [相机2/COM9] 连续稳定 1.60s，角度极差=0.08°；推断已回到零点，position_quality=inferred。
⚠️ [相机2/COM9] 首次回零失败 reason=timeout_unstable_at_target；执行厂商恢复 1/1：上电找0 → 点击回0。
✅ [相机2/COM9] 厂商恢复成功，实测=-0.03°，status=0x00 (0)。
❌ [相机2/COM9] 厂商恢复后仍无法确认到位，reason=timeout_still_moving；已隔离该相机，其余相机继续。
```

轮询日志需要限频或仅在状态变化、候选状态变化、固定时间间隔时输出，避免每 0.2 秒刷屏，但内部结果应保留最后状态和关键统计值。

### 10.10 预计影响文件

- `LensController.py`
  - 在顶部集中增加全部可调阈值及注释。
  - `_wait_until_stopped()` 增加默认值为 `legacy` 的策略参数；legacy 分支保持原逻辑和三元组返回值，full-shot 分支增加稳定判定和详细结果。
  - `initialize_lens()` 和 `move_relative_for_calibration()` 的代码及调用方式保持不变。
  - `return_to_home()`、`move_to_absolute_angle()` 增加默认值为 `legacy` 的可选策略参数，默认调用行为不变。
  - 增加带 `full_shot_` 前缀的软件坐标有效性、质量、等待结果和操作结果字段。
  - `return_to_home()` 的 full-shot 分支编排首次回零和一次厂商恢复流程。
  - `move_to_absolute_angle()` 的 full-shot 分支使用普通移动容差和稳定判定，并修复无效坐标下的小位移跳过。

- `ImageNode.py`
  - 为 `ImagingNode` 增加启用/隔离状态和失败上下文。
  - `parallel_global_homing()` 收集每台镜头的初始化结果；成功时只在上层初始化 full-shot 坐标质量状态，失败时隔离对应相机，不修改 `initialize_lens()`。
  - full-shot 并发移动显式调用 `move_to_absolute_angle(..., wait_policy="full_shot")`；目标为 0 时该方法继续把策略传给共享 `return_to_home()`。
  - 所有并发移动与拍摄函数读取 `future.result()`、返回汇总并过滤隔离相机。
  - 日志中同时带相机名和串口。

- `main.py`
  - `full_shot` 根据移动汇总只拍摄成功相机，不因单相机失败终止整个循环。
  - 回零失败恢复后隔离对应相机，其余相机和转台继续。
  - step metadata 记录镜头状态、跳过原因和位置质量。
  - 保持 `KeyboardInterrupt` 可由用户主动中止整段任务。

- 测试文件
  - 增加无硬件的状态序列测试、稳定窗口测试、厂商恢复测试、隔离与并发汇总测试。

### 10.11 验证计划

1. **正常移动**：`0x01 → 0x00`，最终角度在目标容差内，返回 `confirmed`。
2. **正常回零**：`0xFF → 0x00`，零点角度符合容差，不触发厂商恢复。
3. **明确零点边界**：`0xF5/-2.5°`，回零确认成功并用实测角度校准软件坐标。
4. **0xFF 稳定推断**：连续 `0xFF`，角度在零点附近且覆盖稳定时间、极差不超限，返回 `inferred`，不触发恢复、不隔离。
5. **0xFF 仍在变化**：角度虽在零点容差内但极差持续超限，不能推断成功；达到硬超时后进入厂商恢复。
6. **候选跨正常超时**：在正常超时前刚进入容差，允许跨过正常超时完成稳定观察，但不能越过硬超时。
7. **停稳但位置错误**：`0x00` 但角度超出目标容差，返回 `stopped_position_mismatch`，不能误判成功。
8. **边界方向错误**：回零时读到 `0x0B`，不得按零点成功处理。
9. **普通移动稳定推断**：`0xFF` 在普通目标附近连续稳定，按 `MOVE_ANGLE_TOLERANCE` 返回 `inferred`。
10. **厂商恢复成功**：首次回零失败，执行一次“上电找 0 → 点击回 0”后成功，不隔离相机。
11. **厂商恢复失败**：第二次仍失败，只隔离故障相机，其余相机继续后续阵位。
12. **软件坐标失效**：失败后 `full_shot_position_valid=False`，即使旧 `current_angle` 与下一目标之差小于 `0.1°`，也不得快捷返回。
13. **并发部分失败**：四台相机中一台失败，另外三台仍移动和拍照；失败相机不生成本阵位图片，并写入 metadata。
14. **拍照失败隔离**：某相机拍照抛异常后被隔离，其余相机和转台继续。
15. **用户中断**：任意等待或恢复阶段按 `Ctrl+C`，`KeyboardInterrupt` 正常向上传播并执行既有资源清理。
16. **焦距标定零改动兼容**：确认 `focus_calibration` 中 `initialize_lens()`、`return_to_home()`、`move_relative_for_calibration()` 和 `_wait_until_stopped()` 的调用代码段不增加新参数；现有返回结构、边界识别、交互输出和配置结果不变。
17. **策略隔离检查**：通过静态搜索或单元测试确认只有 ImageNode 的 full-shot 移动路径传入 `wait_policy="full_shot"`，`calibrate_single_lens()` 及其调用链始终使用默认 legacy 策略且不读取 `full_shot_*` 状态字段。
18. **初始化隔离检查**：full-shot 初始化成功后由 `ImageNode` 上层建立专用坐标状态；初始化失败只隔离对应相机；`initialize_lens()` 本身代码和 focus_calibration 行为不变。

### 10.12 实施顺序

1. 在 `LensController.py` 顶部增加全部可调阈值和说明。
2. 为 `_wait_until_stopped()` 增加默认 legacy 策略，并用回归测试锁定原行为和三元组返回值。
3. 定义 full-shot 诊断结果结构和带前缀的软件坐标质量状态。
4. 在 `_wait_until_stopped()` 的 full-shot 分支实现目标误差、连续角度稳定、正常超时和硬超时。
5. 为 `move_to_absolute_angle()` 增加可选策略参数，仅在 full-shot 分支修复无效坐标的小位移快捷返回。
6. 为 `return_to_home()` 增加可选策略参数，仅在 full-shot 分支接入首次回零及一次厂商恢复流程。
7. 修改 `ImageNode.py`，让 full-shot 显式传入增强策略，并实现并发结果传播、相机隔离和拍照过滤。
8. 修改 `main.py`，实现持续运行策略、metadata 异常记录和最终汇总。
9. 增加 mock 状态序列测试，覆盖 10.11 中全部非硬件场景和接口隔离检查。
10. 回归执行现有 `focus_calibration`，确认交互和标定结果零行为变化。
11. 使用真实镜头调节顶部阈值，验证 `0xFF` 稳定、`0xF5` 边界、厂商恢复以及单相机隔离行为。
