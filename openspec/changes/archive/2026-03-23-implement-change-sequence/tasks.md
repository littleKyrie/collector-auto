## 1. 新增寄存器/线圈常量

- [x] 1.1 在 rotation_controller.py 中新增线圈地址常量：coilResume (57746), coilReady (57747), coilMode (57348), coilNoError (57649), coilErrorOccur (57356), coilRecover (57496), coilMachineStart (57494)
- [x] 1.2 新增寄存器地址常量：registerPosition (324), registerContinue (332)
- [x] 1.3 新增线圈值常量：coilValueFalse (0x0000), coilValueTrue (0xFF00), coilResultFalse (0), coilResultTrue (1)
- [x] 1.4 新增错误处理模式常量：errHandleModeContinue (0), errHandleModeInit (1), errHandleModeDefault (2)

## 2. 新增辅助方法

- [x] 2.1 实现 `reset_to_home` 方法：读取当前位置，若不在起始位置则执行模式切换复位
- [x] 2.2 实现 `wait_for_ready` 方法：轮询 coilReady 信号等待机械轴就绪
- [x] 2.3 实现 `check_error` 方法：后台线程持续监控 coilErrorOccur，检测到错误时执行异常处理
- [x] 2.4 实现 `start_machine` 方法：手动模式下启动设备（coilMachineStart）

## 3. 重写 run_rotation_sequence

- [x] 3.1 修改函数签名，新增 error_signal 和 error_continue_model 可选参数
- [x] 3.2 实现红外感应信号屏蔽控制（coilNoError）
- [x] 3.3 实现模式1配置（D320=1）和参数写入（D300, D304）
- [x] 3.4 实现参数确认（coilEnsure）
- [x] 3.5 实现位置检测与复位调用（reset_to_home）
- [x] 3.6 实现手动模式启动调用（start_machine）
- [x] 3.7. 启动异步错误检测线程（check_error）
- [x] 3.8 实现触发-等待-回调循环：发送 coilResume → 等待 coilReady → 调用 action_callback
- [x] 3.9 实现线程清理：通过 threading.Event 停止错误检测线程

## 4. 更新 main.py

- [x] 4.1 新增 --error-signal 命令行参数（默认 True）
- [x] 4.2 新增 --error-continue-model 命令行参数（默认 2）
- [x] 4.3 更新 run_rotation_sequence 调用，传入新参数
- [x] 4.4 更新帮助文档和示例说明

## 5. 测试验证（需要实际连接PLC）

- [ ] 5.1 验证参数配置正确写入 PLC 寄存器（需要实际连接PLC）
- [ ] 5.2 验证模式1触发-等待-回调循环正常工作（需要实际连接PLC）
- [ ] 5.3 验证位置检测与复位逻辑（需要实际连接PLC）
- [ ] 5.4 验证异步错误检测线程正常启动和停止（需要实际连接PLC）
- [ ] 5.5 验证异常处理模式（继续/复位）（需要实际连接PLC）
