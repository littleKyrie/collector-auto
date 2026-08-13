# 文物自动采集控制系统

本项目将 PLC 转台控制、工业相机拍摄和电动镜头控制整合为一个自动采集流程。

系统按照设定次数将转台旋转一周。每次转台移动后，程序控制镜头依次移动到多个焦段，并在每个焦段触发所有相机拍摄。一个阵位的全部焦段拍摄完成后，镜头返回零点，程序再控制转台移动到下一个阵位。

## 主要功能

- 通过 Modbus TCP 控制 PLC 转台。
- 根据旋转次数自动计算单次转台角度：`360° / rotations`。
- 通过串口控制电动镜头寻零、绝对角度移动和回零。
- 使用工业相机软件触发模式采集并保存 JPG 图片。
- 每个转台阵位支持多个镜头焦段拍摄。
- 支持多相机和多镜头节点。
- 按相机名称、阵位和镜头步进组织输出图片。
- 任务结束或程序退出时关闭相机、镜头和 PLC 连接。

## 当前执行流程

```text
枚举并打开相机
→ 打开镜头串口
→ 镜头全局寻零
→ 连接 PLC
→ 配置并复位转台
→ 触发转台旋转一次
→ 等待转台移动
→ 镜头移动到焦段 1，触发所有相机拍摄
→ 镜头移动到焦段 2，触发所有相机拍摄
→ ...
→ 镜头回到 0 点
→ 等待 delay 秒
→ 进入下一个转台阵位
→ 完成全部阵位，转台累计旋转约 360°
```

镜头移动和相机拍摄通过转台控制器的同步回调执行。在当前单线程流程中，一组拍摄没有返回前，程序不会主动触发下一次转台旋转。

## 运行环境

推荐使用当前已验证的基础环境：

- Windows 10/11 x64
- Conda 环境名称：`camera_control`
- Python 3.10.12
- 千兆网工业相机及项目内配套的 MVSDK/Runtime 文件
- 支持 Modbus TCP 的 PLC，默认地址 `192.168.1.88:502`
- 串口控制的电动镜头，默认波特率 `115200`

相机 SDK 的 Python 封装、DLL、CTI 和相机协议文件已经包含在以下目录中：

```text
MVSDK/
Runtime/
```

这些厂商运行库不是 PyPI 包，不能通过 `requirements.txt` 安装。运行程序时应保留项目现有目录结构，并确保 Python 位数与厂商 DLL 位数一致。推荐使用 64 位 Python。

## Python 依赖

`requirements.txt` 整合了 `camera_control` 环境中的相机相关包，以及镜头和转台代码需要的包：

| 包 | 版本 | 用途 |
|---|---:|---|
| `numpy` | 2.2.6 | 相机图像处理基础环境 |
| `opencv-python` | 4.12.0.88 | 相机图像处理与后续扩展 |
| `pyserial` | 3.5 | 镜头串口通信 |
| `pymodbus` | 3.13.1 | PLC Modbus TCP 通信 |

当前 `camera_control` 环境原有 `numpy` 和 `opencv-python`。运行整合后的 `main.py` 还必须安装 `pyserial` 和 `pymodbus`。

## 安装

### 使用现有 camera_control 环境

```powershell
conda activate camera_control
python -m pip install -r requirements.txt
```

### 重新创建环境

```powershell
conda create -n camera_control python=3.10.12 -y
conda activate camera_control
python -m pip install -r requirements.txt
```

安装后可检查依赖：

```powershell
python -m pip check
python -c "import cv2, numpy, serial, pymodbus; print('dependencies ok')"
```

## 硬件配置

### 相机与镜头串口映射

在 `ImageNode.py` 中配置相机名称和镜头串口：

```python
CAMERA_COM_MAP = {
    "1号": "COM4",
}
```

左侧名称必须与相机 SDK 枚举得到的 `cameraName` 完全一致，右侧填写该相机对应镜头控制器的 Windows 串口号。

如果相机没有配置映射，相机仍可能参与拍摄，但该节点的镜头不可控。

### 镜头角度范围

集成程序在 `main.py` 中使用以下范围生成均匀焦段：

```python
ANGLE_START = 0.0
ANGLE_END = 2900.0
```

运行前必须根据实际镜头标定结果确认这个范围安全。镜头控制器的编码器分辨率和默认速度位于 `LensController.py`：

```python
LENS_ENCODER_RES = 32768
LENS_SPEED_RPM = 200
```

镜头上电后的初始化顺序为：

1. 配置串口静默。
2. 执行“上电找 0”。
3. 等待硬件标定。
4. 执行“点击回 0”。
5. 读取状态和实际角度，确认镜头处于零点附近。

### PLC 网络配置

默认 PLC 参数：

```text
IP:   192.168.1.88
Port: 502
Unit/Device ID: 1
```

运行电脑必须与 PLC 位于可通信的网络中。可以通过命令行覆盖 IP 和端口。

