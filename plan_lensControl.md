# Lens Control Plan

## 目标

将镜头控制从当前“软件坐标和硬件查询角度混用”的状态，改为两套明确、互不冲突的坐标模式：

1. `software`：软件坐标模式，默认使用。程序只相信 `current_angle`，状态查询只判断电机是否停稳、运行、异常或触边，不使用硬件返回角度修正软件坐标。
2. `hardware`：硬件坐标模式，可选使用。该模式需要在每次归 0 后额外执行硬件清零指令，使电机内部坐标与软件坐标统一，然后才允许使用硬件返回角度参与到位校验和坐标更新。当前实现阶段先写出清零函数和调用位置，但调用语句默认注释，现场调试时手动取消注释。

两套模式的共同前提是：相信上电找 0 后，后续点击回 0 都能回到电机找到的物理 0 点。

## 当前问题

当前代码在初始化、回 0、移动完成后会调用状态和角度查询指令，并把查询得到的 `real_angle` 写回 `current_angle`。如果电机内部坐标没有执行清零，即使镜头物理上已经回到上电找 0 确认的位置，查询值仍可能是约 `2900` 度。此时把该值写入软件坐标，会破坏软件坐标系，导致后续根据 `target_angle - current_angle` 计算出的相对位移异常。

串行和并行都有这个问题，因为二者最终都调用同一个 `LensController.initialize_lens()`、`LensController.return_to_home()` 和 `LensController.move_to_absolute_angle()`。

另外，当前 `return_to_home()` 有一个判断盲点：它只在 `status == 0x00` 且 `abs(real_angle) < 1.0` 时输出“精准回到 0 点”，但当电机已经停稳、角度却明显异常时，没有单独输出“停稳但角度异常”的日志。这会让调试时很难区分“还没停稳”“没读到状态”和“内部坐标没有归零”。

## 新增参数

在命令行增加坐标模式参数，默认使用软件坐标：

```powershell
python main.py --lens-coordinate-mode software
python main.py --lens-coordinate-mode hardware
```

建议参数定义：

```python
parser.add_argument(
    '--lens-coordinate-mode',
    choices=['software', 'hardware'],
    default='software',
    help='镜头坐标模式：software=软件坐标默认模式，hardware=硬件清零后使用电机内部角度'
)
```

`ImagingSystem`、`ImagingNode` 和 `LensController` 需要传递该模式，避免在底层写死行为。

## 模式一：软件坐标模式

这是默认模式，目标是速度更快、逻辑更稳定，并避免未清零的硬件内部角度污染软件坐标。

### 坐标策略

1. 上电找 0 和点击回 0 用来确认物理 0 点。
2. 初始化成功后，程序强制设置 `current_angle = 0.0`。
3. 回 0 成功后，程序强制设置 `current_angle = 0.0`。
4. 每次移动前使用 `delta_angle = target_angle - current_angle` 计算相对运动量。
5. 电机停稳后，不使用硬件返回角度覆盖 `current_angle`。
6. 正常移动完成后，程序直接设置 `current_angle = target_angle`。
7. 图片目录中的角度继续来自目标软件角度。

### 状态查询策略

正式流程只使用状态查询判断电机状态，不使用查询返回角度作为定位依据。

状态查询可用于判断：

1. 电机是否停稳。
2. 是否仍在运动。
3. 是否触碰物理边界。
4. 是否出现错误或超时。

如果继续复用 `read_motor_status_and_position()`，调用方只读取 `status`，忽略 `real_angle`。后续可以拆出轻量函数：

```python
def read_motor_status(self):
    status, _ = self.read_motor_status_and_position()
    return status
```

### 软件模式下的函数行为

#### `initialize_lens()`

1. 执行上电找 0 指令。
2. 执行点击回 0 指令。
3. 轮询状态，只确认电机停稳。
4. 不要求 `abs(real_angle) < 1.0`。
5. 成功后设置 `self.current_angle = 0.0`。
6. 可保留注释掉的归零后角度查询日志，用于验证普通归零是否会让内部角度变成 0。

#### `return_to_home()`

1. 执行点击回 0 指令。
2. 轮询状态，只确认电机停稳。
3. 成功后设置 `self.current_angle = 0.0`。
4. 不用 `real_angle` 覆盖软件坐标。
5. 如果查询到了 `status == 0x00` 且 `real_angle` 不接近 0，应输出“电机已停稳，但内部角度不是 0”的调试日志；软件模式下这不是失败条件，只是说明内部坐标系未同步。

示意：

```python
if status == 0x00:
    if real_angle is not None and abs(real_angle) < 1.0:
        print(f"[{self.port}] 回 0 后内部角度接近 0: {real_angle:.2f}°")
    elif real_angle is not None:
        print(f"[{self.port}] 电机已停稳，但内部角度未归零: {real_angle:.2f}°；软件坐标仍设置为 0.0°")
    self.current_angle = 0.0
    return True
```

#### `move_to_absolute_angle()`

1. 使用 `delta_angle = target_angle_deg - self.current_angle`。
2. 发送相对运动指令。
3. 轮询状态，只确认停稳或异常。
4. 停稳成功后设置 `self.current_angle = target_angle_deg`。
5. 触边或异常时返回失败，不使用 `real_angle` 覆盖软件坐标。

