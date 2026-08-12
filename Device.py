# -- coding: utf-8 --

from nt import error
import sys
import os
import time
from ctypes import *

from MVSDK.IMVDefines import IMV_ErrorList

current_dir = os.path.dirname(os.path.abspath(__file__))
mvsdk_path = os.path.join(current_dir, "MVSDK")
if mvsdk_path not in sys.path:
    sys.path.append(mvsdk_path)
from IMVApi import *


class CameraDevice():
    def __init__(self):
        self.m_index = 0xff
        self.m_Key = ""
        self.m_userId = ""
        self.cam = MvCamera()

    def init(self, index, camInfo):
        self.m_index = index
        self.m_Key = camInfo.cameraKey
        self.m_userId = camInfo.cameraName.decode("gbk", errors="ignore") if isinstance(camInfo.cameraName, bytes) else camInfo.cameraName
        return IMV_OK

    def openDevice(self):
        nRet = self.cam.IMV_CreateHandle(IMV_ECreateHandleMode.modeByIndex, byref(c_void_p(self.m_index)))
        if IMV_OK != nRet:
            print(f"[{self.m_userId}] Create devHandle failed! ErrorCode: {nRet}")
            return nRet

        nRet = self.cam.IMV_Open()
        if IMV_OK != nRet:
            print(f"[{self.m_userId}] Open devHandle failed! ErrorCode: {nRet}")
            self.cam.IMV_DestroyHandle()
            return nRet

        # Load preset config
        # errorList = IMV_ErrorList()
        # nRet = self.cam.IMV_LoadDeviceCfg("./configs/config.mvcfg", errorList)
        # if IMV_OK != nRet:
        #     print(f"[{self.m_userId}] load config file failed! ErrorCode: {nRet}")
        
        return nRet

    def closeDevice(self):
        if self.cam.handle:
            self.cam.IMV_Close()
            self.cam.IMV_DestroyHandle()

    # --- 属性设置通用方法 ---
    def setDoubleValue(self, pFeatureName, doubleValue):
        if not self.cam.handle: return IMV_INVALID_HANDLE
        return self.cam.IMV_SetDoubleFeatureValue(pFeatureName, doubleValue)

    def setEnumSymbol(self, pFeatureName, pStringValue):
        if not self.cam.handle: return IMV_INVALID_HANDLE
        return self.cam.IMV_SetEnumFeatureSymbol(pFeatureName, pStringValue)

    def executeCommand(self, pFeatureName):
        if not self.cam.handle: return IMV_INVALID_HANDLE
        return self.cam.IMV_ExecuteCommandFeature(pFeatureName)

    # --- 核心：单次软触发拍照并保存 ---
    def snap_and_save(self, save_path):
        if not self.cam.handle: return IMV_INVALID_HANDLE
        
        frame = IMV_Frame()
        
        # 1. 发送软触发命令
        nRet = self.executeCommand("TriggerSoftware")
        if IMV_OK != nRet:
            print(f"[{self.m_userId}] Execute TriggerSoftware failed! Error: {nRet}")
            return nRet

        # 2. 获取一帧图像 (超时时间视分辨率大小而定，上亿像素建议留足5000ms)
        nRet = self.cam.IMV_GetFrame(frame, 5000)
        if IMV_OK != nRet:
            print(f"[{self.m_userId}] Get frame failed! Error: {nRet}")
            return nRet

        # 3. 构造内存转码保存参数
        saveImageParam = IMV_SaveImageParam()
        
        # 动态判断保存格式 (根据你的 save_path 后缀名)
        if save_path.lower().endswith('.bmp'):
            saveImageParam.eImageType = IMV_ESaveType.typeImageBmp
        else:
            saveImageParam.eImageType = IMV_ESaveType.typeImageJpeg
            
        saveImageParam.nWidth = frame.frameInfo.width
        saveImageParam.nHeight = frame.frameInfo.height
        saveImageParam.ePixelFormat = frame.frameInfo.pixelFormat
        saveImageParam.pSrcData = frame.pData
        saveImageParam.nSrcDataLen = frame.frameInfo.size
        saveImageParam.eBayerDemosaic = 2
        saveImageParam.nQuality = 100 # JPEG 压缩质量 0-100，BMP 不使用该参数
        
        # 分配目标内存缓存：宽 * 高 * 4 (给足最大可能的数据量空间)
        buf_size = frame.frameInfo.width * frame.frameInfo.height * 4
        saveImageParam.pDstBuf = (c_ubyte * buf_size)()
        saveImageParam.nDstBufSize = buf_size

        # 4. 调用 SDK，将图像数据在内存中转码为目标格式
        nRet = self.cam.IMV_SaveImage(saveImageParam)
        if IMV_OK != nRet:
            print(f"[{self.m_userId}] IMV_SaveImage failed! Error: {nRet}")
        else:
            # 5. 使用 Python 原生文件操作将转码后的字节流写入磁盘
            # 截取实际生成的有效数据长度 nDstDataLen
            pixel_bytes = bytes(saveImageParam.pDstBuf[:saveImageParam.nDstDataLen])
            with open(save_path, "wb+") as f:
                f.write(pixel_bytes)

        # 6. 释放图像缓存（至关重要） [cite: 1021]
        self.cam.IMV_ReleaseFrame(frame)
        return nRet
    
    def download_xml(self, save_path):
        """下载相机的 GenICam XML 描述文件"""
        if not self.cam.handle: 
            return IMV_INVALID_HANDLE
        
        # 直接传入普通的 Python 字符串，IMVApi.py 内部会去 encode
        nRet = self.cam.IMV_DownLoadGenICamXML(save_path)
        
        if nRet != IMV_OK:
            print(f"[{self.m_userId}] 下载 XML 失败！错误码: {nRet}")
        return nRet


class DeviceSystem():
    def __init__(self):
        self.m_Device = []
        self.m_DeviceNum = 0

    def initSystem(self):
        deviceList = IMV_DeviceList()
        # 枚举所有设备 [cite: 142]
        nRet = MvCamera.IMV_EnumDevices(deviceList, IMV_EInterfaceType.interfaceTypeAll)
        print(deviceList.nDevNum)
        if IMV_OK != nRet or deviceList.nDevNum == 0:
            print("Enumeration devices failed or no device found!")
            sys.exit()

        self.m_DeviceNum = deviceList.nDevNum
        print(f"Found {self.m_DeviceNum} cameras.")

        for i in range(deviceList.nDevNum):
            cam = CameraDevice()
            cam.init(i, deviceList.pDevInfo[i])
            self.m_Device.append(cam)
