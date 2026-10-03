# SPDX-License-Identifier: GPL-2.0-or-later
import ssl
import certifi
import logging
import os

if ssl.get_default_verify_paths().cafile is None:
    os.environ['SSL_CERT_FILE'] = certifi.where()

import traceback

from PyQt5 import QtWidgets, QtCore
from PyQt5.QtCore import pyqtSignal

from fbs_runtime.application_context import cached_property
from fbs_runtime.application_context.PyQt5 import ApplicationContext

import sys

from constants import ORG_NAME
from main_window import MainWindow


# http://timlehr.com/python-exception-hooks-with-qt-message-box/
from util import init_logger


def show_exception_box(log_msg):
    if QtWidgets.QApplication.instance() is not None:
        errorbox = QtWidgets.QMessageBox()
        errorbox.setText(log_msg)
        errorbox.exec_()


class UncaughtHook(QtCore.QObject):
    _exception_caught = pyqtSignal(object)

    def __init__(self, *args, **kwargs):
        super(UncaughtHook, self).__init__(*args, **kwargs)

        # this registers the exception_hook() function as hook with the Python interpreter
        sys._excepthook = sys.excepthook
        sys.excepthook = self.exception_hook

        # connect signal to execute the message box function always on main thread
        self._exception_caught.connect(show_exception_box)

    def exception_hook(self, exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            # ignore keyboard interrupt to support console applications
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        if issubclass(exc_type, RuntimeError) and "failed to communicate" in str(exc_value):
            # 通信失联（设备切蓝牙/拔线后命令无响应）：预期场景而非缺陷，
            # 不弹窗、不打印无意义堆栈、不 abort 进程——转成"设备已断开"
            # 的常规处理（拉黑 + 刷新列表）。注意不能调用默认 excepthook，
            # 否则控制台会刷出完整 traceback（无助于定位，纯噪音）。
            logging.warning("Communication failure intercepted: %s", exc_value)
            try:
                from autorefresh.autorefresh import handle_comm_failure
                handle_comm_failure()
            except Exception:
                logging.exception("handle_comm_failure failed")
            return

        log_msg = '\n'.join([''.join(traceback.format_tb(exc_traceback)),
                             '{0}: {1}'.format(exc_type.__name__, exc_value)])

        # trigger message box show
        self._exception_caught.emit(log_msg)
        # 其余异常保持默认行为：打印堆栈（供调试定位）
        sys._excepthook(exc_type, exc_value, exc_traceback)

class VialApplicationContext(ApplicationContext):
    @cached_property
    def app(self):
        # Override the app definition in order to set WM_CLASS.
        result = QtWidgets.QApplication(sys.argv)
        result.setApplicationName(self.build_settings["app_name"])
        # 组织名用于 QSettings 的默认归属路径；仓库没有自己的域名，所以不设
        # organizationDomain（设了的话 Qt 会用它派生组织名，反而覆盖上面的值）。
        result.setOrganizationName(ORG_NAME)

        #TODO: Qt sets applicationVersion on non-Linux platforms if the exe/pkg metadata is correctly configured.
        # https://doc.qt.io/qt-5/qcoreapplication.html#applicationVersion-prop
        # Verify it is, and only set manually on Linux.
        #if sys.platform.startswith("linux"):
        result.setApplicationVersion(self.build_settings["version"])
        return result

if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == "--linux-recorder":
        from linux_keystroke_recorder import linux_keystroke_recorder

        linux_keystroke_recorder()
    else:
        appctxt = VialApplicationContext()       # 1. Instantiate ApplicationContext
        init_logger()
        qt_exception_hook = UncaughtHook()
        window = MainWindow(appctxt)
        window.show()
        exit_code = appctxt.app.exec_()      # 2. Invoke appctxt.app.exec_()
        sys.exit(exit_code)
