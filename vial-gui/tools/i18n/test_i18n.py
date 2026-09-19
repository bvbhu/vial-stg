# -*- coding: utf-8 -*-
"""i18n 端到端实测：离屏启动 MainWindow，断言真实控件文字随语言切换。

  QT_QPA_PLATFORM=offscreen python test_i18n.py zh
  QT_QPA_PLATFORM=offscreen python test_i18n.py en

说明：各编辑器继承自 BasicEditor，而 BasicEditor 是 **QVBoxLayout**（不是 QWidget），
所以对它们必须沿 layout 的 item 递归取控件，findChildren 是拿不到的。
"""
import os
import sys
import traceback

EXPECT = (sys.argv[1] if len(sys.argv) > 1 else "zh").lower()
os.environ["VIAL_LANG"] = EXPECT
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

def _find_repo(start):
    """向上找到含 src/main/python 的目录，即 vial-gui 仓库根（脚本可放任意子目录）。"""
    d = start
    while True:
        if os.path.isdir(os.path.join(d, "src", "main", "python")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            raise RuntimeError("cannot locate vial-gui repo root from " + start)
        d = parent


ROOT = _find_repo(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "main", "python"))
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PyQt5 import QtCore                                            # noqa: E402
from PyQt5.QtWidgets import (QWidget, QLayout, QLabel, QCheckBox, QRadioButton,  # noqa: E402
                              QGroupBox, QPushButton, QToolButton)
import main as M                                                    # noqa: E402
import i18n                                                         # noqa: E402
from util import tr                                                 # noqa: E402

PROBES = {
    "menu_File":        ("文件", "File"),
    "menu_Keyboard":    ("键盘布局", "Keyboard layout"),
    "menu_Security":    ("安全", "Security"),
    "menu_Theme":       ("主题", "Theme"),
    "menu_Language":    ("语言", "Language"),
    "menu_About":       ("关于", "About"),
    "refresh_btn":      ("刷新", "Refresh"),
    "no_devices":       ("未检测到设备", "No devices detected"),
    "rgb_underglow":    ("底灯灯效", "Underglow Effect"),
    "rgb_backlight":    ("按键灯亮度", "Backlight Brightness"),
    "rgb_speed":        ("速度", "RGB Speed"),
    "matrix_unlock":    ("解锁", "Unlock"),
    "matrix_before":    ("测试前请先解锁键盘", "Unlock the keyboard before testing"),
    "qmk_save":         ("保存", "Save"),
    "qmk_undo":         ("撤销", "Undo"),
    "flash_select":     ("选择文件...", "Select file..."),
    "flash_flash":      ("刷写固件", "Flash"),
    "flash_restore":    ("刷写固件后恢复当前配置", "Restore current layout after flashing"),
    "analog_rt":        ("启用 Rapid Trigger (RT)", "Enable Rapid Trigger (RT)"),
    "analog_panel":     ("每键参数", "Per-key settings"),
    "analog_actpoint":  ("触发点", "Actuation point"),
    "analog_release":   ("断开点", "Release point"),
    "analog_calreset":  ("重置为全局默认", "Reset to global default"),
    "analog_resetall":  ("全局重置（出厂）", "Reset all (factory)"),
    "ko_labels":        ("触发按键", "Trigger"),
    "ko_opts":          ("选项", "Options"),
    "ko_checkbox":      ("在触发按键按下时激活", "Activate when the trigger key is pressed down"),
    "ko_enableall":     ("启用全部", "Enable all"),
    "td_ontap":         ("短按", "On tap"),
    "td_term":          ("短按判定时间", "Tapping term (ms)"),
    "td_hint":          ("在键位映射中使用", "Use <code>TD("),
    "combo_output":     ("输出按键", "Output key"),
    "combo_key":        ("按键 1", "Key 1"),
    "arep_lastkey":     ("上次按键", "Last key"),
    "arep_altkey":      ("替代键", "Alt key"),
    "arep_default":     ("默认使用此替代键", "Default to this alt key"),
    "arep_bidir":       ("交换上次按键和替代键也生效", "Bidirectional"),
    "arep_handed":      ("不区分左右修饰键", "Ignore mod handedness"),
    "tab_altrepeat":    ("替代重复键", "Alt Repeat Key"),
    "qmk_tab_magic":    ("魔法按键", "Magic"),
    "qmk_tab_taphold":  ("短按-按住", "Tap-Hold"),
    "qmk_swapcaps":     ("交换Caps键和左Ctrl键", "Swap Caps Lock and Left Control"),
    "qmk_backslash":    ("交换\\键和退格键", "Swap \\ and Backspace"),
    "qmk_grave":        ("Alt键按下时发送Esc键", "Always send Escape if Alt is pressed"),
    "qmk_retro":        ("按住后无动作恢复短按", "Retro Tapping"),
    "qmk_flowtap":      ("连打短按（抑制按住判定）", "Flow Tap"),
    "qmk_chordal":      ("和弦按住（同手判定为短按）", "Chordal Hold"),
    "qmk_holdpress":    ("按下其他按键时判定为按住", "Hold On Other Key Press"),
    "qmk_quicktap":     ("快速短按判定时间（0 禁用自动重复）", "Quick Tap Term"),
    "qmk_autoshift":    ("对符号键不启用", "Do not Auto Shift special keys"),
    "qmk_oneshot":      ("触发粘滞键的按下次数（再次按下释放）",
                         "Tapping this number of times"),
    "qmk_mouse":        ("滚轮移动间隔（毫秒）", "Time between wheel movements"),
    "qmk_reset":        ("确定要把所有设置恢复为默认值吗？", "Reset all settings to default values?"),
    "kcpanel_bluetooth": ("无线", "Wireless"),
    "kcpanel_basic":     ("基本按键", "Basic"),
    "kcpanel_layers":    ("层控制", "Layers"),
    "kcpanel_any":       ("任意", "Any"),
    "textbox_apply":     ("应用", "Apply"),
    "textbox_cancel":    ("取消", "Cancel"),
    "unlocker_hold":     ("按住以下按键", "Press and hold the following keys"),
    "anydlg_arb":        ("输入任意键值", "Enter an arbitrary keycode"),
    "anydlg_value":      ("对应键值", "Computed value"),
}
IDX = 0 if EXPECT == "zh" else 1
results, fails, keep = [], [], []
KINDS = (QLabel, QPushButton, QCheckBox, QRadioButton, QGroupBox, QToolButton)


