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

## 10. 镜头回零碰壁容差修复计划

### 10.1 问题描述

在 `full_shot` 模式下，每轮拍摄结束后镜头执行回零指令。回零时通过 `read_motor_status_and_position()` 查询电机状态，该函数是 `LensController` 中唯一的电机状态查询入口——不论是正常移动 (`move_to_absolute_angle`) 还是回零 (`return_to_home`)，都通过它获取状态码和内部角度。

当前 `_wait_until_stopped` 对物理边界（`0x0B`/`0xF5`）的处理是**一律返回 `False`**。当镜头回零碰壁且 `real_angle ≈ -2.5°` 时，由于返回值 `False`：

1. `return_to_home` 直接返回 `False`，不执行 `_set_software_zero()`，`current_angle` 保持旧值不变。
2. `move_to_absolute_angle` 调用方 (`parallel_move_lenses`) 忽略返回值，不感知失败。
3. 软件坐标与实际物理位置从此脱节。
4. 后续阵位如果 target_angle 恰好等于旧的 `current_angle`（尤其是 `lens_steps=1` 时所有阵位目标角度相同），`move_to_absolute_angle` 在 `abs(delta) < 0.1` 处直接返回 `True`，不下发任何移动指令。该镜头在所有剩余阵位中物理上停滞在碰壁位置，但流程静默继续，产出的图片焦距完全错误。

### 10.2 参考设计：`focus_calibration` 模式的边界容差

