import json
import time
import sys

if sys.platform == "emscripten":
    class RLock:
        def __enter__(self):
            pass
        def __exit__(self, *args):
            pass
else:
    from multiprocessing import RLock

from PyQt5.QtCore import pyqtSignal, QThread

from util import find_vial_devices
from vial_device import VialDevice

# 探测（发 get_unlock_status）只在"刷新设备列表"时进行，且只针对黑名单
# 内设备（GUI 已不连它们，不存在与 UI 线程并发收发同一 raw HID 端点的问题）。
# 正常使用时绝不后台发命令：UI 线程正在轮询（矩阵页 20ms / analog 连续读）时
# 后台再收发同一端点会串包——矩阵页读到探测响应会"全部键触发"，definition
# 分块读取会拿到残缺数据导致 lzma 解压失败。
# 当前连接设备的失联判定：UI 侧命令无响应 → 失败累计置 comm_dead → 刷新时被
# scan_comm_dead 拉黑 → 设备从列表消失；下一次刷新时探测它（已在黑名单）决定
# 是否复活回列表。


class AutorefreshThread(QThread):

    devices_updated = pyqtSignal(object, bool)
    # 本次刷新有新设备被判 comm_dead（拉黑，即将从设备列表消失）时发出。
    # 主窗口收到该信号后弹「键盘无响应」提示——与「设备无响应的刷新」同一时刻。
    comm_dead_now = pyqtSignal()

    def __init__(self):
        super().__init__()

        self.current_device = None
        self.devices = []
        self.locked = False
        self.mutex = RLock()

        self.sideload_json = None
        self.sideload_vid = self.sideload_pid = -1
        # create empty VIA definitions. Easier than setting it to none and handling a bunch of exceptions
        self.via_stack_json = {"definitions": {}}

        # 通信失效黑名单：设备枚举仍在（USB 保持枚举），但固件切蓝牙后对 USB
        # raw HID 命令不再回包，GUI 判其"已断开"。黑名单内 path 从设备列表过滤，
        # 由 _probe_devices() 低频发 get_unlock_status 尝试复活——切回 USB 后
        # 命令恢复响应，设备自动回到列表。
        # dead_failures 记录"当前已连接设备"在空闲期连续探测失败次数，达到阈值
        # 进黑名单（主动心跳：GUI 空闲无轮询时，也能在数秒内把切走的设备自动
        # 移除；UI 活跃期间后台探测让路，失联改由 UI 线程失败累计识别）。
        self.dead_paths = set()
        self.dead_failures = {}

    def run(self):
        # 后台不再做周期心跳探测：探测只在 update()（刷新）时进行，
        # 正常工作时（UI 线程收发命令）后台绝缘发命令，杜绝端点串包。
        while True:
            self.update()
            time.sleep(1)

    def lock(self):
        with self.mutex:
            self.locked = True

    def unlock(self):
        with self.mutex:
            self.locked = False

    # note that this method is called from both inside and outside of this thread
    def _record_probe_result(self, path, current_path, ok, blacklist_now=False):
        """探测结果状态机（纯逻辑，便于单测）：更新黑名单/失败计数。

        blacklist_now=True 表示有明确证据通信已失效（如打开设备/发送命令直接
        失败），不等心跳累计阈值立即拉黑——设备即时从列表消失。
        返回 True 表示设备列表需要刷新（复活重新列出，或判死移除）。
        """
        with self.mutex:
            revived = False
            newly_dead = False
            if ok:
                # 通信恢复：清零失败计数；若在黑名单则复活
                self.dead_failures.pop(path, None)
                if path in self.dead_paths:
                    self.dead_paths.discard(path)
                    revived = True
            elif blacklist_now:
                # 打开/通信直接失败：立即判死（不限 current_path，因为这是
                # 明确证据而非猜测）
                if path not in self.dead_paths:
                    self.dead_paths.add(path)
                    self.dead_failures.pop(path, None)
                    newly_dead = True
            else:
                # 通信失败：仅对"当前已连接"设备累计并进黑名单
                if path == current_path:
                    fails = self.dead_failures.get(path, 0) + 1
                    self.dead_failures[path] = fails
                    if fails >= 3:
                        self.dead_paths.add(path)
                        self.dead_failures.pop(path, None)
                        newly_dead = True
            return revived or newly_dead

    def mark_dead(self, path):
        """有明确通信失败证据时立即把设备拉黑并从列表移除（打开失败等）。

        供主线程（select_device 打开失败）和探测线程共同调用：一旦确认设备
        已失联（如切蓝牙后固件对 USB 不再回包），立刻让它从下拉框消失，
        而不是等心跳累计 3 次失败。
        首次把设备拉黑后先触发硬刷新，**刷新完成后**才发 comm_dead_now
        （弹窗时机 = 设备无响应的刷新之后，不会被刷新流程盖掉）。
        """
        if self._record_probe_result(path, path, False, blacklist_now=True):
            self.update(hard=True)
            self.comm_dead_now.emit()

    def _probe_devices(self):
        """刷新时的黑名单复活探测：决定哪些设备可以回到可用列表。

        只探测已判死（黑名单）的设备——这些设备 GUI 已不连接，后台发命令不会
        与 UI 线程争用同一 raw HID 端点。当前连接设备**不主动探测**：它正在被
        UI 线程使用（矩阵页轮询、analog 连续读、改键等），此时后台再发命令会
        串包（矩阵页全触发 / definition 分块读取残缺 → lzma 解压失败）。

        探测成功 → 移出黑名单，设备回到列表；失败 → 维持黑名单（仍视为断开）。
        """
        with self.mutex:
            probe_paths = set(self.dead_paths)

        if not probe_paths:
            return False

        revived = False
        for path in list(probe_paths):
            dev = None
            ok = False
            try:
                dev = VialDevice({"path": path})
                dev.open()
                # get_unlock_status 命令：FE 04，pad 到 MSG_LEN(32)
                dev.send(b"\xFE\x04".ljust(32, b"\x00"))
                ok = bool(dev.recv(32, timeout_ms=100))
            except Exception:
                ok = False
            finally:
                if dev is not None:
                    try:
                        dev.close()
                    except Exception:
                        pass

            if self._record_probe_result(path, path, ok):
                revived = True

        # 有设备复活：它已从黑名单移除，本轮的过滤结果需要重算一次
        return revived

    def scan_comm_dead(self):
        """把已 open 设备里 keyboard.comm_dead 的 path 收进黑名单（被动兜底）。

        轮询/交互期间 Keyboard.usb_send 连续失败会把 comm_dead 置 True；
        这里把置位设备即时收进黑名单（列表消失）。返回 True 表示有新设备
        被拉黑（需要刷新列表）。
        """
        added = False
        with self.mutex:
            if self.current_device is not None:
                kb = getattr(self.current_device, "keyboard", None)
                if kb is not None and getattr(kb, "comm_dead", False):
                    if self.current_device.desc["path"] not in self.dead_paths:
                        self.dead_paths.add(self.current_device.desc["path"])
                        added = True
        return added

    # note that this method is called from both inside and outside of this thread
    def update(self, quiet=True, hard=False):
        # 本次刷新是否有设备被拉黑（弹窗条件）：弹窗必须在刷新**完成后**才弹，
        # 否则枚举耗时数秒时弹窗先出、刷新后到，观感上像"被刷新刷掉"。
        # locked 期间不弹（lock_ui 锁定中，UI 不可交互）。
        comm_dead_added = False
        try:
            # if lock()ed then just do nothing
            with self.mutex:
                if self.locked:
                    return
                # can be modified out of mutex so create local copies here
                via_stack_json = self.via_stack_json
                sideload_vid = self.sideload_vid
                sideload_pid = self.sideload_pid

            # 收集已 open 设备里通信失效（comm_dead）的 path 进黑名单。
            # （正常工作时命令无响应 → 失败累计置 comm_dead → 本次刷新把它拉黑，
            #  设备随即从列表消失；不需要后台主动探测。）
            comm_dead_added = self.scan_comm_dead()

            # 刷新时顺带探测黑名单设备是否复活（只探黑名单，不与 UI 争用端点）
            revived = self._probe_devices()

            # this can take a long (~seconds) time on Windows, so run outside of mutex
            # to make sure calling lock() and unlock() is instant
            new_devices = find_vial_devices(via_stack_json, sideload_vid, sideload_pid, quiet=quiet)

            # 过滤黑名单：通信失效的设备不列入设备列表（GUI 认为已断开，不再向它发命令）
            with self.mutex:
                dead_paths = set(self.dead_paths)
            if dead_paths:
                new_devices = [d for d in new_devices if d.desc["path"] not in dead_paths]

            # 本轮探测到复活：该设备刚从黑名单移除，但上面过滤时它还在黑名单里，
            # 需要按新的黑名单重算一次，让它立即回到列表
            if revived:
                with self.mutex:
                    dead_paths = set(self.dead_paths)
                if dead_paths:
                    new_devices = [d for d in new_devices if d.desc["path"] not in dead_paths]

            # this is fast again but discard results if we got lock()ed in between
            with self.mutex:
                if self.locked:
                    return

                # if the set of the devices didn't change at all, don't need to update the combobox
                old_paths = set(d.desc["path"] for d in self.devices)
                new_paths = set(d.desc["path"] for d in new_devices)
                if old_paths == new_paths and not hard:
                    return

                # trigger update and report whether a hard-reload is needed (if current device went away)
                self.devices = new_devices
                old_path = "blank"
                if self.current_device is not None:
                    old_path = self.current_device.desc["path"]

                self.devices_updated.emit(new_devices, (old_path not in new_paths) or hard)
        finally:
            # 刷新已完成（无论列表是否变化）：只要本次判死了新设备就发 comm_dead_now。
            # 列表无变化的提前 return 也在此汇合——拉黑这件事本身就要弹窗，
            # 不能被"设备列表没变"短路。信号经 Qt 队列投递到主线程，弹窗在
            # 刷新流程结束后才真正显示，不会被刷新盖掉。
            if comm_dead_added:
                self.comm_dead_now.emit()

    def load_dummy(self, data):
        with self.mutex:
            self.sideload_json = json.loads(data)
            self.sideload_vid = self.sideload_pid = 0
        self.update()

    def sideload_via_json(self, data):
        with self.mutex:
            self.sideload_json = json.loads(data)
            self.sideload_vid = int(self.sideload_json["vendorId"], 16)
            self.sideload_pid = int(self.sideload_json["productId"], 16)
        self.update()

    def load_via_stack(self, data):
        with self.mutex:
            self.via_stack_json = json.loads(data)

    def set_device(self, current_device):
        with self.mutex:
            self.current_device = current_device
