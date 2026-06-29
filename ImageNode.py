# -- coding: utf-8 --

from Device import *
from LensController import *
import concurrent.futures
import os
import time
import sys

# TODO: 修改为实际连接的相机名称和对应的串口
CAMERA_COM_MAP = {
    "1号": "COM7",
    "2号": "COM8",
    "3号": "COM9",
    "4号": "COM10",
}

class ImagingNode:
    def __init__(self, cam_index, cam_info):
        self.camera = CameraDevice()
        self.camera.init(cam_index, cam_info)
        
        cam_name = self.camera.m_userId
        com_port = CAMERA_COM_MAP.get(cam_name, None)
        
        if not com_port:
            print(f"⚠️ 警告: 未在映射表中找到相机 [{cam_name}] 对应的串口号！镜头不可控。")
            self.lens = None
        else:
            self.lens = LensController(port=com_port)

    def open_node(self):
        cam_ret = self.camera.openDevice()
        if cam_ret == IMV_OK:
            self.camera.setEnumSymbol("TriggerMode", "On")
            self.camera.setEnumSymbol("TriggerSource", "Software")
            self.camera.cam.IMV_StartGrabbing()
            print(f"✅ 相机 [{self.camera.m_userId}] 开启成功并启动拉流。")
        else:
            print(f"❌ 相机 [{self.camera.m_userId}] 开启失败！")
            return False

        if self.lens:
            self.lens.open()
        return True

    def close_node(self):
        self.camera.cam.IMV_StopGrabbing()
        self.camera.closeDevice()
        if self.lens:
            self.lens.shutdown_lens()


class ImagingSystem:
    def __init__(self):
        self.nodes = []

    def init_system(self):
        deviceList = IMV_DeviceList()
        nRet = MvCamera.IMV_EnumDevices(deviceList, IMV_EInterfaceType.interfaceTypeGige)
        if IMV_OK != nRet or deviceList.nDevNum == 0:
            print("❌ 枚举设备失败或未找到相机！")
            sys.exit()

        print(f"🔍 找到 {deviceList.nDevNum} 台相机。正在初始化成像节点...")
        for i in range(deviceList.nDevNum):
            node = ImagingNode(i, deviceList.pDevInfo[i])
            self.nodes.append(node)

    def open_all(self):
        for node in self.nodes:
            node.open_node()

    def close_all(self):
        for node in self.nodes:
            node.close_node()
        print("✅ 所有相机和镜头均已关闭。")

    def global_homing(self):
        print("\n⚙️  ================ 执行系统全局绝对归零 ================")
        for node in self.nodes:
            if node.lens:
                node.lens.initialize_lens()
        print("✅  所有镜头全局归零完毕！绝对物理坐标系已确立。")

    def serial_move_lenses(self, target_angle):
        print(f"\n ⚙️ 正在向所有镜头串行下发转动指令 -> 目标角度: {target_angle}°")
        for node in self.nodes:
            if node.lens:
                node.lens.move_to_absolute_angle(target_angle)
        print(" ✅ 所有镜头均已到位。")

    def parallel_move_lenses(self, target_angle):
        print(f"\n ⚙️ 正在向所有镜头并发下发转动指令 -> 目标角度: {target_angle}°")
        nodes_with_lens = [node for node in self.nodes if node.lens]
        
        if not nodes_with_lens:
            print(" ⚠️ 没有可控的镜头。")
            return

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes_with_lens)) as executor:
            futures = [executor.submit(node.lens.move_to_absolute_angle, target_angle) 
                       for node in nodes_with_lens]
            concurrent.futures.wait(futures)
            
        print(" ✅ 所有镜头均已同步到位。")

    def serial_snap_all(self, save_dir_base):
        os.makedirs(save_dir_base, exist_ok=True)
        print(" 📸 正在依次触发拍摄并写入磁盘...")
        for node in self.nodes:
            cam_name = node.camera.m_userId
            save_path = os.path.join(save_dir_base, f"{cam_name}.jpg")
            
            t0 = time.time()
            ret = node.camera.snap_and_save(save_path)
            t1 = time.time()
            
            if ret == IMV_OK:
                print(f"    ✅ [{cam_name}] 保存成功 (耗时: {t1-t0:.2f}s) -> {save_path}")
            else:
                print(f"    ❌ [{cam_name}] 保存失败！错误码: {ret}")