焦距标定模式中对零点边界已有容差设计，位于 [main.py:759-810](main.py#L759-L810) 的 `classify_calibration_move`：

```python
# 情形一：电机直接报告边界状态
if result.get("boundary"):
    kind = "zero_boundary" if status_code == LENS_BOUNDARY_ZERO else "far_boundary"
    return {
        "kind": kind,
        "corrected_angle": measured_angle,
        "corrected_expected_angle": measured_angle,  # 用实测值修正期望值
        ...
    }

# 情形二：电机未报告边界，但相对位移为负、期望角度为负、实测接近 0
if (
    relative_delta < 0
    and expected_angle < 0
    and abs(measured_angle) <= ZERO_VERIFY_TOLERANCE  # 1.0°
):
    return {
        "kind": "zero_boundary",
        "corrected_angle": measured_angle,
        "corrected_expected_angle": measured_angle,
        ...
    }
```

核心思想：碰壁时**不视为错误**，而是用实测角度修正期望值，让调用方继续以实测位置作为新的基准进行后续操作。

### 10.3 修复方案

#### 10.3.1 新增容差常量

在 `LensController.py` 中新增：

```python
# 回零/移动碰壁容差：当电机触碰物理边界，但内部角度与目标角度之差
# 在此范围内时，视为已到位（以实测角度为准），不再报错。
BOUNDARY_ANGLE_TOLERANCE = 5.0  # 度
```

该值与 `main.py` 中已有的 `ANGLE_VERIFY_TOLERANCE = 5.0` 语义一致，用于同一类"角度偏差可接受"的判断场景。

#### 10.3.2 修改 `_wait_until_stopped`

新增可选参数 `expected_angle` 和 `angle_tolerance`：

```python
def _wait_until_stopped(self, timeout_s=10.0, interval_s=0.5,
                        expected_angle=None, angle_tolerance=None):
```

当检测到边界状态 (`0x0B`/`0xF5`) 时，增加容差判断：

```python
if status in (0x0B, 0xF5):
    boundary_name = "零点边界" if status == 0xF5 else "远端边界"
    print(f"⚠️ [{self.port}] 电机触碰物理{boundary_name}。状态={status}, 内部角度={real_angle}°")

    if expected_angle is not None and real_angle is not None and angle_tolerance is not None:
        if abs(real_angle - expected_angle) <= angle_tolerance:
            print(f"✅ [{self.port}] 碰壁但内部角度在容差内 "
                  f"(实测={real_angle:.2f}°, 期望={expected_angle:.2f}°, "
                  f"偏差={abs(real_angle - expected_angle):.2f}° <= {angle_tolerance}°)，视为到位。")
            return True, status, real_angle

    return False, status, real_angle
```

#### 10.3.3 修改 `return_to_home`

在调用 `_wait_until_stopped` 时传入期望角度 0.0 和容差：

```python
def return_to_home(self):
    ...
    self.send_command(LENS_RETURN_HOME_COMMAND, wait_time=0.5)

    stopped, status, real_angle = self._wait_until_stopped(
        timeout_s=10.0, interval_s=0.5,
        expected_angle=0.0, angle_tolerance=BOUNDARY_ANGLE_TOLERANCE,
    )
    if not stopped:
        print(f"❌ [{self.port}] 回 0 超时或异常！...")
        return False

    # 软件坐标模式：即使碰壁被容差通过，仍用实测角度校准 current_angle
    if self.coordinate_mode == COORDINATE_MODE_SOFTWARE:
        self._log_home_internal_angle(real_angle)
        if real_angle is not None:
            self.current_angle = real_angle  # 用实测值修正，不再无条件设 0.0
            print(f"🎉 [{self.port}] 寻零完成，软件坐标已校准为 {real_angle:.2f}°。")
        else:
            self._set_software_zero()
            print(f"🎉 [{self.port}] 寻零完成，软件坐标已设置为 0.0°。")
        return True
    ...
```

关键变化：软件坐标模式下不再无条件 `current_angle = 0.0`，而是优先采用实测 `real_angle`，这样当碰壁容差通过时（如 `real_angle = -2.5°`），后续阵位的 `delta_angle = target - (-2.5)` 能够正确算出正向位移，驱动镜头离开边界。

#### 10.3.4 修改 `move_to_absolute_angle`

在调用 `_wait_until_stopped` 时传入目标角度和容差：

```python
def move_to_absolute_angle(self, target_angle_deg, speed_rpm=LENS_SPEED_RPM):
    ...
    stopped, status, real_angle = self._wait_until_stopped(
        timeout_s=4.0, interval_s=0.2,
        expected_angle=target_angle_deg, angle_tolerance=BOUNDARY_ANGLE_TOLERANCE,
    )
    if not stopped:
        print(f"❌ 主动查验超时或异常！...")
        return False

    if self.coordinate_mode == COORDINATE_MODE_SOFTWARE:
        # 软件坐标模式：用实测值或目标值更新坐标
        if real_angle is not None:
            self.current_angle = real_angle
            print(f"✅ 电机停稳。内部角度={real_angle:.2f}°，软件坐标已校准。")
        else:
            self.current_angle = target_angle_deg
            print(f"✅ 电机停稳。软件坐标更新为 {target_angle_deg:.2f}°")
        return True
    ...
```

#### 10.3.5 `move_relative_for_calibration` —— 无需修改

该函数仅在 `focus_calibration` 交互式标定中使用。其内部逻辑为：

```python
stopped, status, real_angle = self._wait_until_stopped(...)
boundary = status in LENS_BOUNDARY_STATUSES

if boundary:                    # ← 第一个判断分支
    return {"ok": True, "boundary": True, ...}
```

由于 `boundary` 判断位于最前面，无论 `_wait_until_stopped` 返回 `stopped=True` 还是 `False`，只要 `status == 0xF5` 或 `0x0B`，就会进入边界分支，返回结构 `{"ok": True, "boundary": True}` 完全不变。上游 `classify_calibration_move` 的 `result.get("boundary")` 行为不受影响，且 `focus_calibration` 流程自身已有 `classify_calibration_move` 做交互级边界容差处理。**因此该函数不需要任何修改。**

### 10.4 影响分析

#### 正向影响

- **回零碰壁自动恢复**：`real_angle ≈ -2.5°` 在 5° 容差内，视为成功。`current_angle` 更新为实测值，后续阵位的 `delta_angle` 计算正确，镜头能正常驱动离开边界。
- **静默故障变为可见**：即使容差通过，日志也会输出 `碰壁但内部角度在容差内`，方便事后排查。
- **正常移动碰壁同样受益**：`move_to_absolute_angle` 也加入了容差，即使非回零场景下轻微碰壁也能容错。

#### 风险

- **容差过大可能掩盖真正的硬件故障**：5° 对应约 455 个编码器脉冲（32768 分辨率下），如果镜头实际卡在非零点位置，容差可能将其误判为正常。但鉴于 `ANGLE_VERIFY_TOLERANCE` 在标定流程中已使用相同的 5° 阈值，该值属于经过实践检验的合理范围。

### 10.5 预计影响文件

- `LensController.py`
  - 新增 `BOUNDARY_ANGLE_TOLERANCE` 常量。
  - `_wait_until_stopped` 新增 `expected_angle` 和 `angle_tolerance` 可选参数及容差判断逻辑。
  - `return_to_home`：传入容差参数；软件坐标模式改用 `real_angle` 校准 `current_angle`。
  - `move_to_absolute_angle`：传入容差参数；软件坐标模式改用 `real_angle` 校准 `current_angle`。
  - `move_relative_for_calibration`：无需修改（其 `boundary` 分支在最前，不受 `_wait_until_stopped` 返回值变化影响）。

- `main.py`
  - 无需修改。`full_shot` 调用方 (`parallel_move_lenses` 等) 不感知内部容差逻辑。

### 10.6 验证计划

1. **正常回零不受影响**：`status == 0x00` 时，容差分支不触发，行为与改造前完全一致。
2. **碰壁容差通过**：模拟或实际触发 `status == 0xF5, real_angle = -2.5°`，验证 `return_to_home` 返回 `True`，`current_angle` 更新为 `-2.5°`，控制台输出 `碰壁但内部角度在容差内`。
3. **碰壁容差不通过**：模拟 `real_angle = -10°`（超出 5° 容差），验证 `return_to_home` 返回 `False`，行为与改造前一致。
4. **后续阵位正常驱动**：容差通过后，下一个阵位的 `move_to_absolute_angle(2000°)` 计算 `delta = 2000 - (-2.5) = 2002.5°`，正确下发正向移动指令，镜头离开边界。
5. **焦距标定兼容**：执行 `focus_calibration` 流程的交互式标定，验证边界碰壁提示和容差行为无退化。
6. **远端边界不受影响**：`status == 0x0B` 远端边界时，如果 `real_angle` 在容差范围内，同样可通过容差检查；超出容差则保持原有失败行为。

### 10.7 实施顺序

1. 在 `LensController.py` 新增 `BOUNDARY_ANGLE_TOLERANCE` 常量。
2. 修改 `_wait_until_stopped`，增加 `expected_angle`/`angle_tolerance` 可选参数和容差判断。
3. 修改 `return_to_home`，传入容差参数并改用 `real_angle` 校准软件坐标。
4. 修改 `move_to_absolute_angle`，传入容差参数并优先使用 `real_angle` 更新坐标。
5. 确认 `move_relative_for_calibration` 无需修改（其 `boundary` 判断路径不受影响）。
6. 硬件联调验证：正常回零、碰壁容差通过、碰壁容差不通过、后续阵位驱动恢复、标定兼容。
