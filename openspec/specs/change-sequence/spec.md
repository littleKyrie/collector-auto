## ADDED Requirements

### Requirement: 触发模式旋转序列控制
系统 SHALL 使用PLC模式1（间隔运行+触发）执行旋转序列，在每次机械轴转动到位后通过触发信号控制外部操作。

#### Scenario: 配置模式1
- **WHEN** 系统配置旋转参数
- **THEN** 设置D320=1（模式1：间隔运行+触发）

#### Scenario: 参数写入
- **WHEN** 系统写入参数
- **THEN** 写入D300（角度×100）、D304（速度×100）

#### Scenario: 参数生效
- **WHEN** 参数写入完成
- **THEN** 触发S400使参数生效

#### Scenario: 发送继续信号
- **WHEN** 系统需要机械轴转动到下一个位置
- **THEN** 发送S406（coilResume）触发机械轴转动

#### Scenario: 等待机械轴就绪
- **WHEN** 发送继续信号后
- **THEN** 轮询S407（coilReady）直到机械轴移动完成

#### Scenario: 执行回调操作
- **WHEN** 机械轴就绪信号到达
- **THEN** 执行注册的回调函数（如拍照）

### Requirement: 机械轴位置检测与复位
系统 SHALL 在启动旋转序列前检测机械轴当前位置，若不在起始位置则执行复位操作。

#### Scenario: 读取当前位置
- **WHEN** 启动旋转序列前
- **THEN** 读取D324（registerPosition）获取当前角度位置

#### Scenario: 判断是否需要复位
- **WHEN** 当前角度不在0°附近（误差范围±1°）
- **THEN** 执行复位流程

#### Scenario: 执行复位
- **WHEN** 需要复位
- **THEN** 切换自动模式（S57348=0）→ 等待1秒 → 切换手动模式（S57348=1）→ 等待回到起始位置

#### Scenario: 起始位置确认
- **WHEN** 复位完成
- **THEN** 当前角度在0°附近（±1°范围内）

### Requirement: 手动模式启动流程
系统 SHALL 在手动模式下执行设备启动流程，确保PLC进入可运行状态。

#### Scenario: 启动机器
- **WHEN** 设备处于手动模式
- **THEN** 写入S57494（coilMachineStart）=1启动机器

#### Scenario: 等待启动
- **WHEN** 发送启动信号后
- **THEN** 等待4秒确保设备启动完成

#### Scenario: 重置启动按钮
- **WHEN** 启动等待完成
- **THEN** 写入S57494=0重置启动按钮

### Requirement: 红外感应信号控制
系统 SHALL 支持配置是否屏蔽红外感应信号。

#### Scenario: 屏蔽红外信号
- **WHEN** error_signal参数为True
- **THEN** 写入S57649（coilNoError）=0xFF00屏蔽红外感应

#### Scenario: 启用红外信号
- **WHEN** error_signal参数为False
- **THEN** 写入S57649=0x0000启用红外感应

### Requirement: 异步错误检测
系统 SHALL 使用后台线程持续监控PLC错误状态，检测到错误时执行异常处理。

#### Scenario: 启动错误检测线程
- **WHEN** 旋转序列开始
- **THEN** 启动后台线程定期读取S57356（coilErrorOccur）

#### Scenario: 检测到错误
- **WHEN** S57356=0（有错误）
- **THEN** 执行异常处理流程

#### Scenario: 异常处理-继续模式
- **WHEN** error_continue_model=0且检测到错误
- **THEN** 写入D332（registerContinue）=0，触发S57496（coilRecover）复位

#### Scenario: 异常处理-复位模式
- **WHEN** error_continue_model=1且检测到错误
- **THEN** 写入D332=1，触发S57496复位，停止错误检测线程

#### Scenario: 停止错误检测
- **WHEN** 旋转序列结束
- **THEN** 通过Event.set()通知错误检测线程退出

### Requirement: 回调机制
系统 SHALL 支持注册回调函数，在每次机械轴就绪时调用。

#### Scenario: 注册回调
- **WHEN** 调用run_rotation_sequence时传入action_callback参数
- **THEN** 系统保存回调函数引用

#### Scenario: 调用回调
- **WHEN** 机械轴就绪信号到达
- **THEN** 调用action_callback(current_index, total_count)

#### Scenario: 回调返回值处理
- **WHEN** 回调函数返回False
- **THEN** 继续下一次旋转（跳过当前步骤）

#### Scenario: 回调异常处理
- **WHEN** 回调函数抛出异常
- **THEN** 记录错误日志，继续下一次旋转