def check(name, blob):
    want = PROBES[name][IDX]
    ok = blob is not None and want in str(blob)
    results.append((name, want, ok, str(blob)[:52] if blob is not None else "None"))
    if not ok:
        fails.append("%s: 期望 %r / 实际 %r" % (name, want, str(blob)[:90]))


def text_of(w):
    """取一个控件的显示文字。

    三个坑：
      - QGroupBox 没有 text()，标题在 title() 上；
      - SquareButton 把 text 覆盖成了**实例属性**（它内部用 QLabel 做自动换行），
        所以对它的 .text 加括号调用会抛 TypeError: 'str' object is not callable；
      - 普通控件才是 text() 方法。
    """
    t = getattr(w, "text", None)
    try:
        t = t() if callable(t) else t
    except Exception:
        t = None
    if not t and hasattr(w, "title"):
        try:
            t = w.title()
        except Exception:
            t = None
    return str(t) if t else ""


def texts_of(widgets):
    return " || ".join([t for t in (text_of(w) for w in widgets) if t])


def widgets_in_layout(layout):
    """沿 layout 的 item 递归取控件（编辑器都是 QVBoxLayout，没有宿主 widget）。"""
    found = []
    for i in range(layout.count()):
        it = layout.itemAt(i)
        if it is None:
            continue
        w = it.widget()
        if w is not None:
            found.append(w)
            for k in KINDS:
                found.extend(w.findChildren(k))
        sub = it.layout()
        if sub is not None:
            found.extend(widgets_in_layout(sub))
    return found


