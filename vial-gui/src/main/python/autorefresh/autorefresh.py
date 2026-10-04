import logging
import sys

from PyQt5.QtCore import QObject, pyqtSignal

# 通信失联统一处理的重入保护：mark_dead → update → on_device_selected 链中
# 若再次发生通信异常，避免递归进入本处理。
_comm_failure_handling = False


def handle_comm_failure():
    """通信失联统一兜底：拉黑当前设备并触发刷新（崩溃拦截用）。

    设备切蓝牙/拔线后，UI 槽内任何 usb_send 都会快速失败并抛 RuntimeError
    （掉线路径 `retries=3`，最坏阻塞 ≈3×0.6s 后失败，见 util.hid_send）。本函数把它
    转成"设备已断开"的常规处理：拉黑 + 刷新列表 + 回退到无设备状态，而不是让
    异常冒泡导致 PyQt5 中止进程。
    """
    global _comm_failure_handling
    if _comm_failure_handling:
        return
    _comm_failure_handling = True
    try:
        inst = Autorefresh.instance
        if inst is None or inst.current_device is None:
            return
        path = inst.current_device.desc.get("path")
        try:
            inst.current_device.close()
        except Exception:
            pass
        inst.current_device = None
        try:
            # 同步清掉后台线程的 current_device 引用，避免探测/扫描碰已关闭设备
            inst.thread.set_device(None)
        except Exception:
            pass
        if path is not None:
            try:
                inst.thread.mark_dead(path)
            except Exception:
                pass
    finally:
        _comm_failure_handling = False


class AutorefreshLocker:

    def __init__(self, autorefresh):
        self.autorefresh = autorefresh

    def __enter__(self):
        self.autorefresh._lock()

    def __exit__(self):
        self.autorefresh._unlock()


class Autorefresh(QObject):

    instance = None
    devices_updated = pyqtSignal(object, bool)
    # 透传后台线程的 comm_dead_now：本次刷新有新设备被判死（拉黑）时发出。
    comm_dead_now = pyqtSignal()

    def __init__(self):
        super().__init__()

        self.devices = []
        self.current_device = None

        Autorefresh.instance = self

        if sys.platform == "emscripten":
            from autorefresh.autorefresh_thread_web import AutorefreshThreadWeb

            self.thread = AutorefreshThreadWeb()
        elif sys.platform.startswith("win"):
            from autorefresh.autorefresh_thread_win import AutorefreshThreadWin

            self.thread = AutorefreshThreadWin()
        else:
            from autorefresh.autorefresh_thread import AutorefreshThread

            self.thread = AutorefreshThread()

        self.thread.devices_updated.connect(self.on_devices_updated)
        self.thread.comm_dead_now.connect(self.comm_dead_now)
        self.thread.start()

    def _lock(self):
        self.thread.lock()

    def _unlock(self):
        self.thread.unlock()

    @classmethod
    def lock(cls):
        return AutorefreshLocker(cls.instance)

    def load_dummy(self, data):
        self.thread.load_dummy(data)

    def sideload_via_json(self, data):
        self.thread.sideload_via_json(data)

    def load_via_stack(self, data):
        self.thread.load_via_stack(data)

    def select_device(self, idx):
        if self.current_device is not None:
            self.current_device.close()
        self.current_device = None
        if idx >= 0:
            self.current_device = self.devices[idx]

        if self.current_device is not None:
            # 黑名单里的设备不再尝试打开：它刚被判死（打开失败/无响应），此刻
            # 再开一次必然同样失败，而失败分支又会触发 mark_dead + 硬刷新，
            # 形成"刷新 → 打开失败 → 刷新"的同步递归。设备在下次刷新探测成功
            # 复活后才会重新出现在 devices 里（届时不在黑名单，正常打开）。
            try:
                path = self.current_device.desc["path"]
            except Exception:
                path = None
            if path is not None and path in self.thread.dead_paths:
                logging.info("Skipping open of blacklisted device %s", path)
                self.current_device = None
                self.thread.set_device(None)
                return
            try:
                if self.current_device.sideload:
                    self.current_device.open(self.thread.sideload_json)
                elif self.current_device.via_stack:
                    self.current_device.open(self.thread.via_stack_json["definitions"][self.current_device.via_id])
                else:
                    self.current_device.open(None)
            except Exception:
                # 打开/通信失败（设备已切走、拔线或固件无响应）：放弃该设备，
                # 回退到"无设备"状态，避免半开实例残留导致后续 rebuild 二次异常。
                dead_path = None
                try:
                    if self.current_device is not None:
                        dead_path = self.current_device.desc["path"]
                        self.current_device.close()
                except Exception:
                    dead_path = None
                self.current_device = None
                if dead_path is not None:
                    # 打开失败就是"已失联"的明确证据：立即拉黑并刷新列表，
                    # 让设备从下拉框即时消失（不等心跳累计 3 次失败）。
                    # mark_dead 自带重入闸门，其触发的刷新不会再回到本函数。
                    self.thread.mark_dead(dead_path)
                raise
        self.thread.set_device(self.current_device)

    def on_devices_updated(self, devices, changed):
        self.devices = devices
        self.devices_updated.emit(devices, changed)

    def update(self, quiet=True, hard=False):
        self.thread.update(quiet, hard)
