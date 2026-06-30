# 执行流程优化计划

## 目标

在保留当前业务流程约束的前提下，缩短启动阶段镜头初始化，以及单个阵位中镜头移动、拍照和保存的耗时，并将图片保存改为无压缩 BMP。

本轮明确不优化 PLC 等待逻辑：转台触发后的等待仍保留当前实现，即 `time.sleep((self.angle_per_step / self.speed) + 2)`。这里的 `+ 2` 秒继续作为默认稳定等待余量。

必须保留的行为：

- 每个转台阵位拍摄完成后，镜头必须回到 0。
- 转台等待逻辑保持现状，不启用 `wait_for_ready()`。
- 原有串行函数保留。
- 原有串行调用位置保留代码但注释掉，旁边启用并行调用，方便后续手动切换验证。
- 图片保存为 BMP，不使用 JPEG 压缩。

## 当前主要耗时点

1. 镜头移动是串行执行。
   - 当前入口：`main.py` 的 `progress_callback()` 调用 `sys_dev.serial_move_lenses(target_angle)`。
   - 实现位置：`ImageNode.py` 的 `serial_move_lenses()`。
   - 影响：多镜头时，每个镜头移动耗时相加。

2. 镜头全局初始化是串行执行。
   - 当前入口：`main.py` 初始化相机系统后调用 `sys_dev.global_homing()`。
   - 实现位置：`ImageNode.py` 的 `global_homing()`，内部逐个调用 `node.lens.initialize_lens()`。
   - 影响：`initialize_lens()` 内包含上电找 0、回 0 和固定等待，多镜头时初始化耗时相加。

3. 相机拍照保存是串行执行。
   - 当前入口：`main.py` 的 `progress_callback()` 调用 `sys_dev.serial_snap_all(save_dir_base)`。
   - 实现位置：`ImageNode.py` 的 `serial_snap_all()`。
   - 影响：多相机时，每台相机的触发、取帧、格式转换、写盘耗时相加。

4. PLC 连接重复。
   - 当前位置：`main.py` 中第一次在初始化后连接 PLC，后面进入 `try` 后再次 `client.connect()`。
   - 影响：多一次无必要网络连接，增加启动时间，并可能覆盖底层 client 状态。

5. 图片保存使用 JPG。
   - 当前位置：`ImageNode.py` 的 `serial_snap_all()` 生成 `{cam_name}.jpg`。
   - 当前压缩位置：`Device.py` 的 `snap_and_save()` 根据后缀选择 JPEG 或 BMP。
   - 目标：文件名改为 `.bmp`，让 `snap_and_save()` 走 `typeImageBmp`，避免 JPEG 压缩质量损失。

6. 转台等待使用固定估算时间。
   - 当前位置：`rotation_controller.py` 的 `run_rotation_sequence()`。
   - 当前逻辑：`time.sleep((self.angle_per_step / self.speed) + 2)`。
   - 本轮处理：保持不变，不切换到 PLC 到位信号。

## 优化方案

### 1. 镜头移动改为并发，保留串行调用注释

改动入口：

- `main.py`

计划改动：

每个步进点的镜头移动，将串行调用注释掉，启用并行调用：

```python
# sys_dev.serial_move_lenses(target_angle)
sys_dev.parallel_move_lenses(target_angle)
```

每个阵位结束后的镜头回 0，也保留串行调用但注释掉：

```python
# sys_dev.serial_move_lenses(0.0)
sys_dev.parallel_move_lenses(0.0)
```

保留内容：

- `ImageNode.py` 中的 `serial_move_lenses()` 函数不删除。
- `ImageNode.py` 中已有的 `parallel_move_lenses()` 继续使用。
- `main.py` 中保留串行调用代码，只通过注释切换。

原因：

- 回 0 行为仍然保留，只是多个镜头同时回 0。
- 多镜头独立串口时，并发移动可以把耗时从“所有镜头耗时相加”降低为“最慢镜头耗时”。

注意事项：

- 如果多个镜头实际共享同一个串口或底层控制器，不能直接并发，需要切回串行调用验证。
- 后续手动切换时，只需要注释或取消注释 `serial_move_lenses()` 与 `parallel_move_lenses()` 两行。

