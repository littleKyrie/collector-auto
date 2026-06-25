import time
import sys
import os
from ImageNode import *

# ==========================================
# ⚙️ 硬件标定配置区 
# ==========================================
# 定义镜头的物理旋转边界（正数，0点在最缩进端）
ANGLE_START = 300.0      # 起始端角度 
ANGLE_END   = 2000.0     # 终点端角度 

def generate_step_angles(steps):
    """根据步进次数，自动计算均分的物理角度列表"""
    if steps <= 0:
        return []
    if steps == 1:
        return [ANGLE_START]
    
    angles = []
    # 计算每个步进的角度跨度
    step_interval = (ANGLE_END - ANGLE_START) / (steps - 1)
    
    for i in range(steps):
        # 计算当前步进的具体角度，并保留两位小数
        current_angle = round(ANGLE_START + (i * step_interval), 2)
        angles.append(current_angle)
        
    return angles

def main():
    print("================ 自动化多阵列拍摄系统 ================")
    try:
        rounds = int(input("请输入拍摄转台轮次: "))
        wait_time = float(input("请输入每轮转台拍摄结束后的休眠等待时间(秒): "))
        steps = int(input(f"请输入镜头步进拍摄次数 (将在 {ANGLE_START}° 到 {ANGLE_END}° 之间均分): "))
    except ValueError:
        print("❌ 输入格式有误，请重新运行程序并输入有效的整数或小数。")
        return

    # 1. 自动计算本轮所有需要停留的目标角度
    target_angles = generate_step_angles(steps)
    print(f"\n📊 系统已自动规划 {steps} 个步进拍摄点:")
    for idx, angle in enumerate(target_angles):
        print(f"   - 步进 {idx + 1}: 目标角度 {angle}°")

    # 2. 初始化硬件
    sys_dev = ImagingSystem()
    sys_dev.init_system()
    sys_dev.open_all()

    # 3. 硬件上电找0及标定
    sys_dev.global_homing()

    # 4. 自动化生产大循环
    for r in range(1, rounds + 1):
        round_start_time = time.time()
        print(f"\n================ 正在执行第 {r}/{rounds} 轮转台拍摄 ================")

        # 遍历生成的均分角度列表
        for step_idx, target_angle in enumerate(target_angles):
            print(f"\n 🎯 [第 {r} 轮 - 步进 {step_idx + 1}/{steps}] -> 目标角度: {target_angle}°")
            
            # 【串行/并发】驱动所有镜头前往绝对坐标
            # sys_dev.parallel_move_lenses(target_angle)
            sys_dev.serial_move_lenses(target_angle)

            # 【串行】镜头停稳后，稳健地触发相机拍照保存
            # 文件夹命名更改为以 步进序号 和 物理角度 命名，方便后期查阅
            save_dir_base = os.path.abspath(f"./Output/Round_{r}/Step_{step_idx + 1}_Angle_{target_angle}")
            sys_dev.serial_snap_all(save_dir_base)

        print("\n 🔙 本轮拍摄完毕，镜头正在平滑退回初始 0 点...")
        # 直接调用底层的绝对定位去 0.0 度，LensController 内部会自动拦截并使用官方回0指令
        sys_dev.serial_move_lenses(0.0)

        # 5. 时间补偿与转台同步
        elapsed = time.time() - round_start_time
        if elapsed < wait_time:
            sleep_time = wait_time - elapsed
            print(f"\n⏳ 本轮拍摄完成 (耗时 {elapsed:.2f}s)。等待转台就绪，休眠 {sleep_time:.2f}s...")
            time.sleep(sleep_time)
        else:
            print(f"\n⚠️ 警告：本轮耗时 {elapsed:.2f}s，已超出预设的转台停留时间 {wait_time}s！")

    # 6. 安全清理
    print("\n🎉 拍摄任务全部结束，正在安全关闭硬件...")
    sys_dev.close_all()

if __name__ == "__main__":
    main()