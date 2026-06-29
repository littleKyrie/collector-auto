# add_mvsdk_path.py
import sys
import os

# 获取 exe 所在目录（打包后）或当前工作目录（开发时）
if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

# 将 MVSDK 子目录添加到 sys.path 的头部
mvsdk_path = os.path.join(base_dir, 'MVSDK')
if os.path.exists(mvsdk_path):
    sys.path.insert(0, mvsdk_path)