### 2. 镜头全局初始化改为并发，保留串行实现和串行调用注释

改动位置：

- `ImageNode.py`
- `main.py`

计划改动：

- 将现有 `global_homing()` 直接重命名为 `serial_global_homing()`，作为唯一串行初始化入口。
- 新增 `parallel_global_homing()`，并发调用每个镜头的 `node.lens.initialize_lens()`。
- 不再保留 `global_homing()` 兼容别名，避免两个名字不同但逻辑完全相同的串行实现造成歧义。

建议实现形态：

```python
def serial_global_homing(self):
    print("\n⚙️  ================ 执行系统全局绝对归零 ================")
    for node in self.nodes:
        if node.lens:
            node.lens.initialize_lens()
    print("✅  所有镜头全局归零完毕！绝对物理坐标系已确立。")

def parallel_global_homing(self):
    print("\n⚙️  ================ 并发执行系统全局绝对归零 ================")
    nodes_with_lens = [node for node in self.nodes if node.lens]
    if not nodes_with_lens:
        print(" ⚠️ 没有可初始化的镜头。")
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes_with_lens)) as executor:
        futures = [executor.submit(node.lens.initialize_lens) for node in nodes_with_lens]
        concurrent.futures.wait(futures)

    print("✅  所有镜头全局归零完毕！绝对物理坐标系已确立。")
```

`main.py` 初始化调用处保留串行调用注释，启用并行调用：

```python
# sys_dev.serial_global_homing()
sys_dev.parallel_global_homing()
```

原因：

- `initialize_lens()` 是按镜头对象执行的初始化动作，当前每个 `LensController` 绑定一个串口。
- 串行入口只保留 `serial_global_homing()`，并行入口只保留 `parallel_global_homing()`，接口含义更直接。
- 若每个镜头确实使用独立串口，并发初始化可以把启动阶段耗时从“所有镜头初始化耗时相加”降低为“最慢镜头初始化耗时”。

注意事项：

- 只有在镜头串口彼此独立时才建议并发初始化。
- `initialize_lens()` 内包含固定等待和状态轮询，并发执行时日志会交错，必要时可在日志中保留串口号用于区分。
- 如果上电找 0 或回 0 会影响共享电源、共享控制器或机械结构，必须切回串行初始化。

### 3. 相机拍照和保存改为并发，保留串行函数和串行调用注释

改动位置：

- `ImageNode.py`
- `main.py`

计划改动：

- 在 `ImageNode.py` 新增 `parallel_snap_all(save_dir_base)`。
- 原有 `serial_snap_all(save_dir_base)` 不删除。
- 主流程中保留串行调用但注释掉，启用并行调用：

```python
# sys_dev.serial_snap_all(save_dir_base)
sys_dev.parallel_snap_all(save_dir_base)
```

建议实现形态：

```python
def parallel_snap_all(self, save_dir_base):
    os.makedirs(save_dir_base, exist_ok=True)
    print("正在并发触发拍摄并写入磁盘...")

    nodes = list(self.nodes)
    if not nodes:
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes)) as executor:
        futures = []
        for node in nodes:
            cam_name = node.camera.m_userId
            save_path = os.path.join(save_dir_base, f"{cam_name}.bmp")
            future = executor.submit(node.camera.snap_and_save, save_path)
            futures.append((cam_name, save_path, future))

        for cam_name, save_path, future in futures:
            ret = future.result()
            if ret == IMV_OK:
                print(f"[{cam_name}] 保存成功 -> {save_path}")
            else:
                print(f"[{cam_name}] 保存失败，错误码: {ret}")
```

原因：

- 多相机通常互相独立，触发、取帧、SDK 转换、磁盘写入可以重叠。
- BMP 文件较大，并发写盘可能让磁盘成为新的瓶颈，所以必须现场验证。

风险控制：

- 先用少量相机验证并发稳定性。
- 如果 SDK 多线程保存不稳定，保留的 `serial_snap_all()` 可以直接切回。
- 如果 BMP 并发写盘太慢，可保留并行函数但限制 `max_workers`，例如 `max_workers=2`。

### 4. PLC 等待逻辑保持现状

改动位置：

- `rotation_controller.py`

