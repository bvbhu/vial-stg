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


def _register_fonts(app):
    """注册 Latin 主字体 + CJK 逐字回退，复刻桌面端字体结构。

    桌面端（main.py）不设字体、不补偿：用 Qt 默认（Windows: Segoe UI @9pt），
    CJK 由系统回退（微软雅黑）按 Unicode 码位补绘，页面与字大小由系统字体决定。
    Qt WebAssembly 无系统字体，若直接把 CJK 字体（思源黑体 usWin≈1.448em）设为
    应用字体，同字号下行高比西文字体大 ~25%，整页被等比放大、被迫缩字号补偿，
    字被缩成 ~6pt、明显小于桌面端。本函数改为复刻桌面结构：随包分发 Inter
    （SIL OFL 1.1，Latin，~1.21em≈Segoe UI 1.189em）作主字体、Noto Sans SC 作
    CJK 回退；setFamilies 让 Qt 用主字体行高（已验证：主字体行高，非各族最大值）、
    CJK 码位从回退字体取字同字号绘出。字号由 main() 设为 9pt（桌面端 Qt 默认），
    不再补偿——主字体行高≈Segoe UI@9pt，页面与字大小均贴近桌面端。
    build.sh 把两字体拷进 preload 的 /usr/local/fonts/，许可证见 src/fonts/OFL.txt。
    注册失败只影响显示，绝不阻断启动。需要 app.get_resource 已就绪。
    """
    try:
        latin_id = QtGui.QFontDatabase.addApplicationFont(app.get_resource("fonts/Inter-Regular.ttf"))
        latin_fams = QtGui.QFontDatabase.applicationFontFamilies(latin_id) if latin_id >= 0 else []
        cjk_id = QtGui.QFontDatabase.addApplicationFont(app.get_resource("fonts/NotoSansSC-Regular.otf"))
        cjk_fams = QtGui.QFontDatabase.applicationFontFamilies(cjk_id) if cjk_id >= 0 else []
        if not latin_fams:
            # Latin 主字体缺失时退回纯 CJK（保底：至少中文不渲染成方框）
            print("webmain: Latin font registration failed (id=%d); CJK fallback only" % latin_id)
            if cjk_fams:
                f = QtGui.QFont(app.font()); f.setFamily(cjk_fams[0]); app.setFont(f)
                app._cjk_font_id = cjk_id
            return
        app._latin_font_id = latin_id  # 持有引用，防止字体数据库句柄被回收
        if cjk_fams:
            app._cjk_font_id = cjk_id
        families = [latin_fams[0]] + (cjk_fams[:1] if cjk_fams else [])
        font = QtGui.QFont(app.font())
        font.setFamilies(families)   # Latin 主字体行高 + CJK 逐字回退，同字号不缩放
        app.setFont(font)
        print("webmain: fonts active: %r + %r (%.1fpt, line height %dpx)" % (
            latin_fams[0], cjk_fams[0] if cjk_fams else None,
            font.pointSizeF(), QtGui.QFontMetrics(font).height()))
    except Exception as e:
        print("webmain: font setup error: %r" % e)


def main(app):
    # 9pt = 桌面端 Qt 默认字号（Windows: Segoe UI @9pt）。_register_fonts 用
    # Inter(~1.21em≈Segoe UI) 作主字体、Noto 作 CJK 回退、不补偿，页面与字大小
    # 均贴近桌面端，避免旧行为把 CJK 字体(1.448em)设主后被迫缩字号。
    font = app.font()
    font.setPointSize(9)
    app.setFont(font)

    app.get_resource = web_get_resource
    _register_fonts(app)
    with open(app.get_resource("build_settings.json"), "r") as inf:
        app.build_settings = json.loads(inf.read())
    qt_exception_hook = UncaughtHook()

    # Not sure of the best way to do this.
    global window
    window = MainWindow(app)
    window.show()

    app.processEvents()
