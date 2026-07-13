# 旋转拍摄保存路径与焦距标定边界处理计划

## 背景

本计划覆盖两个需求：

1. 修改旋转拍摄模式中图片结果的保存路径。
2. 分析并修正焦距标定时大幅负角度触达物理边界后被误判为标定失败的问题。

当前涉及的主要文件：

- `main.py`
  - `run_rotation_multi_shot()` 中创建拍照目录与写入 step metadata。
  - `calibrate_single_lens()` 中执行焦距标定交互、移动镜头、校验移动结果。
- `ImageNode.py`
  - `ImagingSystem.serial_snap_all()` 与 `parallel_snap_all()` 接收目录并为每台相机追加文件名。
- `LensController.py`
  - `_wait_until_stopped()` 读取电机状态。
  - `move_relative_for_calibration()` 执行标定用相对移动并返回状态、内部角度、边界标记。
- `Device.py`
  - `snap_and_save(save_path)` 按文件后缀选择 BMP 或 JPEG。`.bmp` 保存 BMP，其他后缀保存 JPEG。

## 本次新增交互需求

除路径和边界处理外，焦距标定 command 还需要补充两个交互行为：

1. 单镜头端点输入阶段新增 `status` 命令。
   - 适用位置：`calibrate_single_lens()` 中每个端点的 `输入角度 / ok / home / cancel` 循环。
   - 新提示：`输入角度 / ok / home / status / cancel`。
   - 行为：只查看当前相机本轮端点缓存状态，不移动镜头、不写 JSON、不结束本轮标定。
   - 如果本轮尚未确认任何端点，输出默认参考 samples，例如 `[0.0, 2900.0]`。
   - 如果本轮已经确认 1 个端点，输出该端点值，便于用户标定第二个端点时回顾参考。

2. `reuse` 后即使全部相机都已标定，也不自动退出。
   - 用户选择 `reuse` 后，如果当前 JSON 中所有已连接相机都已经 `calibrated=true`，仍应进入完成后命令循环。
   - 完成后命令循环至少支持 `status`、`redo 相机name`、`back`。
   - 用户可以手动查看结果、重标部分相机，或明确输入 `back` 后退出。
   - 实现上建议按 state 是否全部完成统一进入完成后命令循环，不要只在 `reuse` 字符串分支里写特殊逻辑。

## 需求 1：旋转拍摄保存路径调整

### 当前行为

旋转拍摄循环中，`main.py` 当前按阵位和镜头 step 生成目录：

```text
Output/Position_{current}/Step_{step_idx + 1}
```

随后 `ImageNode.py` 的 `parallel_snap_all(save_dir_base)` 或 `serial_snap_all(save_dir_base)` 对每台相机追加：

```text
{camera.user_id}.bmp
```

因此实际代码当前更接近：

```text
Output/Position_1/Step_1/3.bmp
```

用户期望改为：

```text
Output/{camera.user_id}/Position{i}_Step{j}.bmp
```

示例：

```text
Output/3/Position1_Step1.bmp
```

注意：本需求确认图片后缀仍保持当前默认 `.bmp`。`Device.py` 虽支持通过其他后缀保存 JPEG，但本次计划不调整图片格式，只调整目录结构和文件命名。

### 设计目标

- 图片按相机聚合，方便同一相机的所有阵位和 step 连续查看。
- 文件名包含阵位和镜头 step，避免同一相机目录内重名。
- 不破坏旧的 `serial_snap_all(save_dir_base)` / `parallel_snap_all(save_dir_base)` 调用方式，降低影响面。
- 不再为图片创建旧的 `Output/Position_i/Step_j` 层级。

### 建议改动

1. 在 `ImageNode.py` 新增旋转拍摄专用保存方法，例如：

```python
def parallel_snap_rotation_step(self, output_root, position_index, step_index, extension=".bmp"):
    ...
```

每台相机保存路径为：

```python
save_dir = os.path.join(output_root, cam_name)
save_path = os.path.join(save_dir, f"Position{position_index}_Step{step_index}{extension}")
```

串行版本也需要同步新增：

```python
def serial_snap_rotation_step(...)
```

2. 在 `main.py` 旋转拍摄循环中，把：

```python
save_dir_base = os.path.abspath(f"./Output/Position_{current}/Step_{step_idx + 1}")
sys_dev.parallel_snap_all(save_dir_base)
```

替换为：

```python
output_root = os.path.abspath("./Output")
sys_dev.parallel_snap_rotation_step(
    output_root,
    position_index=current,
    step_index=step_idx + 1,
    extension=".bmp",
)
```

