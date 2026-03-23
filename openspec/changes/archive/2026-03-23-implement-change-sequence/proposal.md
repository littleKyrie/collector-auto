## Why

当前 `run_rotation_sequence` 函数使用模式0（间隔运行+延时），这是一个简化实现。生产环境中需要使用模式1（间隔运行+触发），以便在每次机械轴转动到位后精确触发拍照，而非依赖固定延时。参考已有的 Go 实现 `execSphereTask`，需要将 Python 版本升级为完整的触发模式控制流程，包括：位置检测与复位、手动模式启动、异步错误监控、触发-等待就绪-回调的循环控制。

## What Changes

- **BREAKING** 将 `run_rotation_sequence` 从模式0（间隔运行+延时）改为模式1（间隔运行+触发）
- 新增红外感应信号屏蔽控制（coilNoError S57649）
- 新增机械轴当前位置检测（registerPosition D324），启动前判断是否需要复位到起始位置
- 新增手动模式下的启动流程（coilMachineStart S57494、coilMode S57348 模式切换）
- 新增异步错误检测机制（coilErrorOccur S57356），使用后台线程持续监控 PLC 错误状态
- 新增异常复位处理（coilRecover S57496、registerContinue D332）
- 循环控制改为：发送继续信号（coilResume S57746）→ 等待机械轴就绪（coilReady S57747）→ 执行回调（如拍照）
- 新增更多寄存器/线圈地址常量定义

## Capabilities

### New Capabilities
- `change-sequence`: 重构 `run_rotation_sequence` 为触发模式控制流程，涵盖位置检测复位、手动模式启动、异步错误监控、触发-等待-回调循环、异常复位等完整控制逻辑

### Modified Capabilities
- `rotation-control`: 运行模式从模式0变更为模式1，控制流程发生根本性变化；新增多个寄存器/线圈地址；`run_rotation_sequence` 函数签名和行为变更

## Impact

- **rotation_controller.py**: `run_rotation_sequence` 函数重写；新增 `wait_for_ready`、`check_error`、`reset_to_home` 等方法；新增寄存器/线圈常量
- **modbus_client.py**: 可能需要新增 `read_register` 的便捷方法（当前已有）
- **main.py**: 调用方式可能需要适配新的参数（如 error_signal 配置、error_continue_model 配置）
- **线程安全**: 引入后台错误检测线程，需要考虑 Modbus 客户端的线程安全性
- **向后兼容**: 模式变更为 **BREAKING**，原有模式0的调用方式不再适用