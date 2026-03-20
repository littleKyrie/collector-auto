## ADDED Requirements

### Requirement: 360度旋转控制
系统 SHALL 提供360度旋转控制功能，支持从0度开始，按指定次数完成完整360度旋转。

#### Scenario: 配置旋转参数
- **WHEN** 用户指定旋转次数（如12次）和速度（如10°/s）
- **THEN** 系统计算每次旋转角度（360°/12=30°）并配置PLC参数

#### Scenario: 执行旋转
- **WHEN** 用户启动旋转任务
- **THEN** 系统按配置的次数自动旋转，每次旋转后停顿指定时间

#### Scenario: 完成旋转
- **WHEN** 所有旋转次数完成
- **THEN** 系统停止并返回完成状态

### Requirement: Modbus-TCP通讯
系统 SHALL 使用Modbus-TCP协议与PLC通讯，地址192.168.1.88，端口502。

#### Scenario: 建立连接
- **WHEN** 系统启动
- **THEN** 建立与PLC的Modbus-TCP连接

#### Scenario: 连接失败
- **WHEN** 连接失败或超时
- **THEN** 系统报错并退出

### Requirement: 参数配置
系统 SHALL 支持配置以下参数：
- 旋转次数（正整数）
- 旋转速度（0-30°/s）
- 停顿时间（秒）

#### Scenario: 有效参数
- **WHEN** 用户提供有效参数（次数=12，速度=10，延时=3）
- **THEN** 系统接受并使用这些参数

#### Scenario: 无效参数
- **WHEN** 用户提供无效参数（速度=50°/s超出范围）
- **THEN** 系统拒绝并提示错误

### Requirement: 自动模式控制
系统 SHALL 使用PLC自动模式0（间隔运行+延时）实现旋转控制。

#### Scenario: 配置模式0
- **WHEN** 系统配置旋转参数
- **THEN** 设置D320=0（模式0）

#### Scenario: 参数写入
- **WHEN** 系统写入参数
- **THEN** 写入D300（角度×100）、D304（速度×100）、D316（延时秒数）

#### Scenario: 参数生效
- **WHEN** 参数写入完成
- **THEN** 触发S400使参数生效

### Requirement: 运行控制
系统 SHALL 提供启动、停止、状态监控功能。

#### Scenario: 启动运行
- **WHEN** 用户启动旋转
- **THEN** 触发S401开始自动运行

#### Scenario: 监控进度
- **WHEN** 旋转进行中
- **THEN** 监控S403（Move_Done）检测每次移动完成

#### Scenario: 停止运行
- **WHEN** 用户停止或任务完成
- **THEN** 系统停止旋转

### Requirement: 命令行接口
系统 SHALL 提供命令行接口，支持参数化配置。

#### Scenario: 显示帮助
- **WHEN** 用户运行 `python main.py --help`
- **THEN** 显示参数说明和使用示例

#### Scenario: 执行旋转
- **WHEN** 用户运行 `python main.py --rotations 12 --speed 10 --delay 3`
- **THEN** 系统执行12次旋转，每次30°，速度10°/s，停顿3秒

### Requirement: 进度显示
系统 SHALL 实时显示旋转进度。

#### Scenario: 显示当前进度
- **WHEN** 旋转进行中
- **THEN** 显示"完成 1/12"、"完成 2/12"等进度信息

#### Scenario: 显示完成
- **WHEN** 所有旋转完成
- **THEN** 显示"旋转完成"并退出

### Requirement: 错误处理
系统 SHALL 处理常见错误情况。

#### Scenario: 连接中断
- **WHEN** 运行中连接中断
- **THEN** 系统报错并尝试重连

#### Scenario: 参数溢出
- **WHEN** 单次角度超过327.67°（INT范围限制）
- **THEN** 系统拒绝并提示调整参数

#### Scenario: PLC报警
- **WHEN** PLC发生报警
- **THEN** 系统检测并报告报警状态