计划改动：

- 不修改 `run_rotation_sequence()` 中的固定等待：

```python
time.sleep((self.angle_per_step / self.speed) + 2)
```

- 不启用当前注释掉的 `wait_for_ready()` 调用。
- 不在本轮修正 `wait_for_ready()` 的地址或极性问题。

原因：

- 新需求明确保留当前 PLC 等待逻辑。
- 当前固定等待虽然不够精确，但行为稳定，适合作为并行镜头和并行拍照优化时的控制变量。

后续可选项：

- 等本轮并行逻辑稳定后，再单独评估 `coilReady` 地址和 True/False 极性。
- 未来如果要重新启用 PLC 到位信号，需先现场确认 `coilReady` 的真实地址和状态语义。

### 5. 去掉重复 PLC 连接，保留必要连接

改动位置：

- `main.py`

计划改动：

保留第一次连接：

```python
client = ModbusClient(host=args.host, port=args.port)
print("\n[2/3] 正在连接PLC转台...")
if not client.connect():
    sys_dev.close_all()
    return 1
```

删除或注释掉后面 `try` 块里重复的连接逻辑：

```python
# print("\n正在连接PLC...")
# if not client.connect():
#     print("错误: 无法连接到PLC，请检查网络连接和PLC状态")
#     sys_dev.close_all()
#     return 1
# print("PLC连接成功!")
```

建议：

- 这里可以直接注释重复连接代码，不必删除，方便回看原流程。
- `RotationController(client)` 使用已经连接的同一个 `client`。

原因：

- 避免重复连接覆盖底层 client 状态。
- 启动流程更清晰。

### 6. 使用 BMP 无压缩保存

改动位置：

- `ImageNode.py`
- 可选：`Device.py`

计划改动：

在串行和并行保存路径中都使用 `.bmp`：

```python
save_path = os.path.join(save_dir_base, f"{cam_name}.bmp")
```

保留串行函数时也建议把 `serial_snap_all()` 内的 `.jpg` 改为 `.bmp`，这样无论手动切回串行还是使用并行，图片格式都一致。

原因：

- `Device.py` 的 `snap_and_save()` 已经按后缀判断：
  - `.bmp` -> `IMV_ESaveType.typeImageBmp`
  - 其他 -> `IMV_ESaveType.typeImageJpeg`
- 改文件后缀即可让 SDK 保存 BMP。

建议同步改进：

- 在 `Device.py` 中把 `saveImageParam.nBayerDemosaic = 2` 改为 `saveImageParam.eBayerDemosaic = 2`，因为 SDK 结构字段名是 `eBayerDemosaic`。
- BMP 不使用 `nQuality`，可保留但注明仅 JPEG 生效。

注意：

- BMP 无压缩会显著增大文件体积。
- 并发保存 BMP 时，磁盘写入可能成为新的主要耗时点。

## 推荐实施顺序

1. 注释掉 `main.py` 中重复的第二次 PLC 连接。
2. 将 `serial_snap_all()` 的保存后缀从 `.jpg` 改为 `.bmp`。
3. 在 `ImageNode.py` 新增 `parallel_snap_all()`，并同样使用 `.bmp`。
4. 在 `ImageNode.py` 将 `global_homing()` 重命名为 `serial_global_homing()`，并新增 `parallel_global_homing()`。
5. 在 `main.py` 中注释串行初始化调用，启用 `parallel_global_homing()`。
6. 在 `main.py` 中注释串行镜头移动调用，启用 `parallel_move_lenses()`。
7. 在 `main.py` 中注释串行拍照保存调用，启用 `parallel_snap_all()`。
8. 阵位结束回 0 也改为并行调用，但保留串行调用注释。
9. 保持 `rotation_controller.py` 的 PLC 固定等待逻辑不变。
10. 现场跑小规模流程，验证并行稳定性和 BMP 写盘性能。

## 验证计划

### 静态验证

```powershell
python -m py_compile main.py ImageNode.py Device.py rotation_controller.py
```

检查点：