## 使用方法

查看顶层模式：

```powershell
python main.py --help
```

查看旋转拍摄参数：

```powershell
python main.py --full_shot --help
```

使用默认参数运行：

```powershell
python main.py --full_shot
```

默认配置为：

- 转台旋转 12 次。
- 每次理论旋转 30°。
- 转台速度 10°/s。
- 每个阵位拍摄 5 个镜头焦段。
- 每个阵位拍摄和镜头回零后额外等待 3 秒。
- PLC 地址为 `192.168.1.88:502`。

自定义运行示例：

```powershell
python main.py --full_shot --rotations 24 --speed 5 --delay 2 --lens-steps 7
```

连接其他 PLC：

```powershell
python main.py --full_shot --host 192.168.1.100 --port 502
```

显示详细日志：

```powershell
python main.py --full_shot --verbose
```

指定图片输出目录和镜头范围配置文件：

```powershell
python main.py --full_shot `
  --output_path C://results `
  --config_path C://camera-configs/lens_range_map.json
```

### 命令行参数

| 参数 | 缩写 | 默认值 | 说明 |
|---|---|---:|---|
| `--rotations` | `-r` | `12` | 一周内的转台旋转次数 |
| `--speed` | `-s` | `10.0` | 转台速度，单位 °/s，允许范围为 0～30 |
| `--delay` | `-d` | `3.0` | 每个阵位拍摄及镜头回零后的额外等待时间，单位秒 |
| `--lens-steps` | - | `5` | 每个转台阵位的镜头焦段数量 |
| `--host` | - | `192.168.1.88` | PLC IP 地址 |
| `--port` | `-p` | `502` | PLC Modbus TCP 端口 |
| `--error-signal` | - | `True` | 是否屏蔽红外感应信号 |
| `--error-continue-model` | - | `2` | PLC 异常处理模式：0=继续，1=复位，2=默认 |
| `--output_path` | - | 项目根目录下的 `Output` | 图片输出根目录；正式拍摄前清空其中的旧内容 |
| `--config_path` | - | 项目根目录下的 `configs/lens_range_map.json` | 本次拍摄读取的镜头范围配置文件 |
| `--verbose` | `-v` | 关闭 | 输出详细日志 |

相对形式的 `output_path` 和 `config_path` 都以项目根目录为基准解析。`config_path` 是具体 JSON 文件路径，不是目录路径。

## 图片输出

图片默认保存到项目根目录的 `Output` 文件夹：

```text
Output/
├── 1/
│   ├── Position1_Step1.jpg
│   ├── Position1_Step2.jpg
│   └── ...
└── 2/
    ├── Position1_Step1.jpg
    ├── Position1_Step2.jpg
    └── ...
```

目录含义：

- 第一层目录名是相机 SDK 返回的当前相机名，例如 `1`、`2`。
- `Position{i}` 表示第 `i` 个转台阵位。
- `Step{j}` 表示该阵位的第 `j` 个镜头步进。
- 图片格式保持为 JPG。

每次正式拍摄前，程序会清空本次选定的图片输出根目录，然后在其中重新创建相机子目录。该清理在一轮任务中只执行一次，不会在阵位或镜头步进之间重复执行。

为避免误删，盘符根目录、文件系统根目录、项目根目录、符号链接/junction 根目录，以及包含本次 `config_path` 的目录不能作为 `output_path`。如果清理失败，拍摄不会启动。

自定义输出示例 `--output_path C://results` 会生成：

```text
C://results/1/Position1_Step1.jpg
C://results/2/Position1_Step1.jpg
```

## PLC 寄存器与线圈

转台控制使用模式 1：间隔运行加外部触发。

| 名称 | 地址 | 作用 |
|---|---:|---|
| `D300` | 300 | 单次间隔运行角度，写入值为角度 × 100 |
| `D304` | 304 | 运行速度，写入值为速度 × 100 |
| `D320` | 320 | 工作模式，当前写入 1 |
| `D324` | 324 | 机械轴当前位置 |
| `D332` | 332 | 异常后的继续运行模式 |
| `S400` | 57744 | 确认参数写入 |
| `coilStart` | 57745 | 启动机械轴 |
| `coilResume` | 57746 | 触发下一次旋转 |
| `coilReady` | 57747 | 机械轴移动完成/等待拍摄信号 |
| `coilMode` | 57348 | 自动/手动模式切换 |
| `coilNoError` | 57649 | 屏蔽雷达或红外告警 |
| `coilErrorOccur` | 57356 | PLC 错误状态 |
| `coilRecover` | 57496 | 异常恢复 |
| `coilMachineStart` | 57494 | 手动模式启动 |

实际地址和有效电平应以 PLC 程序及 `document/文物扫描寄存器地址及介绍250123.xlsx` 为准。

## 项目结构

