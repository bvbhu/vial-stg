import time
import win32gui, win32con, win32api
import win32gui_struct

from autorefresh.autorefresh_thread import AutorefreshThread


GUID_DEVINTERFACE_USB_DEVICE = "{A5DCBF10-6530-11D2-901F-00C04FB951ED}"
DEVICE_NOTIFY_ALL_INTERFACE_CLASSES = 4

g_device_changes = 0


def device_changed(hwnd, msg, wp, lp):
    global g_device_changes
    if wp in [win32con.DBT_DEVICEARRIVAL, win32con.DBT_DEVICEREMOVECOMPLETE]:
        g_device_changes += 1
    # 必须显式返回 int：pywin32 312 的 C 层 wndproc 会把 handler 返回值经
    # PyWinObject_AsPARAM 转 LRESULT，返回 None 会每条消息刷一行
    # "TypeError: WPARAM is simple, so must be an int object (got NoneType)"
    # （旧版 pywin32 静默把 None 当 0，上游代码因此在 312 上开始刷屏）。
    return 0


class AutorefreshThreadWin(AutorefreshThread):

    def run(self):
        global g_device_changes

        # code based on:
        # - https://github.com/libsdl-org/SDL/blob/7b3449b89f0625e4603f5d8681e2bac1f51a9386/src/hidapi/SDL_hidapi.c
        # - https://github.com/vmware-archive/salt-windows-install/blob/master/deps/salt/python/App/Lib/site-packages/win32/Demos/win32gui_devicenotify.py
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = "VIAL_DEVICE_DETECTION"
        wc.lpfnWndProc = { win32con.WM_DEVICECHANGE: device_changed }
        try:
            class_atom = win32gui.RegisterClass(wc)
        except Exception as e:
            # 1410 = ERROR_CLASS_ALREADY_EXISTS。窗口类名是进程级全局的，同进程内
            # 再启一个检测线程（检测线程重启、或测试里创建多个主窗口）就会撞码；
            # 类已经在了，直接复用，别让它冒泡成 Qt 事件循环里的未捕获异常。
            if getattr(e, "winerror", None) != 1410:
                raise
            class_atom = 0
        hwnd = win32gui.CreateWindowEx(0, "VIAL_DEVICE_DETECTION", None, 0, 0, 0, 0, 0, win32con.HWND_MESSAGE, None, None, None)

        hdev = win32gui.RegisterDeviceNotification(
            hwnd,
            win32gui_struct.PackDEV_BROADCAST_DEVICEINTERFACE(GUID_DEVINTERFACE_USB_DEVICE),
            win32con.DEVICE_NOTIFY_WINDOW_HANDLE | DEVICE_NOTIFY_ALL_INTERFACE_CLASSES
        )

        while True:
            for x in range(100):
                win32gui.PumpWaitingMessages()
                time.sleep(0.01)

            if g_device_changes > 0:
                g_device_changes = 0
                self.update()