## 模式二：硬件坐标模式

该模式用于调试或后续需要硬件内部角度闭环时使用。它必须先保证电机内部坐标和软件坐标统一。

### 坐标策略

1. 上电找 0 和点击回 0 用来确认物理 0 点。
2. 点击回 0 后，预留硬件清零调用位置；调试时取消注释，将当前位置写成电机内部 `0.0`。
3. 启用清零调用后，再读取硬件内部角度，要求其接近 `0.0`。
4. 后续移动完成后，可以使用 `real_angle` 做到位校验。
5. 如果 `real_angle` 合理，可以用它更新 `current_angle`。
6. 如果 `real_angle` 与目标偏差超限，应返回失败或进入异常处理，不应静默继续。

### 硬件清零指令

`documents/自动对焦教学-2` 中写明清零指令为：

```text
01 64 00 00000000 00000000 000000
```

按当前 `send_command()` 的用法，传入不带 CRC 的十六进制字符串：

```python
LENS_SET_ZERO_COMMAND = '0164000000000000000000000000'
```

计划中应实现一个独立清零函数，便于后续调试时手动打开调用：

```python
def set_current_position_as_hardware_zero(self):
    """将当前位置设置为电机内部 0 点。默认只提供函数，调用点先注释。"""
    return self.send_command(LENS_SET_ZERO_COMMAND, wait_time=0.2)
```

硬件清零指令应放在每次归零动作完成后，包括：

1. 初始化阶段的“上电找 0 + 点击回 0”之后。
2. 每轮拍摄结束后的 `return_to_home()` 之后。

关机阶段不再属于归零清零流程，见“关机前镜头收缩策略”。

当前实现阶段只在对应位置写出调用指令，并保持注释。调试时由人工取消注释：

```python
# 调试用途：归 0 后将当前位置写成电机内部 0 点。
# self.set_current_position_as_hardware_zero()
```

示意：

```python
def _maybe_set_hardware_zero(self):
    if self.coordinate_mode != 'hardware':
        return True

    # 默认先不自动执行；调试硬件坐标模式时手动取消注释。
    # return self.set_current_position_as_hardware_zero()
    return True
```

由于清零指令已经从文档确认，代码可以先构造函数；但自动调用仍保持注释，避免在未完成现场调试前改变电机内部坐标。

### 归零后内部状态验证

为了验证“一般情况下点击回 0 是否会自动把内部状态置 0”，保留一段默认注释的查询日志。它不改变控制逻辑，只用于人工调试。

示意：

```python
# 调试用途：验证点击回 0 后，电机内部角度是否会自动归零。
# status, real_angle = self.read_motor_status_and_position()
# print(f"[{self.port}] 回 0 后内部状态={status}, 内部角度={real_angle}°")
```

这段调试查询可以放在：

1. `initialize_lens()` 点击回 0 后。
2. `return_to_home()` 停稳后。
3. 硬件清零指令执行后，用于确认清零是否生效。

### 硬件模式下的函数行为

#### `initialize_lens()`

1. 执行上电找 0 指令。
2. 执行点击回 0 指令。
3. 预留硬件清零调用位置，默认注释；调试硬件模式时取消注释执行。
4. 查询 `real_angle`。
5. 要求 `abs(real_angle) < 1.0` 才认为初始化成功。
6. 设置 `self.current_angle = real_angle` 或直接设置为 `0.0`。
7. 如果停稳但 `real_angle` 不接近 0，输出明确异常日志并返回失败。

#### `return_to_home()`

1. 执行点击回 0 指令。
2. 等待电机停稳。
3. 预留硬件清零调用位置，默认注释；调试硬件模式时取消注释执行。
4. 查询 `real_angle` 并确认接近 `0.0`。
5. 成功后设置 `self.current_angle = 0.0`。
6. 如果停稳但清零前或清零后的 `real_angle` 异常，输出明确日志。
7. 如果清零后 `real_angle` 仍不接近 0，返回失败。

#### `move_to_absolute_angle()`

1. 使用 `delta_angle = target_angle_deg - self.current_angle`。
2. 移动完成后查询 `real_angle`。
3. 如果 `abs(real_angle - target_angle_deg) < tolerance`，设置 `self.current_angle = real_angle`。
4. 如果偏差超限，返回失败并输出异常日志。

## 关机前镜头收缩策略

当前 `shutdown_lens()` 会调用 `return_to_home()`。根据现场观察，这会让镜头伸展到最远端，不适合作为关机前动作。计划改为：关机前不再执行点击回 0，不再调用 `return_to_home()`。

关机前镜头应执行收缩到底端的动作，二选一：

1. 优先方案：执行上电找 0 指令，让镜头按硬件找 0 流程收缩并碰到最底端。
2. 备选方案：执行正向旋转 `3000°`，让镜头收缩并碰到最底端。

需要在 `LensController.py` 中新增关机专用函数，例如：