旧的串行和并行保存函数仍然保留。

3. metadata 不建议继续写入旧的 step 图片目录。建议改为对应 command 的运行日志目录：

```text
log/{command_name}/{run_timestamp}/Position{i}_Step{j}_metadata.json
```

当前 `--full_shot` 模式下，对外实际路径表现为：

```text
log/full_shot/{run_timestamp}/Position{i}_Step{j}_metadata.json
```

实现时不要在写文件逻辑中硬编码 `"full_shot"`，而应复用当前 command 对应的日志目录变量，例如 `FULL_SHOT_LOG_DIR`：

```python
run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
command_log_dir = FULL_SHOT_LOG_DIR
run_log_dir = os.path.join(command_log_dir, run_timestamp)
metadata_path = os.path.join(
    run_log_dir,
    f"Position{position_index}_Step{step_index}_metadata.json",
)
```

这样后续如果 command 名称或日志目录规则调整，只需要改对应 command 的日志目录常量或映射关系，不会遗漏 metadata 写入路径。

内容保持现有字段，并补充：

```json
{
  "image_path_pattern": "Output/{camera_user_id}/Position{i}_Step{j}.bmp"
}
```

4. 更新 `--full_shot --help` 中的输出说明：

```text
图片输出到 Output/{camera.user_id}/Position{i}_Step{j}.bmp
每个 step 的相机目标角度写入 log/{command_name}/{run_timestamp}/Position{i}_Step{j}_metadata.json
```

### 验证点

- 单次阵位、单个 step 后，相机 `3` 的图片路径为 `Output/3/Position1_Step1.bmp`。
- 多相机并发保存时，每台相机各自创建目录，不互相覆盖。
- 多阵位多 step 下文件名唯一。
- BMP 文件能被正常打开，且 `Device.py` 走 BMP 保存路径。
- metadata 不再创建旧的 `Output/Position_i/Step_j` 图片目录。

## 需求 2：焦距标定大角度触边误判分析与方案

### 日志现象

用户给出的关键日志：

```text
当前电机内部绝对角度: 1398.99° : 1508.170166015625°
[1] 端点 1 输入角度 / ok / home / cancel: -2000
      标定相对移动: -2000.00°
      原始发送报文: 01 64 01 00 00 04 44 FF FD 38 E4 00 00 00 63 F3
当前电机内部绝对角度: 0.00°
清理镜头 COM8: 上电找 0 并关闭串口
[1] 本轮标定失效: 预期角度与内部角度偏差过大: expected=-601.01°, actual=0.00°
```

从日志看，用户输入 `-2000` 时，程序的期望角度计算为：

```text
expected = current_internal_angle + relative_delta
         = 1398.99 + (-2000)
         = -601.01
```

但真实硬件不可能越过 0 点继续到负角度，因此实际内部角度停在：

```text
actual = 0.00
```

这更像是“到达 0 点物理边界后的合理钳位结果”，不是镜头内部坐标系异常。

### 当前代码为何会误判

`main.py` 的 `calibrate_single_lens()` 当前流程大致为：

1. 记录 `expected_target_angle = current_internal_angle + relative_delta`。
2. 调用 `lens.move_relative_for_calibration(relative_delta)`。
3. 读取 `measured_angle` 并更新 `current_internal_angle`。
4. 如果 `result["boundary"]` 为真，提示边界并继续。
5. 如果 `result["ok"]` 为假，判定失败。
6. 如果 `abs(measured_angle - expected_target_angle) > ANGLE_VERIFY_TOLERANCE`，判定失败。

`LensController.move_relative_for_calibration()` 只有在 `_wait_until_stopped()` 读到状态码 `0xF5` 或 `0x0B` 时，才会设置：

```python
"boundary": True
```

但用户日志里，程序没有输出已有的边界提示，而是进入了偏差校验。这说明该次硬件很可能在触达 0 点后返回了正常停稳状态 `0x00` 和角度 `0.00`，没有返回 `0xF5`。

因此根因不是日志理解错误，而是程序目前只信任硬件状态码识别边界；当硬件以 `status=0x00, angle=0.00` 表达“已经停在边界”时，代码没有结合运动方向、期望角度和实际角度做二次判断，导致把合理的边界钳位误判为异常偏差。

### 新增调试结论

新一组现场日志显示，远端边界与零点边界的状态表现并不对称：

