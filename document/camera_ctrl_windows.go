//go:build windows
// +build windows

package device

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"log/slog"
	"os/exec"
	"strconv"
	"time"

	"github.com/goburrow/modbus"
	"github.com/google/s2a-go/retry"
)

const (
	registerAngle    = 300   // 角度寄存器
	registerSpeed    = 304   // 速度寄存器
	registerDelay    = 316   // 自动拍照模式或延迟拍照模式下设置延迟，以s为单位
	registerModel    = 320   // 模式寄存器，值 0 间隔运行+延时，1 间隔运行+触发，2 自动运行+自动拍照
	registerContinue = 332   // 0：继续运行 1：回到起始位置 2：默认可以写为2模式
	registerPosition = 324   // 机械轴角度位置
	coilEnsure       = 57744 // 确认参数写入（包括间隔角度、运行速度、模式0延迟时间）
	coilStart        = 57745 // 启动机械轴
	coilResume       = 57746 // 暂停后重新执行，一般间隔运行+触发模式下使用
	coilReady        = 57747 // 机械轴移动完成，等待拍照信号
	coilMode         = 57348 // 模式切换，0-自动 1-手动
	coilNoError      = 57649 // 屏蔽雷达告警信号
	coilErrorOccur   = 57356 // 读取该线圈，是否出现错误，0-有错误，1-无错误
	coilRecover      = 57496 // 异常后复位处理，消除错误
	coilMachineStart = 57494 // 启动机器

	coilValueFalse = 0x0000
	coilValueTrue  = 0xff00

	coilResultFalse = 0
	coilResultTrue  = 1

	errHandleModeContinue = 0 // 继续运行
	errHandleModeInit     = 1 // 恢复到初始位置
	errHandleModeDefault  = 2 // 默认

	maxAngle = 1<<15 - 1
	minAngle = 100
	minSpeed = 100

	defaultCheckIntervalMs  = 1000 * time.Millisecond
	defaultCameraCtrlBinary = "bin\\MGWBsdk.exe"
	defaultModbusDeviceAddr = "192.168.1.88:502"
)

func discoverCameraCtrlBinary() string {
	return defaultCameraCtrlBinary
}

func (cc *CameraCtrl) GetCameraInfo(ctx context.Context) (string, error) {
	out, err := cc.cmdRun(ctx, 0, []string{"", "0", "0", "0", "2000", "2000"})
	slog.Info("cmd run", "out", string(out), "err", err)
	if err != nil {
		return "[]", err
	}

	cameraInfo := parseCameraInfo(string(out))
	data, err := json.Marshal(cameraInfo)
	return string(data), err
}

// ExecTask 执行任务
// ./MGWBSdk 文件保存路径 每个相机拍摄张数 每张照片时间间隔 轨道使能 轨道转动时间 轨道停止后等待时间
// #文件保存路径：照片保存的路径，在sdk内部格式为  xxx/picture/cameraID/xxx.jpg
// #每个相机拍摄张数：单个相机拍摄张数。照片总张数 = 此参数 X 相机数量
// #每张照片时间间隔：单位ms，建议3000ms以上。因为相机物理硬件的原因。对焦，释放快门需要时间。（当轨道使能 = 1时，此参数无效)
// #轨道使能：0-关闭，1-开启。
// #轨道转动时间：仅轨轨道使能 = 1时，此参数有效。轨道转动时间，单位ms.
// #轨道停止后等待时间：仅轨轨道使能 = 1时，此参数有效。单位ms。轨道停止后，需要一个时间来稳定支架，否则会有晃动可能导致照片拍摄模糊。（建议2000ms以上)
func (cc *CameraCtrl) ExecTask(ctx context.Context, taskId int64, taskDir string, cnt, intervalMs int) ([]byte, error) {
	if cnt <= 0 {
		return []byte{}, nil
	}

	if cnt > 180 {
		cnt = 180
	}

	// 一体采集
	if cc.cfg.Model == 1 && cnt > 1 {
		return cc.execSphereTask(ctx, taskId, taskDir, cnt, intervalMs)
	}
	// 采集箱采集或采集数量为1
	return cc.execTask(ctx, taskId, taskDir, cnt, intervalMs)
}

func (cc *CameraCtrl) execTask(ctx context.Context, taskId int64, taskDir string, cnt, intervalMs int) (out []byte, err error) {
	retry.Run(ctx, func() error {
		out, err = cc.cmdRun(ctx, taskId,
			[]string{
				taskDir,                  // 文件保存路径
				strconv.Itoa(cnt),        // 每个相机拍摄张数
				strconv.Itoa(intervalMs), // 每张照片时间间隔
				"0",                      // 轨道使能
				"200",                    // 轨道转动时间
				"200",                    // 轨道停止后等待时间
			})
		return err
	})
	if err != nil {
		slog.Error("cmd run with retry", "err", err)
		return nil, err
	}
	return out, err
}