```text
collector-auto/
├── main.py                  # 相机、镜头、转台联动入口
├── rotation_controller.py   # PLC 转台业务流程
├── modbus_client.py         # Modbus TCP 客户端封装
├── ImageNode.py             # 相机与镜头节点、成像系统
├── Device.py                # 工业相机 SDK 封装
├── LensController.py        # 镜头串口协议和运动控制
├── MutipleCameras.py        # 独立的多相机/镜头测试流程
├── MVSDK/                   # 厂商相机 Python SDK
├── Runtime/                 # 厂商相机运行库
├── configs/                 # 相机配置文件目录
├── document/                # PLC 寄存器及参考实现
├── Output/                  # 默认图片输出目录
├── requirements.txt         # Python 依赖
└── README.md                # 项目说明
```

## 当前实现注意事项

以下内容是联调和生产使用前需要确认的现状：

1. 转台旋转后当前主要通过理论运动时间加 2 秒等待，`wait_for_ready()` 尚未启用。严格采集应使用 PLC 到位信号确认机械轴完全停止后再拍摄。
2. 镜头移动失败或相机保存失败目前主要输出日志，不一定会中止整轮任务。
3. `coilResume` 当前持续写入真值，没有由 Python 显式恢复为假值，需要确认 PLC 是否会自动复位该线圈。
4. `main.py` 当前会调用两次 `client.connect()`，建议后续保留一次连接。
5. 单次转角会转换为“角度 × 100”的整数。无法精确整除 360° 的旋转次数可能产生小量累计误差。
6. 相机、镜头打开和寻零的返回值目前没有全部向主流程传播。
7. 多台相机当前依次触发，不是严格同时曝光。

## 常见问题

### 找不到 `serial` 模块

```powershell
python -m pip install pyserial==3.5
```

不要安装名称为 `serial` 的其他包。

### 找不到 `pymodbus` 模块

```powershell
python -m pip install pymodbus==3.13.1
```

本项目使用 `device_id=` 参数，README 和 requirements 中记录的版本已核对该接口。

### 找不到相机

- 确认相机已上电且网线连接正常。
- 确认电脑网卡与相机处于同一网段。
- 使用厂商工具确认相机可以被枚举和采集。
- 确认 `Runtime`、CTI 和协议文件完整。
- 检查防火墙是否阻止相机通信。

### `MVSDKmd.dll` 报 WinError 1114

如果程序在导入 `IMVApi.py` 时提示 DLL 初始化失败，这通常不是 `requirements.txt` 中的 Python 包缺失，而是厂商原生运行库未能正确加载。依次检查：

- 使用 64 位 Python 运行 `Runtime/x64/MVSDKmd.dll`，不要混用 Win32 DLL。
- 安装或修复相机厂商 SDK、驱动及其要求的 Microsoft Visual C++ 运行库。
- 确认 `Runtime/x64` 中的配套 DLL 完整，且没有被其他版本的同名 DLL 覆盖。
- 使用厂商客户端确认相机 SDK 能在当前电脑正常启动。
- 确认从项目根目录运行程序，使 `./Runtime/x64/MVSDKmd.dll` 路径有效。

由于 `main.py` 在解析命令行前会导入相机 SDK，原生 DLL 初始化失败时，`python main.py --help` 也会失败。

### 镜头无法控制

- 检查 `CAMERA_COM_MAP` 中的相机名称和 COM 口。
- 在设备管理器中确认串口存在且未被其他程序占用。
- 确认串口波特率为 115200。
- 确认镜头寻零成功后再开始正式采集。

### PLC 无法连接

- 检查 PLC 地址、端口和供电。
- 检查电脑与 PLC 的网络连通性。
- 确认 PLC 已启用 Modbus TCP 服务。
- 确认防火墙允许 TCP 502 端口。

## 依赖更新原则

生产采集环境建议继续使用 `requirements.txt` 中的固定版本，避免现场环境因包升级发生接口变化。升级依赖时至少应重新验证：

- 相机枚举、打开、软触发、取帧和图片保存。
- 镜头串口打开、寻零、绝对移动、状态读取和回零。
- PLC 寄存器读写、线圈读写和完整旋转序列。
- 多阵位、多焦段完整采集及退出清理。

### 项目打包

```cmd
python -m pip install pyinstaller
pyinstaller --add-data "Runtime;Runtime" --add-data "MVSDK;MVSDK" --runtime-hook add_mvsdk_path.py --hidden-import platform --hidden-import IMVApi main.py

新的打包指令：
pyinstaller --clean --noconfirm --paths "." --paths "MVSDK" --add-data "Runtime;Runtime" --add-data "MVSDK;MVSDK" --add-data "configs;configs" --runtime-hook "add_MVSDK_path.py" --hidden-import "platform" --hidden-import "IMVApi" --hidden-import "ImageNode" --hidden-import "Device" --hidden-import "LensController" main.py