- 正向继续移动到远端时，硬件会返回 `status=0x0B`，当前代码可以正确识别为远端边界，并继续留在本轮标定。
- 从远端附近输入较大的负相对角度回到零点附近时，硬件可能返回正常停稳状态，而不是零点边界状态码；例如 `expected=-9.25°`，`actual=0.01°`，程序未进入 `result["boundary"]` 分支，而是进入偏差校验后失败。
- 官方 `home` 指令能稳定回到 `0.01°` 或 `0.02°`，说明内部零点读数本身可信；问题不是状态码表一定错误，而是“相对负角度移动触达零点”这一场景未必触发硬件零点边界状态。

因此，零点边界不能只依赖 `LENS_BOUNDARY_ZERO` 状态码。需要增加纯软件边界推断：当本次移动方向为负、理论期望角度小于 0、实际内部角度已经落在零点容差内时，应视为已抵达零点边界，并把本次期望角度修正为硬件实测的零点附近角度，例如 `0.01°`，而不是继续保留负期望值参与偏差校验。

### 期望行为

当输入过大的相对角度导致镜头停在边界时：

- 不退出本轮标定。
- 打印明确提示，例如：

```text
已抵达零点边界，当前角度修正为 0.00°，请输入正的相对角度离开边界，或输入 ok 接受该端点。
```

- 更新当前角度为真实内部角度。
- 允许用户继续输入角度或 `ok` 缓存端点。

单镜头端点输入阶段还需要新增 `status` 命令：

- 当前提示从 `输入角度 / ok / home / cancel` 扩展为 `输入角度 / ok / home / status / cancel`。
- `status` 只查看当前相机本轮端点缓存状态，不移动镜头、不写 JSON、不结束本轮标定。
- 如果当前本轮还没有缓存任何端点，输出默认参考值，例如 `samples=[0.0, 2900.0]` 或当前默认范围，并提示“本轮尚未确认端点”。
- 如果当前本轮已经缓存 1 个端点，输出已缓存端点值，例如 `samples=[2980.76]`，并提示另一个端点尚未确认。这个场景主要用于用户回顾第一个端点作为第二个端点的调焦参考。
- 如果当前本轮已经缓存 2 个端点，输出两个端点值。正常流程中两个端点确认后会结束单镜头标定，因此该分支主要用于防御和调试。

`reuse` 进入标定 command 后也需要调整完成态行为：

- 用户选择 `reuse` 后，即使当前 JSON 中所有已连接相机都已经 `calibrated=true`，程序也不应直接退出。
- 应进入完成后命令循环，允许用户输入 `status` 查看结果、输入 `redo 相机name` 重标部分相机，或输入 `back` 手动退出。
- 这样 `reuse` 的语义是“沿用已有结果并进入可操作会话”，而不是“发现全部完成就自动结束”。

只有在以下情况才退出本轮标定并执行清理：

- 无法读取内部角度。
- 电机返回非边界类错误状态，且无法确认停稳。
- 内部角度出现异常负数，例如小于 `-1.0°`。
- 运动方向与实测变化明显矛盾，且不能解释为 0 点或远端边界。
- 多次读取不稳定，无法确认实际位置。

### 建议改动

1. 在 `main.py` 增加边界判定辅助函数，例如：

```python
def classify_calibration_move(
    start_angle,
    relative_delta,
    expected_angle,
    measured_angle,
    status_code,
    result,
):
    ...
```

返回结构建议包含：

```python
{
    "kind": "normal" | "zero_boundary" | "far_boundary" | "failure",
    "corrected_angle": measured_angle,
    "corrected_expected_angle": measured_angle,
    "message": "...",
}
```

2. 判定规则建议：

- 如果 `result["boundary"]` 为真，沿用硬件状态码判断。
- 如果 `relative_delta < 0`，`expected_angle < 0`，且 `measured_angle` 在 `ZERO_VERIFY_TOLERANCE` 内接近 0，即使硬件状态码不是零点边界，也判定为 `zero_boundary`。
- 如果 `relative_delta > 0` 且硬件返回远端边界状态 `0x0B`，判定为 `far_boundary`。
- 如果 `relative_delta > 0`、`measured_angle` 明显小于 `expected_angle`、但移动方向没有反向且最终稳定，可以先判定为“疑似远端边界”，提示用户确认或输入负角度离开；不应立即失败。
- 如果偏差超限但不满足任何边界条件，才返回 `failure`。

3. 调整 `calibrate_single_lens()` 中的校验顺序：

当前：