- 没有语法错误。
- `main.py` 中初始化镜头的串行调用仍保留但被注释。
- `main.py` 中启用了 `parallel_global_homing()`。
- `main.py` 中串行调用仍保留但被注释。
- `main.py` 中启用了 `parallel_move_lenses()` 和 `parallel_snap_all()`。
- `rotation_controller.py` 中仍保留 `time.sleep((self.angle_per_step / self.speed) + 2)`。
- 输出路径后缀为 `.bmp`。

### 小流程硬件验证

建议命令：

```powershell
python main.py --rotations 2 --lens-steps 2 --delay 0 --verbose
```

观察点：

- PLC 只连接一次。
- 启动阶段所有镜头并发执行上电找 0 和回 0 初始化。
- 转台触发后仍按当前逻辑等待 `角度/速度 + 2 秒`。
- 每个步进点所有镜头并发移动。
- 每个步进点所有相机并发拍照并保存 BMP。
- 每个阵位完成后所有镜头仍回 0。
- 输出目录中图片后缀为 `.bmp`，文件可以正常打开。

### 串行/并行切换验证

镜头初始化切换：

```python
# 串行
sys_dev.serial_global_homing()
# sys_dev.parallel_global_homing()

# 并行
# sys_dev.serial_global_homing()
sys_dev.parallel_global_homing()
```

镜头移动切换：

```python
# 串行
sys_dev.serial_move_lenses(target_angle)
# sys_dev.parallel_move_lenses(target_angle)

# 并行
# sys_dev.serial_move_lenses(target_angle)
sys_dev.parallel_move_lenses(target_angle)
```

拍照保存切换：

```python
# 串行
sys_dev.serial_snap_all(save_dir_base)
# sys_dev.parallel_snap_all(save_dir_base)

# 并行
# sys_dev.serial_snap_all(save_dir_base)
sys_dev.parallel_snap_all(save_dir_base)
```

阵位结束回 0 切换：

```python
# 串行
sys_dev.serial_move_lenses(0.0)
# sys_dev.parallel_move_lenses(0.0)

# 并行
# sys_dev.serial_move_lenses(0.0)
sys_dev.parallel_move_lenses(0.0)
```

## 性能对比指标

记录优化前后：

- 启动阶段镜头全局初始化总耗时。
- 单个步进点镜头移动总耗时。
- 单个步进点多相机拍照保存总耗时。
- 单个阵位总耗时。
- 完整任务总耗时。
- BMP 文件平均大小。
- BMP 并发写盘耗时。
- 转台等待耗时保持现状，用作对比时的固定项。

## 风险与回退

1. 相机 SDK 多线程保存不稳定。
   - 回退：保留 `parallel_snap_all()`，但在 `main.py` 中切回 `serial_snap_all()`。
   - 折中：限制 `parallel_snap_all()` 的 `max_workers`。

2. BMP 文件过大导致磁盘写入变慢。
   - 回退：仍保存 BMP，但降低并发保存数量，例如 `max_workers=2`。
   - 备选：更换更快磁盘或分盘写入。

3. 镜头并发移动与硬件串口拓扑冲突。
   - 回退：在 `main.py` 中取消注释 `serial_move_lenses()`，注释 `parallel_move_lenses()`。

4. 镜头并发初始化与硬件串口、电源或机械拓扑冲突。
   - 回退：在 `main.py` 中取消注释 `serial_global_homing()`，注释 `parallel_global_homing()`。
   - 排查：确认每个镜头是否独立串口、独立控制链路，观察上电找 0 和回 0 是否会互相影响。

5. 去掉重复 PLC 连接后出现连接状态异常。
   - 回退：恢复第二次连接代码。
   - 更推荐的排查方式：检查第一次 `client.connect()` 后 `client.is_connected()` 状态。

## 预期收益

- 启动阶段镜头全局初始化耗时从“所有镜头初始化耗时相加”降低为“最慢镜头初始化耗时”。
- 镜头移动耗时从“所有镜头耗时相加”降低为“最慢镜头耗时”。
- 多相机拍照保存耗时从“所有相机耗时相加”降低为“最慢相机加磁盘写入竞争”。
- PLC 等待行为保持稳定，不引入到位信号地址或极性风险。
- PLC 连接流程更清晰，减少重复连接带来的不确定性。
- 图片质量满足 BMP 无压缩保存要求。
