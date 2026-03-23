## Context

当前 `run_rotation_sequence` 使用 PLC 模式0（间隔运行+延时），即配置好角度和速度后启动，PLC 自动按固定延时循环转动。这种方式无法在每次转动到位后精确触发外部操作（如拍照），只能依赖固定延时等待。

Go 版本 `execSphereTask` 已实现完整的模式1（间隔运行+触发）控制流程，Python 版本需要对齐实现。现有代码结构清晰（`RotationController` + `ModbusClient`），改造成本可控。

**约束条件**:
- pymodbus 3.0.0+，使用 `device_id` 而非 `unit`
- Modbus 客户端是同步的，引入后台线程需要考虑线程安全
- 现有 `ModbusClient` 已封装了 `read_coil`、`write_coil`、`read_register`、`write_register` 等方法

## Goals / Non-Goals

**Goals:**
- 将 `run_rotation_sequence` 从模式0重构为模式1
- 实现位置检测复位、手动模式启动、异步错误监控、触发-等待-回调循环
- 保持 `RotationController` 类的封装性，新增方法而非破坏现有结构
- 支持可选的错误信号屏蔽和异常处理模式配置

**Non-Goals:**
- 不改变 `ModbusClient` 的底层实现（已有足够接口）
- 不实现拍照逻辑本身（通过回调函数由调用方提供）
- 不支持模式0和模式1的动态切换（BREAKING 变更，统一为模式1）

## Decisions

### Decision 1: 使用模式1（间隔运行+触发）替代模式0

**选择**: 模式1  
**理由**: 模式0依赖固定延时，无法精确控制拍照时机。模式1允许在每次机械轴转动到位后通过 `coilReady` 信号触发外部操作，精度更高。Go 版本已验证此方案可行。  
**替代方案**: 保留模式0 + 增加延时估算 → 拒绝，精度不够且与 Go 版本不一致。

### Decision 2: 异步错误检测使用 `threading.Thread`

**选择**: `threading.Thread` + `threading.Event` 控制停止  
**理由**: 现有代码是同步的，引入 asyncio 会大幅改变架构。threading 是最轻量的方案，Go 版本的 goroutine 也是类似思路。  
**替代方案**: asyncio → 拒绝，改造成本过高；轮询检测 → 拒绝，无法及时响应错误。

### Decision 3: 线程安全策略

**选择**: 错误检测线程独立使用 Modbus 连接，或通过锁保护共享连接  
**理由**: pymodbus 的 `ModbusTcpClient` 不是线程安全的，多线程并发读写可能导致数据混乱。  
**方案**: 
- 方案A: 错误检测线程创建独立的 Modbus 连接（推荐，简单可靠）
- 方案B: 使用 `threading.Lock` 保护共享连接（复杂度较高）

**选择方案A**，因为 PLC 支持多连接，且错误检测频率低（1秒一次），额外连接开销可忽略。

### Decision 4: 函数签名变更

**选择**: `run_rotation_sequence` 新增 `error_signal` 和 `error_continue_model` 可选参数  
**理由**: 
- `error_signal`: 控制是否屏蔽红外感应信号（Go 版本的 `coilNoError`）
- `error_continue_model`: 异常处理模式（0=继续，1=复位，2=默认）

保持 `rotations`、`speed`、`delay`、`progress_callback` 参数不变，降低迁移成本。

### Decision 5: 启动前位置检测与复位

**选择**: 读取 `registerPosition`（D324），若不在起始位置则执行模式切换复位  
**理由**: Go 版本的逻辑是：读取当前位置 → 若不在 0° 附近 → 切换自动模式 → 切换手动模式 → 等待回到起始位置。这确保每次任务从一致的状态开始。  
**实现**: 新增 `reset_to_home` 方法封装此逻辑。

## Risks / Trade-offs

**[Risk] 线程异常导致资源泄漏** → 使用 `try-finally` 和 `threading.Event` 确保线程正确退出，`check_error` 线程在 `run_rotation_sequence` 结束时通过 `Event.set()` 停止。

**[Risk] 模式切换时序问题** → Go 版本在模式切换后有 `time.Sleep(1 * time.Second)`，Python 版本需要保持相同延时。若 PLC 响应慢，可能需要增加重试。

**[Risk] 后台线程的 Modbus 连接断开** → 错误检测线程独立管理连接生命周期，在线程退出时自动断开。

**[Trade-off] BREAKING 变更** → 模式1的控制流程与模式0完全不同，原有调用方式不再适用。需要更新 `main.py` 的调用逻辑和参数。

## Migration Plan

1. 新增寄存器/线圈常量定义
2. 新增 `reset_to_home`、`wait_for_ready`、`check_error` 方法
3. 重写 `run_rotation_sequence`，实现模式1控制流程
4. 更新 `main.py`，适配新的参数和调用方式
5. 测试验证：连接 PLC → 执行旋转序列 → 验证触发模式和错误检测

**回滚策略**: 保留 Git 历史，若出现问题可 revert 到模式0版本。

## Open Questions

- 错误检测线程的 Modbus 连接参数是否与主连接一致？（建议：复用 `self.client` 的 host/port 配置）
- `delay` 参数在模式1下是否还有意义？（Go 版本中 `intervalMs` 用于拍照间隔，模式1下机械轴由触发信号控制，delay 可能仅用于拍照后的等待）