```python
def retract_lens_for_shutdown(self):
    """关机前收缩镜头到底端，不执行点击回 0。"""
    # 方案一：上电找 0 指令。
    # 方案二：如需改用正向 3000° 收缩，可在现场调试后替换为相对运动指令。
    ok = self.send_command('0164060000000000000000000000', wait_time=0.5)
    time.sleep(5.0)
    return ok
```

`shutdown_lens()` 应改为：

```python
def shutdown_lens(self):
    if not self.serial or not self.serial.is_open:
        return
    print(f"[{self.port}] 收到关机信号，正在收缩镜头到底端...")
    self.retract_lens_for_shutdown()
    self.close()
```

注意：关机收缩动作不是软件坐标意义上的回 0，也不参与 `current_angle` 的正常拍摄坐标闭环。执行后程序即将关闭，不需要再用硬件角度覆盖 `current_angle`。

## 保留但默认注释的调试能力

以下能力保留在代码中，供后续排查内部坐标系时打开。

1. 状态和角度查询指令：
   - 保留 `read_motor_status_and_position()`。
   - 保留 `0165000000000000000000000000` 查询指令。
   - `software` 模式默认不使用返回角度修正 `current_angle`。
   - `hardware` 模式只有在执行硬件清零后才使用返回角度。

2. 归零后的内部状态验证：
   - 默认注释。
   - 用于验证点击回 0 是否会自动让内部角度归零。
   - 只输出日志，不参与正式控制。

3. 每次归零后的硬件设 0 点指令：
   - 清零函数应实现，指令为 `0164000000000000000000000000`。
   - 每个归零后的调用点默认注释。
   - 注释说明用途：让电机内部坐标系与软件坐标系统一，方便调试和硬件坐标闭环。

## 拟修改点

### `main.py`

1. 新增 `--lens-coordinate-mode` 参数。
2. 默认值为 `software`。
3. 将参数传入 `ImagingSystem`。

### `ImageNode.py`

1. `ImagingSystem` 接收 `lens_coordinate_mode`。
2. `ImagingNode` 接收 `lens_coordinate_mode`。
3. 创建 `LensController` 时传入该模式。
4. 串行和并行入口不分叉，只负责调用同一个底层逻辑。

### `LensController.py`

1. `LensController.__init__()` 增加 `coordinate_mode='software'`。
2. 增加模式校验，非法模式直接报错。
3. 增加内部辅助函数，例如：
   - `_wait_until_stopped()`
   - `_set_software_zero()`
   - `_maybe_set_hardware_zero()`
   - `set_current_position_as_hardware_zero()`
   - `_debug_log_internal_position_after_home()`
4. `initialize_lens()` 按模式分支处理。
5. `return_to_home()` 按模式分支处理。
6. `move_to_absolute_angle()` 按模式分支处理。
7. 禁止在 `software` 模式下用 `real_angle` 覆盖 `current_angle`。
8. 在 `return_to_home()` 中补充“停稳但内部角度异常”的日志分支。
9. `shutdown_lens()` 不再调用 `return_to_home()`，改为调用关机专用的 `retract_lens_for_shutdown()`。

## 验收标准

1. 默认不传参数时使用 `software` 模式。
2. `software` 模式初始化后软件坐标恒为 `0.0`。
3. `software` 模式回 0 后软件坐标恒为 `0.0`。
4. `software` 模式正常移动到 `target_angle` 后，软件坐标更新为 `target_angle`。
5. `software` 模式下，即使电机内部角度查询返回约 `2900` 度，也不会影响 `current_angle`。
6. `return_to_home()` 在电机停稳但内部角度不是 0 时，会输出明确日志。
7. `hardware` 模式必须显式选择。
8. `hardware` 模式只有在回 0 后实际启用硬件清零调用时，才允许使用硬件角度闭环。
9. 硬件清零函数存在，使用指令 `0164000000000000000000000000`。
10. 每次归零后的硬件清零调用点默认保持注释，调试时手动取消注释。
11. 归零后内部状态验证默认保持注释，调试时手动取消注释。
12. 串行和并行调用在同一坐标模式下行为一致。
13. 图片目录中的角度继续来自目标软件角度，而不是硬件查询角度。
14. 关机前不再执行点击回 0；关机前执行上电找 0 或正向 `3000°` 收缩到底端。

## 风险和注意事项

1. 两套模式都依赖上电找 0 和点击回 0 的物理准确性。
2. `software` 模式无法发现小幅丢步，只能通过状态、超时、边界和最终成像结果判断异常。
3. `hardware` 模式依赖硬件清零指令正确可靠；虽然文档已给出指令，仍需要现场验证执行效果。
4. 如果运动过程中卡滞但状态仍返回停稳，`software` 模式可能认为已经到位。
5. 如果 `hardware` 模式清零失败或内部角度异常，应中断流程，不应静默继续。
6. `return_to_home()` 中“停稳但内部角度异常”的日志不能在 `software` 模式中直接作为失败条件，否则会重新引入内部坐标污染软件坐标的问题。
7. 关机收缩策略需要现场确认“上电找 0”和“正向 3000°”哪一个方向确实对应镜头收缩到底端，避免误用方向导致继续伸展。