```python
if result.get("boundary"):
    continue
...
if deviation > ANGLE_VERIFY_TOLERANCE:
    return failed
```

建议改为：

```python
classification = classify_calibration_move(...)

if classification["kind"] in ("zero_boundary", "far_boundary"):
    current_internal_angle = classification["corrected_angle"]
    lens.current_angle = current_internal_angle
    expected_target_angle = classification["corrected_expected_angle"]
    print(classification["message"])
    log_event(...)
    continue

if classification["kind"] == "failure":
    return {"status": "failed", "reason": classification["message"]}
```

4. 在 `LensController.move_relative_for_calibration()` 中补充返回上下文，便于上层判断：

```python
{
    "ok": True,
    "status": status,
    "real_angle": real_angle,
    "boundary": boundary,
    "error": None,
    "requested_delta": delta_angle_deg,
}
```

如愿意进一步收敛职责，也可以让调用方传入 `start_angle`，由 `LensController` 返回 `expected_angle` 与 `clamped_boundary`。但更建议把“标定策略判断”保留在 `main.py`，让 `LensController.py` 只负责硬件移动和原始状态返回。

5. 对 0 点边界的修正逻辑：

```python
if relative_delta < 0 and expected_target_angle < 0 and measured_angle <= ZERO_VERIFY_TOLERANCE:
    corrected_angle = measured_angle
    current_internal_angle = corrected_angle
    lens.current_angle = corrected_angle
    expected_target_angle = corrected_angle
    print(f"已抵达零点边界，当前角度修正为 {corrected_angle:.2f}°...")
    continue
```

当前远端边界的代码行为是：`move_relative_for_calibration()` 在碰到边界时把 `self.current_angle` 更新为硬件返回的 `real_angle`；`calibrate_single_lens()` 随后也会先把 `measured_angle` 写入 `current_internal_angle` 和 `lens.current_angle`，再因为 `result["boundary"]` 为真而 `continue`，不再用原始 `expected_target_angle` 做偏差校验。零点软判定也应保持这个模式：硬件实测角度是当前真实角度，软件只修正本轮校验用的期望角度。

不要在这里无条件把真实角度写死成 `0.0`。例如硬件读数是 `0.01°` 时，`current_internal_angle` 和 `lens.current_angle` 都应保留 `0.01°`；只是把原本为负数的 `expected_target_angle` 修正为同一个 `0.01°`，从而和远端边界一样跳过不合理的偏差失败。

这类逻辑应覆盖两类现场日志：

```text
expected=-601.01°, actual=0.00°
expected=-9.25°, actual=0.01°
```

第二个例子尤其说明：即使已经非常接近零点，如果继续使用负的 `expected_target_angle` 做偏差校验，也会把一个合理的零点钳位结果误判成异常。

6. 对远端边界的修正逻辑：

- 优先依赖 `status == LENS_BOUNDARY_FAR`。
- 如果硬件未返回远端边界状态，但实测角度稳定、正向移动未达到期望值，可提示“疑似远端边界”，并允许用户：
  - 输入 `ok` 接受当前端点；
  - 输入负角度离开边界；
  - 输入 `home` 回 0。
- 不建议仅凭 `ANGLE_END=2900.0` 强行判断远端边界，因为标定本身就是为了得到真实远端范围；默认值只能作为提示参考，不能作为硬失败依据。

7. 在 `calibrate_single_lens()` 的端点输入循环中增加 `status` 分支：

```python
if lowered == "status":
    print_current_calibration_status(
        camera_name=camera_name,
        endpoint_idx=endpoint_idx,
        samples=samples,
        current_internal_angle=current_internal_angle,
        default_samples=[ANGLE_START, ANGLE_END],
    )
    continue
```

建议新增一个小函数负责格式化输出，避免把交互文本散落在端点循环中：

```python
def print_current_calibration_status(
    camera_name,
    endpoint_idx,
    samples,
    current_internal_angle,
    default_samples,
):
    ...
```

输出内容建议包含：

- 当前相机 name。
- 当前正在标定的端点序号。
- 当前内部绝对角度；如果还没读到，则提示未读取。
- 本轮已确认端点 samples。
- 如果本轮 samples 为空，显示默认参考 samples。

8. 调整 `run_focus_range_calibration()` 的 `reuse` 完成态逻辑：

