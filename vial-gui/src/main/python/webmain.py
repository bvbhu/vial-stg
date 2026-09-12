# SPDX-License-Identifier: GPL-2.0-or-later
import os

import traceback

from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtCore import pyqtSignal

import sys
import json

from main_window import MainWindow


# http://timlehr.com/python-exception-hooks-with-qt-message-box/
from util import init_logger

window = None

def show_exception_box(log_msg):
    if QtWidgets.QApplication.instance() is not None:
        global errorbox

        errorbox = QtWidgets.QMessageBox()
        errorbox.setText(log_msg)
        errorbox.setModal(True)
        errorbox.show()


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
        else:
            log_msg = '\n'.join([''.join(traceback.format_tb(exc_traceback)),
                                 '{0}: {1}'.format(exc_type.__name__, exc_value)])

            # trigger message box show
            self._exception_caught.emit(log_msg)
        sys._excepthook(exc_type, exc_value, exc_traceback)


def web_get_resource(name):
    return "/usr/local/" + name


def _register_cjk_font(app):
    """注册随包分发的中文字体并设为应用字体。

    Qt WebAssembly 不带系统字体，i18n 中文界面会整体渲染成方框。
    build.sh 把完整的 Noto Sans SC（思源黑体的 Google 发行名，SIL OFL 1.1，
    未修改原版，见仓库 src/fonts/OFL.txt）拷进 preload 的 /usr/local/fonts/。
    注册失败只影响中文显示，绝不阻断启动。需要 app.get_resource 已就绪。
    """
    try:
        path = app.get_resource("fonts/NotoSansSC-Regular.otf")
        fid = QtGui.QFontDatabase.addApplicationFont(path)
        families = QtGui.QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        if not families:
            print("webmain: CJK font registration failed (id=%d)" % fid)
            return
        app._cjk_font_id = fid  # 持有引用，防止字体数据库句柄被回收
        font = app.font()
        font.setFamily(families[0])
        app.setFont(font)
        print("webmain: CJK font active: %s" % families[0])
    except Exception as e:
        print("webmain: CJK font setup error: %r" % e)


def main(app):
    font = app.font()
    font.setPointSize(10)
    app.setFont(font)

    app.get_resource = web_get_resource
    _register_cjk_font(app)
    with open(app.get_resource("build_settings.json"), "r") as inf:
        app.build_settings = json.loads(inf.read())
    qt_exception_hook = UncaughtHook()

    # Not sure of the best way to do this.
    global window
    window = MainWindow(app)
    window.show()

    app.processEvents()
