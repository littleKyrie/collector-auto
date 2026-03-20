## 1. 环境准备

- [x] 1.1 安装pymodbus依赖
- [x] 1.2 创建requirements.txt文件

## 2. Modbus通讯模块

- [x] 2.1 创建modbus_client.py文件
- [x] 2.2 实现ModbusClient类，封装连接功能
- [x] 2.3 实现connect()方法连接PLC (192.168.1.88:502)
- [x] 2.4 实现disconnect()方法断开连接
- [x] 2.5 实现read_coil()方法读取线圈状态
- [x] 2.6 实现write_coil()方法写入线圈
- [x] 2.7 实现read_register()方法读取寄存器
- [x] 2.8 实现write_register()方法写入寄存器
- [x] 2.9 实现write_registers()方法批量写入寄存器
- [x] 2.10 添加连接超时和重试机制
- [x] 2.11 添加错误处理和日志记录

## 3. 旋转控制模块

- [x] 3.1 创建rotation_controller.py文件
- [x] 3.2 实现RotationController类
- [x] 3.3 实现validate_parameters()方法验证参数有效性
  - 检查旋转次数为正整数
  - 检查速度在0-30°/s范围内
  - 检查延时为非负数
  - 检查单次角度不超过327.67°
- [x] 3.4 实现calculate_angle_per_step()方法计算每次旋转角度
- [x] 3.5 实现configure_parameters()方法配置PLC参数
  - 写入D300 (角度×100)
  - 写入D304 (速度×100)
  - 写入D316 (延时秒数)
  - 写入D320 (模式0)
- [x] 3.6 实现confirm_parameters()方法触发S400确认参数
- [x] 3.7 实现start_rotation()方法触发S401启动运行
- [x] 3.8 实现wait_for_move_done()方法监控S403上升沿
- [x] 3.9 实现run_rotation_sequence()方法执行完整旋转序列
- [x] 3.10 实现stop_rotation()方法停止运行
- [x] 3.11 添加进度回调函数支持

## 4. 命令行入口

- [x] 4.1 创建main.py文件
- [x] 4.2 使用argparse解析命令行参数
  - --rotations: 旋转次数 (默认12)
  - --speed: 旋转速度 (默认10°/s)
  - --delay: 停顿时间 (默认3秒)
  - --host: PLC地址 (默认192.168.1.88)
  - --port: PLC端口 (默认502)
- [x] 4.3 实现参数验证和错误提示
- [x] 4.4 实现进度显示回调函数
- [x] 4.5 实现主函数main()整合所有功能
- [x] 4.6 添加--help帮助信息
- [x] 4.7 添加异常处理和用户友好的错误消息

## 5. 测试和验证

- [ ] 5.1 测试Modbus连接功能
- [x] 5.2 测试参数验证逻辑
- [ ] 5.3 测试单次旋转功能
- [ ] 5.4 测试完整旋转序列
- [x] 5.5 测试错误处理（连接失败、参数错误等）
- [ ] 5.6 验证进度显示功能
- [x] 5.7 验证命令行参数解析

## 6. 文档和清理

- [x] 6.1 创建README.md使用说明
- [ ] 6.2 添加代码注释和文档字符串
- [x] 6.3 清理临时文件（read_excel.py等）
- [ ] 6.4 最终代码审查