func (cc *CameraCtrl) execSphereTask(ctx context.Context, taskId int64, taskDir string, cnt, intervalMs int) (out []byte, err error) {
	handler := modbus.NewTCPClientHandler(defaultModbusDeviceAddr)
	// handler.Logger = log.New(os.Stdout, "", log.LstdFlags)
	if err = handler.Connect(); err != nil {
		return nil, err
	}
	defer handler.Close()

	client := modbus.NewClient(handler)
	var errorSignal = coilValueFalse
	if !cc.cfg.ErrorSignal {
		errorSignal = coilValueTrue
	}
	_, err = client.WriteSingleCoil(coilNoError, errorSignal)
	if err != nil {
		return nil, err
	}
	slog.Info("设置是否启用红外感应信号成功！", "启用", errorSignal)

	// 切换模式，0 间隔运行+延时，1 间隔运行+触发，2 自动运行+自动拍照
	_, err = client.WriteSingleRegister(registerModel, 1) // 写入寄存器
	if err != nil {
		return nil, err
	}

	// 根据照片数设置角度，实际角度和速度需要乘100，最大值为32767
	// 数量最好设置能被整除，由于已经限制了照片数量为2-180，因此不会超过限制
	angle := uint16(36000 / cnt)
	// 设置转动角度和角速度，单个任务设置一次即可
	_, err = client.WriteSingleRegister(registerAngle, angle) // 写入单次转动角度
	if err != nil {
		slog.Error("设置间隔转动角度失败", "err", err)
		return nil, err
	}
	_, err = client.WriteSingleRegister(registerSpeed, cc.cfg.RotationSpeed) // 写入转动角速度
	if err != nil {
		slog.Error("设置间隔转动角速度失败", "err", err)
		return nil, err
	}

	// 确认写入的角度和角速度、延迟
	_, err = client.WriteSingleCoil(coilEnsure, coilValueTrue)
	if err != nil {
		slog.Error("确定间隔转动角度和角速度失败", "err", err)
		return nil, err
	}
	slog.Info("设置间隔角度和速度成功", "角度", angle, "角速度", cc.cfg.RotationSpeed)

	// 获取机械臂的角度位置
	res, err := client.ReadHoldingRegisters(registerPosition, 2)
	if err != nil {
		slog.Error("读取机械轴位置失败", "err", err)
		return nil, err
	}

	curAngle := binary.BigEndian.Uint16(res[0:2])
	needWaitTime := time.Second
	// 角度误差在0.1度范围内，不考虑负数
	if curAngle > 36010 || (curAngle > 10 && curAngle < 35990) {
		slog.Info("机械轴未处于始末位置", "角度", curAngle)
		// 切换自动状态
		_, err = client.WriteSingleCoil(coilMode, coilValueFalse) // 黄灯长亮
		if err != nil {
			slog.Error("机械轴切换自动模式失败", "err", err)
			return nil, err
		}
		time.Sleep(1 * time.Second)
		_, err = client.WriteSingleCoil(coilMode, coilValueTrue) // 绿灯长亮
		if err != nil {
			slog.Error("机械轴切换手动模式失败", "err", err)
			return nil, err
		}
		time.Sleep(1 * time.Second)
		needWaitTime += time.Duration(curAngle/cc.cfg.RotationSpeed) * time.Second
	}

	// 上电后，机器处于手动模式，绿灯长亮
	// 启动，绿灯闪烁
	_, err = client.WriteSingleCoil(coilMachineStart, coilValueTrue)
	if err != nil {
		slog.Error("设备手动模式下启动失败", "err", err)
		return nil, err
	}
	time.Sleep(4 * time.Second)

	_, err = client.WriteSingleCoil(coilMachineStart, coilValueFalse)
	if err != nil {
		// 打印错误即可，无需返回
		slog.Error("手动模式下重置启动按钮失败", "err", err)
	}
	// 等待机械轴到初始位置
	time.Sleep(needWaitTime)

	// 等待采集机器优化后才能实现
	// todo 发生异常时，上报错误状态，并暂停任务，等待采集平台下发继续采集或重新采集的信号
	// 0 - 继续采集（从当前位置继续）
	// 1 - 重新采集 (机械轴复位后再开始采集)
	// 2 - 默认值
	childCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	go cc.checkError(childCtx, client)

	// 启动机械轴
	_, err = client.WriteSingleCoil(coilStart, coilValueTrue)
	if err != nil {
		slog.Error("机械轴启动失败", "err", err)
		return nil, err
	}
	time.Sleep(1 * time.Second)
	slog.Debug("机械轴启动成功！")

	for i := 0; i < cnt; i++ {
		// 模式一暂停后启动
		_, err = client.WriteSingleCoil(coilResume, coilValueTrue)
		if err != nil {
			slog.Error("发送继续信号失败", "err", err.Error())
			continue
		}
		slog.Debug("发送机械臂继续转动信号成功！")

		// 等待是否轨道是否就位，可以根据时间估算，也可以读取信号
		err = cc.waitingForReady(ctx, client)
		if err != nil {
			// 仅当ctx Done后才会返回错误
			slog.Error("等待机械臂就绪错误", "err", err.Error())
			return nil, err
		}

		// 拍照
		slog.Debug("开始执行拍照！")
		_, err = cc.execTask(ctx, taskId, taskDir, 1, intervalMs)
		if err != nil {
			slog.Error("执行拍照失败", "err", err.Error())
			continue
		}
		slog.Debug("执行拍照成功！")
	}

	// // 模式一下冗余转动一次，理论上机械轴不会转动
	// _, err = client.WriteSingleCoil(coilResume, coilValueTrue)
	// if err != nil {
	// 	return nil, err
	// }
	return nil, nil
}