def pool(obj):
    """对 QWidget 用 findChildren；对 QLayout（各编辑器）递归 item。"""
    if obj is None:
        return ""
    keep.append(obj)
    if isinstance(obj, QLayout):
        return texts_of(widgets_in_layout(obj))
    ws = [obj]
    for k in KINDS:
        ws.extend(obj.findChildren(k))
    return texts_of(ws)


try:
    print("== i18n.get_language() -> %s (期望 %s) ==" % (i18n.get_language(), EXPECT))
    assert i18n.get_language() == EXPECT, "语言与 VIAL_LANG 不一致"

    appctx = M.VialApplicationContext()
    M.init_logger()
    win = M.MainWindow(appctx)
    win.show()
    QtCore.QTimer.singleShot(400, appctx.app.quit)
    appctx.app.exec_()

    menus = [a.text() for a in win.menuBar().actions()]
    print("menuBar:", menus)
    for name, idx in (("menu_File", 0), ("menu_Keyboard", 1), ("menu_Security", 2),
                      ("menu_Theme", 3), ("menu_Language", 4), ("menu_About", 5)):
        check(name, menus[idx] if idx < len(menus) else "<菜单缺失>")

    check("refresh_btn", win.btn_refresh_devices.text())
    check("no_devices", win.lbl_no_devices.text())

    # 各编辑器：它们是 QVBoxLayout
    rgbp = pool(win.rgb_configurator)
    check("rgb_underglow", rgbp)
    check("rgb_backlight", rgbp)
    check("rgb_speed", rgbp)
    mtp = pool(win.matrix_tester)
    check("matrix_unlock", mtp)
    check("matrix_before", mtp)
    qsp = pool(win.qmk_settings)
    check("qmk_save", qsp)
    check("qmk_undo", qsp)
    ffp = pool(win.firmware_flasher)
    check("flash_select", ffp)
    check("flash_flash", ffp)
    check("flash_restore", ffp)
    atp = pool(win.analog_tab)
    for name in ("analog_rt", "analog_panel",
                 "analog_actpoint", "analog_release", "analog_calreset", "analog_resetall"):
        check(name, atp)

    # 条目控件（无设备时不随主窗口创建，单独实例化；它们的 w2 是真 QWidget）
    from editor.key_override import KeyOverrideEntryUI, LayersUI
    from editor.tap_dance import TapDanceEntryUI
    from editor.combos import ComboEntryUI
    from editor.alt_repeat_key import AltRepeatKeyEntryUI
    from widgets.square_button import SquareButton

    ko = KeyOverrideEntryUI(0)
    keep.append(ko)
    kop = pool(ko.w2)
    check("ko_labels", kop)
    check("ko_opts", kop)
    check("ko_checkbox", kop)
    check("ko_enableall", pool(LayersUI()))
    print("editor tabs:", " || ".join(win.tabs.tabText(i) for i in range(win.tabs.count())))
    check("tab_altrepeat", " || ".join(win.tabs.tabText(i)
                                        for i in range(win.tabs.count())))
    td = TapDanceEntryUI(0)
    keep.append(td)
    tdp = pool(td.w2)
    check("td_ontap", tdp)
    check("td_term", tdp)
    check("td_hint", tdp)
    ce = ComboEntryUI(0)
    keep.append(ce)
    cep = pool(ce.w2)
    check("combo_output", cep)
    check("combo_key", cep)
    ar = AltRepeatKeyEntryUI(0)
    keep.append(ar)
    arp = pool(ar.w2)
    check("arep_lastkey", arp)
    check("arep_altkey", arp)
    check("arep_default", arp)
    check("arep_bidir", arp)
    check("arep_handed", arp)

    from tabbed_keycodes import FilteredTabbedKeycodes
    tk = FilteredTabbedKeycodes(parent=None)
    tabs = " || ".join(tk.tabText(i) for i in range(tk.count()))
    print("keycode tabs:", tabs)
    check("kcpanel_bluetooth", tabs)
    check("kcpanel_basic", tabs)
    check("kcpanel_layers", tabs)
    anybtns = texts_of([b for b in tk.findChildren(SquareButton)
                        if text_of(b) in ("Any", "任意")])
    print("Any 按钮:", repr(anybtns), "| SquareButton 总数:", len(tk.findChildren(SquareButton)))
    check("kcpanel_any", anybtns)

    # 设置页：字段标题来自资源 JSON（运行时数据），需要假键盘让 recreate_gui 真正跑起来
    from collections import defaultdict
    from editor.qmk_settings import QmkSettings

    qs = QmkSettings()
    kb = type("FakeKB", (), {})()
    kb.supported_settings = set(f["qsid"] for t in QmkSettings.settings_defs["tabs"]
                                for f in t["fields"])
    kb.settings = defaultdict(int)
    qs.keyboard = kb
    qs.recreate_gui()
    keep.append(qs)
    qmtabs = " || ".join(qs.tabs_widget.tabText(i) for i in range(qs.tabs_widget.count()))
    print("qmk tabs:", qmtabs)
    qmkp = qmtabs + " || " + pool(qs)
    print("qmk 文案:", qmkp[:300])
    for name in ("qmk_tab_magic", "qmk_tab_taphold", "qmk_swapcaps", "qmk_backslash",
                 "qmk_grave", "qmk_retro", "qmk_holdpress", "qmk_flowtap", "qmk_chordal", "qmk_quicktap",
                 "qmk_autoshift", "qmk_oneshot", "qmk_mouse"):
        check(name, qmkp)

    # 这几个窗口的构造签名/父级要求不一，直接验证其 tr() 调用点的译文
    check("qmk_reset", tr("QmkSettings", "Reset all settings to default values?"))
    check("textbox_apply", tr("TextboxWindow", "Apply"))
    check("textbox_cancel", tr("TextboxWindow", "Cancel"))
    check("anydlg_arb", tr("AnyKeycodeDialog", "Enter an arbitrary keycode"))
    check("anydlg_value", tr("AnyKeycodeDialog", "Computed value: 0x{:X}"))
    check("unlocker_hold", tr("Unlocker",
        "Press and hold the following keys until the progress bar below fills up:"))

    # 占位符完整性
    a = tr("AnalogTab", "Key #{} (row {}, col {})").format(7, 1, 3)
    b = tr("AnyKeycodeDialog", "Computed value: 0x{:X}").format(0x7786)
    c = tr("TapDance", "Use <code>TD({})</code> to set up this action in the keymap.").format(2)
    d = tr("Combos", "Key {}").format(4)
    e = tr("MacroRecorder", "Memory used by macros: {}/{}").format(10, 2048)
    print("format 自检:", a, "|", b, "|", c, "|", d, "|", e)
    assert "7" in a and "0X7786" in b.upper() and "TD(" in c and "4" in d and "10/2048" in e, \
        "占位符被破坏"

    # 同进程内切换语言，新建控件应立即换语言
    other = "en" if EXPECT == "zh" else "zh"
    i18n.set_language(other)
    flipped = tr("MainWindow", "Refresh")
    expect_after = PROBES["refresh_btn"][1 if other == "en" else 0]
    print("set_language(%s) -> Refresh=%r (期望 %r)" % (other, flipped, expect_after))
    assert flipped == expect_after, "运行时切换语言无效"
    i18n.set_language(EXPECT)

except Exception:
    traceback.print_exc()
    fails.append("运行时异常（见上方 traceback）")

print("\n%-18s %-40s %-4s %s" % ("PROBE", "EXPECT", "OK?", "ACTUAL"))
for name, want, ok, actual in results:
    print("%-18s %-40s %-4s %s" % (name, want[:40], "yes" if ok else "NO", actual))

npass = sum(1 for r in results if r[2])
print("\n%d/%d 项通过 (lang=%s)" % (npass, len(results), EXPECT))
if fails:
    print("FAIL:")
    for f in fails:
        print("   -", f)
    sys.stdout.flush()
    os._exit(1)
print("== ALL i18n CHECKS PASSED (lang=%s) ==" % EXPECT)
sys.stdout.flush()
os._exit(0)