- `choose_initial_lens_map()` 返回复用后的 state 后，顶层不应因为 `completed_camera_count(state, camera_infos) == len(camera_infos)` 而直接写日志并退出。
- 当全部完成时，应调用现有或新增的完成后命令循环，例如 `run_completion_loop(camera_infos, camera_infos_by_name, state, lens_registry, log_events)`。
- 完成后命令循环至少保留 `status`、`redo 相机name`、`back`。
- 只有用户输入 `back` 时，才保存必要日志、清理残留镜头并结束当前 `--focus_calibration` command。

实现上建议不要把这个行为只绑死在 `reuse` 字符串判断上，而是按 state 的完成情况统一处理：只要进入 command 后发现全部已完成，就进入完成后命令循环。这样无论是 `reuse` 读取到全完成，还是本次会话刚刚完成最后一台相机，行为都一致。

### 需要保留的失败保护

为了避免把真实异常误当成边界，以下保护应保留：

- `normalize_internal_angle()` 对小幅负数 `-1.0 <= angle < 0.0` 归零；对小于 `-1.0` 的负角度继续视为异常。
- `measured_angle is None` 时继续失败。
- `result["ok"]` 为假且状态码不是已知或可解释边界时继续失败。
- 如果 `relative_delta < 0`，但 `measured_angle` 远离 0 且与预期偏差很大，继续失败。
- 如果 `relative_delta > 0`，但 `measured_angle` 比起点更小，继续失败或要求人工确认。

### 验证场景

1. 从约 `1398.99°` 输入 `-2000`：
   - 程序读取 `0.00°`。
   - 判定为 0 点边界。
   - 提示已抵达零点边界。
   - 不退出本轮标定。

2. 从约 `180.75°` 输入 `-190`：
   - 理论期望值为负，例如 `expected=-9.25°`。
   - 程序读取 `0.01°`。
   - 即使状态码不是 `LENS_BOUNDARY_ZERO`，也按软件规则判定为 0 点边界。
   - 当前真实角度、`lens.current_angle` 和修正后的期望角度都同步为硬件实测值 `0.01°`，不退出本轮标定。

3. 在 0 点边界输入 `ok`：
   - 缓存端点为 `0.00°`。

4. 在 0 点边界输入正角度：
   - 镜头离开边界。
   - 当前内部角度更新为实测值。

5. 输入正常相对角度，例如 `+100`：
   - 实测角度与期望角度偏差小于 `ANGLE_VERIFY_TOLERANCE`。
   - 正常继续。

6. 输入过大正角度触达远端：
   - 若状态码为 `0x0B`，提示远端边界，不退出。
   - 若状态码为 `0x00` 但实测角度稳定且明显小于期望，提示疑似远端边界，不直接失败。

7. 模拟内部角度返回 `-20.0°`：
   - 继续判定为内部坐标异常，退出本轮标定。

8. 模拟读取不到角度：
   - 继续判定失败，执行镜头清理。

9. 单镜头端点输入阶段输入 `status`，且本轮尚未确认端点：
   - 输出当前相机、当前端点序号、当前内部角度。
   - 输出默认参考 samples，例如 `[0.0, 2900.0]`。
   - 不移动镜头，不写 JSON，不退出本轮标定。

10. 单镜头端点输入阶段已确认 1 个端点后输入 `status`：
    - 输出已确认的 1 个端点值。
    - 提示另一个端点尚未确认。
    - 允许用户继续输入角度、`ok`、`home` 或 `cancel`。

11. 启动 `--focus_calibration` 后选择 `reuse`，且所有已连接相机都已完成标定：
    - 不自动退出。
    - 进入完成后命令循环。
    - 用户可输入 `status` 查看全部结果，输入 `redo 相机name` 重标指定相机，或输入 `back` 退出。

## 实施顺序

1. 先改旋转拍摄保存路径：
   - 新增旋转拍摄专用保存方法。
   - 调整 `main.py` 调用与 metadata 路径。
   - 更新帮助文本。

2. 再改焦距标定边界处理：
   - 增加移动结果分类函数。
   - 修改 `calibrate_single_lens()` 的偏差校验逻辑。
   - 增加单镜头端点输入阶段的 `status` 命令，输出当前本轮 samples 或默认参考 samples。
   - 调整 `reuse` 后全部已完成时的顶层流程，进入完成后命令循环而不是自动退出。
   - 保留真实异常失败路径。

3. 最后验证：
   - 对路径生成逻辑可用无硬件的单元级测试或 dry-run helper 验证。
   - 焦距标定建议增加可注入的模拟 `LensController`，覆盖 0 点边界、远端边界、正常移动、异常负角度和读数失败。