func (cc *CameraCtrl) waitingForReady(ctx context.Context, client modbus.Client) error {
	intervalMs := cc.cfg.CheckErrInterval
	if cc.cfg.CheckErrInterval < defaultCheckIntervalMs {
		intervalMs = defaultCheckIntervalMs
	}
	ticker := time.NewTicker(intervalMs)
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			// 循环获取状态
			res, err := client.ReadCoils(coilReady, 1)
			if err != nil {
				slog.Error("获取机械臂是否就绪失败", "err", err)
				continue
			}
			if res[0] == coilResultTrue {
				slog.Debug("机械臂位置就绪！")
				return nil
			}
		}
	}
}

func (cc *CameraCtrl) checkError(ctx context.Context, client modbus.Client) {
	intervalMs := cc.cfg.CheckInterval
	if cc.cfg.CheckInterval < defaultCheckIntervalMs {
		intervalMs = defaultCheckIntervalMs
	}
	ticker := time.NewTicker(intervalMs)
	for {
		select {
		case <-ctx.Done():
			slog.Error("上下文结束", "err", ctx.Err())
			return
		case <-ticker.C:
			// 循环获取状态
			res, err := client.ReadCoils(coilErrorOccur, 1)
			if err != nil {
				slog.Error("获取机器是否异常失败", "err", err)
				continue
			}
			if res[0] == coilResultFalse {
				slog.Error("机器出现异常，请等待异常处理完成！")
				// 异常时，先设置继续模式
				_, err = client.WriteSingleRegister(registerContinue, cc.cfg.ErrContinueModel)
				if err != nil {
					slog.Error("设置异常处理模式失败", "err", err)
				}
				time.Sleep(1 * time.Second)
				// 触发复位按钮，消除告警信号
				_, err = client.WriteSingleCoil(coilRecover, coilValueTrue)
				if err != nil {
					slog.Error("异常后复位失败", "err", err)
				}
				time.Sleep(1 * time.Second)
				_, err = client.WriteSingleCoil(coilRecover, coilValueFalse)
				if err != nil {
					slog.Error("异常后复位失败", "err", err)
				}
				if cc.cfg.ErrContinueModel == errHandleModeInit {
					return
				}
				continue
			}
		}
	}
}

func (cc *CaptureCtrl) cmdRun(ctx context.Context, args []string) ([]byte, error) {
	binary := discoverCameraCtrlBinary()
	cmd := exec.CommandContext(ctx, binary, args...)
	if cmd.Err != nil {
		return nil, cmd.Err
	}
	// 获取输出管道
	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}

	// 启动命令
	err = cmd.Start()
	if err != nil {
		return nil, err
	}

	// 读取输出
	var out bytes.Buffer
	_, err = out.ReadFrom(stdoutPipe)
	if err != nil {
		return nil, err
	}

	// 等待命令完成
	err = cmd.Wait()
	if err != nil {
		return nil, err
	}
	if cmd.Err != nil {
		return nil, cmd.Err
	}

	return out.Bytes(), nil
}

func dealError(client modbus.Client) error {
	res, err := client.ReadCoils(57356, 1)
	if err != nil {
		return err
	}
	if res[0] == coilResultTrue {
		return nil
	}
	// 异常情况下复位，绿灯长亮
	_, err = client.WriteSingleCoil(57496, 65280)
	if err != nil {
		return err
	}
	time.Sleep(5 * time.Second)

	_, err = client.WriteSingleCoil(57496, 0)
	if err != nil {
		slog.Error("重置复位按钮失败", "err", err)
	}
	time.Sleep(1 * time.Second)

	// 切换自动状态
	_, err = client.WriteSingleCoil(57348, 0) // 黄灯长亮
	if err != nil {
		return err
	}
	time.Sleep(1 * time.Second)
	_, err = client.WriteSingleCoil(57348, 65280) // 绿灯长亮
	if err != nil {
		return err
	}
	time.Sleep(1 * time.Second)
	return nil
}
