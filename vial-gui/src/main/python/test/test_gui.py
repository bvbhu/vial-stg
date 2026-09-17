import os

# 本文件断言的是控件上的**英文原文**（"Basic" / "Quantum" 等）。i18n 会按系统
# locale 自动选语言，在中文系统上不钉住语言就会让 UI 测试假性失败。
os.environ["VIAL_LANG"] = "en"  # 硬钉，不允许被外部环境覆盖

import lzma
import os.path
import struct

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QPushButton
from pytestqt.qt_compat import qt_api

from main_window import MainWindow

from protocol.constants import CMD_VIA_GET_PROTOCOL_VERSION, CMD_VIA_VIAL_PREFIX, CMD_VIAL_GET_KEYBOARD_ID, \
    CMD_VIAL_GET_SIZE, CMD_VIAL_GET_DEFINITION, CMD_VIA_GET_LAYER_COUNT, CMD_VIA_MACRO_GET_COUNT, \
    CMD_VIA_MACRO_GET_BUFFER_SIZE, CMD_VIAL_QMK_SETTINGS_QUERY, CMD_VIAL_DYNAMIC_ENTRY_OP, \
    DYNAMIC_VIAL_GET_NUMBER_OF_ENTRIES, CMD_VIA_KEYMAP_GET_BUFFER, CMD_VIA_MACRO_GET_BUFFER, CMD_VIAL_GET_UNLOCK_STATUS, \
    CMD_VIA_SET_KEYCODE, DYNAMIC_VIAL_COMBO_GET, DYNAMIC_VIAL_COMBO_SET, DYNAMIC_VIAL_TAP_DANCE_GET, \
    DYNAMIC_VIAL_TAP_DANCE_SET, \
    CMD_VIAL_ANALOG_GET_CAPS, CMD_VIAL_ANALOG_GET_KEY_CONFIG, CMD_VIAL_ANALOG_SET_KEY_CONFIG, \
    CMD_VIAL_ANALOG_GET_KEY_READINGS, CMD_VIAL_ANALOG_CALIBRATE, CMD_VIAL_ANALOG_RESET_KEY, \
    CMD_VIAL_ANALOG_PERSIST_COMMIT, ANALOG_FLAG_RT_ENABLED, ANALOG_FLAG_ACTUATION_OVERRIDE, \
    ANALOG_FLAG_CONTINUOUS, ANALOG_CAP_BOTTOM_OUT_CAL
from widgets.square_button import SquareButton

FAKE_KEYBOARD = """
{
  "matrix": {
    "rows": 2,
    "cols": 2
  },
  "layouts": {
    "keymap": [
      [
        "0,0",
        "0,1"
      ],
      [
        "1,0",
        "1,1"
      ]
    ]
  }
}
"""


def mock_enumerate():
    return [{
        "vendor_id": 0xDEAD,
        "product_id": 0xBEEF,
        "serial_number": "vial:f64c2b3c",
        "usage_page": 0xFF60,
        "usage": 0x61,
        "path": "/magic/path/for/tests",
        "manufacturer_string": "Vial Testing Ltd",
        "product_string": "Test Keyboard",
    }]


class VirtualKeyboard:

    def __init__(self, kbjson, combos=None, tap_dance=None):
        if combos is None:
            combos = []
        if tap_dance is None:
            tap_dance = []

        self.keyboard_definition = lzma.compress(kbjson.encode("utf-8"))

        self.rows = 2
        self.cols = 2
        self.layers = 4
        self.keymap = []
        for layer in range(self.layers):
            self.keymap.append([])
            for row in range(self.rows):
                self.keymap[-1].append([0 for x in range(self.cols)])

        self.macro_count = 8
        self.macro_buffer = b"\x00" * 512

        self.combos = combos
        self.tap_dance = tap_dance

        self.key_override_entries = 0
        self.alt_repeat_key_entries = 0

        # ---- Vial Analog 协议模拟(0xF0-0xF6)，语义镜像固件 vial.c 翻译层 ----
        self.analog_axis_type = 1  # hall
        self.analog_caps = 0x3F    # 五个基础能力位 + bit5 触底校准开关
        self.analog_bottom_out = False  # 0xF4 mode4/5 的运行态，随 caps 的 msg[7] 回报
        # 行程域满量程(对应固件 ANALOG_MAX_TRAVEL)：<=255 走 uint8 线格式，否则 uint16。
        # 测试里把它改成 >255 即可覆盖宽域分支。
        self.analog_max_travel = 255
        # 全局默认槽(对应固件 g_analog_global：4 阈值 + RT 开关)
        self.analog_global = {"actuation": 200, "release": 192, "rt_down": 10, "rt_up": 10, "rt": False}
        # 每键配置：ki -> dict；出厂=跟随全局固件侧不存值，这里存"键上当前生效值"
        self.analog_keys = {}
        for ki in range(self.rows * self.cols):
            self.analog_keys[ki] = {"actuation": 200, "release": 192, "rt_down": 10, "rt_up": 10,
                                    "rt": False, "customized": False, "continuous": False,
                                    "raw_rest": 375, "raw_full": 675}
        self.analog_readings = {}  # ki -> (travel, raw)，测试注入
        self.analog_cmd_log = []   # (cmd, ki) 记录，断言用；GET_CAPS 不记(无 ki)
        # 注入读失败：列进来的 ki 在 0xF1 时抛错，用于覆盖"批量读有键没读到时
        # 不得静默用占位值顶替、更不得落盘"的路径。
        self.analog_fail_keys = set()
        # 0xF0 请求计数（GET_CAPS 没有 ki，不进 cmd_log，单独记）
        self.analog_caps_count = 0

    def get_keymap_buffer(self):
        output = b""
        for layer in range(self.layers):
            for row in range(self.rows):
                for col in range(self.cols):
                    output += struct.pack(">H", self.keymap[layer][row][col])
        return output

    def vial_cmd_dynamic(self, msg):
        if msg[2] == DYNAMIC_VIAL_GET_NUMBER_OF_ENTRIES:
            response = struct.pack("BBBB", len(self.tap_dance), len(self.combos),
                                   self.key_override_entries, self.alt_repeat_key_entries)
            # Zero pad to 31 bytes.
            response += (31 - len(response)) * b'\0'
            # Set last two bits, indicating Caps Word and Layer Lock.
            response += (0b00000011).to_bytes(1, "little")
            return response
        elif msg[2] == DYNAMIC_VIAL_COMBO_GET:
            idx = msg[3]
            assert idx < len(self.combos)
            return struct.pack("<BHHHHH", 0, *self.combos[idx])
        elif msg[2] == DYNAMIC_VIAL_COMBO_SET:
            idx = msg[3]
            keys = struct.unpack_from("<HHHHH", msg[4:])
            assert idx < len(self.combos)
            self.combos[idx] = keys
            return b""
        elif msg[2] == DYNAMIC_VIAL_TAP_DANCE_GET:
            idx = msg[3]
            assert idx < len(self.tap_dance)
            return struct.pack("<BHHHHH", 0, *self.tap_dance[idx])
        elif msg[2] == DYNAMIC_VIAL_TAP_DANCE_SET:
            idx = msg[3]
            values = struct.unpack_from("<HHHHH", msg[4:])
            assert idx < len(self.tap_dance)
            self.tap_dance[idx] = values
            return b""
        raise RuntimeError("unsupported dynamic submsg 0x{:02X}".format(msg[2]))

    # ---- 行程域宽度：镜像固件 analog_core.h 的判据(<=255 为窄) ----
    def _analog_wide(self):
        return self.analog_max_travel > 255

    def _analog_cfg_fmt(self):
        return "<HHHHBBHHH" if self._analog_wide() else "<BBBBBBHHH"

    def _analog_cfg_size(self):
        return struct.calcsize(self._analog_cfg_fmt())

    def _analog_entry_size(self):
        """0xF3 单条读数 = 行程(1 或 2 字节) + raw 2 字节。"""
        return 4 if self._analog_wide() else 3

    def _analog_max_readings(self):
        """31 字节载荷能装多少条读数(固件同样按条目宽度算)。"""
        return 31 // self._analog_entry_size()

    def _analog_cfg_bytes(self, ki):
        """打包线格式(窄 12 / 宽 16 字节)，位语义与固件一致：bit1 OVERRIDE=非跟随全局。"""
        fmt = self._analog_cfg_fmt()
        if ki == 0xFFFF:
            g = self.analog_global
            flags = ANALOG_FLAG_RT_ENABLED if g["rt"] else 0
            return struct.pack(fmt, g["actuation"], g["release"], g["rt_down"], g["rt_up"],
                               flags, 0, 375, 675, 0)
        k = self.analog_keys[ki]
        flags = 0
        if k["rt"]:
            flags |= ANALOG_FLAG_RT_ENABLED
        if k["customized"]:
            flags |= ANALOG_FLAG_ACTUATION_OVERRIDE
        if k["continuous"]:
            flags |= ANALOG_FLAG_CONTINUOUS
        return struct.pack(fmt, k["actuation"], k["release"], k["rt_down"], k["rt_up"],
                           flags, 0, k["raw_rest"], k["raw_full"], 0)

    def _analog_store(self, ki, act, rel, rtd, rtu, flags, raw_rest, raw_full):
        """解包写入，镜像固件：全局槽忽略锚点/OVERRIDE；单键写即转自定义。"""
        rt = bool(flags & ANALOG_FLAG_RT_ENABLED)
        if ki == 0xFFFF:
            self.analog_global = {"actuation": act, "release": rel, "rt_down": rtd, "rt_up": rtu, "rt": rt}
            return
        k = self.analog_keys[ki]
        k.update({"actuation": act, "release": rel, "rt_down": rtd, "rt_up": rtu,
                  "rt": rt, "customized": True,
                  "continuous": bool(flags & ANALOG_FLAG_CONTINUOUS),
                  "raw_rest": raw_rest, "raw_full": raw_full})

    def vial_cmd_analog(self, msg):
        cmd = msg[1]
        if cmd == CMD_VIAL_ANALOG_GET_CAPS:
            num = self.rows * self.cols
            self.analog_caps_count += 1
            # 布局按协议文档：msg[0]=ver, msg[1..2]=num_keys(LE16), msg[3]=axis,
            # msg[4]=caps, msg[5]=max_readings, msg[6]=config_size,
            # msg[7]=触底校准开关状态, msg[8..9]=满量程(LE16)
            return struct.pack("<BHBBBBBH", 1, num,
                               self.analog_axis_type, self.analog_caps,
                               self._analog_max_readings(), self._analog_cfg_size(),
                               1 if self.analog_bottom_out else 0, self.analog_max_travel)
        # 0xF4 比其它命令多一个 mode 字节(msg[2])，键号从 msg[3] 起
        ki_off = 3 if cmd == CMD_VIAL_ANALOG_CALIBRATE else 2
        ki = struct.unpack_from("<H", msg, ki_off)[0]
        self.analog_cmd_log.append((cmd, ki))
        if cmd == CMD_VIAL_ANALOG_GET_KEY_CONFIG:
            if ki in self.analog_fail_keys:
                raise RuntimeError("simulated analog read failure for ki=%d" % ki)
            return self._analog_cfg_bytes(ki)
        elif cmd == CMD_VIAL_ANALOG_SET_KEY_CONFIG:
            (act, rel, rtd, rtu, flags, _res,
             raw_rest, raw_full, _res2) = struct.unpack_from(self._analog_cfg_fmt(), msg, 4)
            self._analog_store(ki, act, rel, rtd, rtu, flags, raw_rest, raw_full)
            return b"\x00"
        elif cmd == CMD_VIAL_ANALOG_GET_KEY_READINGS:
            entries = b""
            n = 0
            for k in range(ki, self.rows * self.cols):
                travel, raw = self.analog_readings.get(k, (0, 0))
                entries += struct.pack("<HH" if self._analog_wide() else "<BH", travel, raw)
                n += 1
                if n == self._analog_max_readings():
                    break
            return struct.pack("<B", n) + entries
        elif cmd == CMD_VIAL_ANALOG_CALIBRATE:
            mode = msg[2]
            # mode 4/5：触底校准开关(纯运行态)——开启期间固件抑制全部键输出，
            # 扫描侧只推高各键 bottom 锚点；关闭即结束，GUI 回读全部锚点
            if mode in (4, 5):
                self.analog_bottom_out = (mode == 4)
                return b"\x00"
            lo = 0 if ki == 0xFFFF else ki
            hi = (self.rows * self.cols - 1) if ki == 0xFFFF else ki
            sample = 0
            for k in range(lo, hi + 1):
                _, raw = self.analog_readings.get(k, (0, 0))
                if mode == 0:      # SAMPLE_REST
                    self.analog_keys[k]["raw_rest"] = raw
                elif mode == 1:    # SAMPLE_FULL
                    self.analog_keys[k]["raw_full"] = raw
                elif mode == 2:    # RESET_CAL
                    self.analog_keys[k]["raw_rest"] = 375
                    self.analog_keys[k]["raw_full"] = 675
                else:
                    return b"\x01"
                sample = raw
            return b"\x00" + struct.pack("<H", sample)
        elif cmd == CMD_VIAL_ANALOG_RESET_KEY:
            g = self.analog_global
            if ki == 0xFFFF:
                self.analog_global = {"actuation": 200, "release": 192, "rt_down": 10, "rt_up": 10, "rt": False}
                for k in self.analog_keys.values():
                    k.update({"actuation": 200, "release": 192, "rt_down": 10, "rt_up": 10,
                              "rt": False, "customized": False, "continuous": False,
                              "raw_rest": 375, "raw_full": 675})
            else:
                k = self.analog_keys[ki]
                k.update({"actuation": g["actuation"], "release": g["release"],
                          "rt_down": g["rt_down"], "rt_up": g["rt_up"], "rt": g["rt"],
                          "customized": False})  # 校准锚点保留
            return b"\x00"
        elif cmd == CMD_VIAL_ANALOG_PERSIST_COMMIT:
            return b"\x00"
        raise RuntimeError("unknown analog command 0x{:02X}".format(cmd))

    def vial_cmd(self, msg):
        if msg[1] == CMD_VIAL_GET_KEYBOARD_ID:
            return struct.pack("<IQ", 6, 0xF00DFACEDEADBEEF)
        elif msg[1] == CMD_VIAL_GET_SIZE:
            return struct.pack("<I", len(self.keyboard_definition))
        elif msg[1] == CMD_VIAL_GET_DEFINITION:
            page = struct.unpack_from("<H", msg[2:])[0]
            return self.keyboard_definition[page*32:(page+1)*32]
        elif msg[1] == CMD_VIAL_GET_UNLOCK_STATUS:
            return struct.pack("<BB", 0, 0)  # TODO we want to test unlocking as well
        elif msg[1] == CMD_VIAL_QMK_SETTINGS_QUERY:
            return b"\xFF" * 32
        elif msg[1] == CMD_VIAL_DYNAMIC_ENTRY_OP:
            return self.vial_cmd_dynamic(msg)
        elif CMD_VIAL_ANALOG_GET_CAPS <= msg[1] <= CMD_VIAL_ANALOG_PERSIST_COMMIT:
            return self.vial_cmd_analog(msg)
        raise RuntimeError("unknown command for Vial protocol 0x{:02X}".format(msg[1]))

    def process(self, msg):
        if msg[0] == CMD_VIA_VIAL_PREFIX:
            return self.vial_cmd(msg)
        elif msg[0] == CMD_VIA_GET_PROTOCOL_VERSION:
            return struct.pack(">BH", msg[0], 9)
        elif msg[0] == CMD_VIA_SET_KEYCODE:
            layer, row, col, kc = struct.unpack_from(">BBBH", msg[1:])
            self.keymap[layer][row][col] = kc
            return b""
        elif msg[0] == CMD_VIA_MACRO_GET_COUNT:
            return struct.pack(">BB", msg[0], self.macro_count)
        elif msg[0] == CMD_VIA_MACRO_GET_BUFFER_SIZE:
            return struct.pack(">BH", msg[0], len(self.macro_buffer))
        elif msg[0] == CMD_VIA_MACRO_GET_BUFFER:
            offset, size = struct.unpack_from(">HB", msg[1:])
            return msg[0:1] + self.macro_buffer[offset:offset+size]
        elif msg[0] == CMD_VIA_GET_LAYER_COUNT:
            return struct.pack(">BB", msg[0], self.layers)
        elif msg[0] == CMD_VIA_KEYMAP_GET_BUFFER:
            offset, size = struct.unpack_from(">HB", msg[1:])
            return msg[0:1] + self.get_keymap_buffer()[offset:offset+size]
        raise RuntimeError("unknown command for VIA protocol 0x{:02X}".format(msg[0]))


class MockDevice:

    def open_path(self, path):
        assert path == "/magic/path/for/tests"

    def close(self):
        pass

    def write(self, data):
        assert len(data) == 33
        assert data[0] == 0
        self.msg = data[1:]

        return len(data)

    def read(self, sz, timeout_ms=None):
        assert sz == 32
        resp = self.vk.process(self.msg)
        assert len(resp) <= 32
        resp += b"\x00" * (32 - len(resp))
        return resp


class FakeAppctx:

    def get_resource(self, path):
        return os.path.join(os.path.dirname(os.path.realpath(__file__)), "../../resources/base/", path)


all_mw = []


def prepare(qtbot, keyboard_json, combos=None, tap_dance=None):
    # 原来这里写死 `import hidraw as hid`，但 hidproxy 只在 linux 上用 hidraw
    # （其它平台是 `import hid`），所以在 Windows/macOS 上补丁打到了错误的模块上。
    # 直接取应用真正使用的那个模块对象，跨平台都能生效。
    from hidproxy import hid

    vk = VirtualKeyboard(keyboard_json, combos=combos, tap_dance=tap_dance)
    MockDevice.vk = vk

    hid.enumerate = mock_enumerate
    hid.device = MockDevice

    mw = MainWindow(FakeAppctx())
    qtbot.addWidget(mw)
    mw.show()
    # keep reference to MainWindow for the duration of tests
    # when MainWindow goes out of scope some KeyWidgets are still registered within KeycodeDisplay which causes UaF
    all_mw.append(mw)

    return mw, vk


def test_gui_startup(qtbot):
    mw, vk = prepare(qtbot, FAKE_KEYBOARD)
    assert mw.combobox_devices.currentText() == "Vial Testing Ltd Test Keyboard"
    assert mw.combobox_devices.count() == 1


def test_about_keyboard(qtbot):
    mw, vk = prepare(qtbot, FAKE_KEYBOARD)

    mw.about_menu.actions()[0].trigger()
    assert mw.about_dialog.windowTitle() == "About Vial Testing Ltd Test Keyboard"
    assert mw.about_dialog.textarea.toPlainText() == ('Manufacturer: Vial Testing Ltd\n'
         'Product: Test Keyboard\n'
         'VID: DEAD\n'
         'PID: BEEF\n'
         'Device: /magic/path/for/tests\n'
         '\n'
         'VIA protocol: 9\n'
         'Vial protocol: 6\n'
         'Vial keyboard ID: F00DFACEDEADBEEF\n'
         '\n'
         'Macro entries: 8\n'
         'Macro memory: 512 bytes\n'
         'Macro delays: yes\n'
         'Complex (2-byte) macro keycodes: yes\n'
         '\n'
         'Tap Dance entries: unsupported - disabled in firmware\n'
         'Combo entries: unsupported - disabled in firmware\n'
         'Key Override entries: unsupported - disabled in firmware\n'
         'Alt Repeat Key entries: unsupported - disabled in firmware\n'
         'Caps Word: yes\n'
         'Layer Lock: yes\n'
         '\n'
         'QMK Settings: disabled in firmware\n')
    mw.about_dialog.accept()


def test_key_change(qtbot):
    """ Tests changing keys in a keymap """
    mw, vk = prepare(qtbot, FAKE_KEYBOARD)

    # nothing should be selected yet in the keyboard display
    assert mw.keymap_editor.container.active_key is None

    # initial keycode must be KC_NO
    assert vk.keymap[0][0][0] == 0

    # clicking on first key must activate it
    point = mw.keymap_editor.container.widgets[0].bbox[0]
    qtbot.mouseClick(mw.keymap_editor.container, qt_api.QtCore.Qt.MouseButton.LeftButton,
                     pos=QPoint(int(point.x()), int(point.y())))

    assert mw.keymap_editor.container.active_key == mw.keymap_editor.container.widgets[0]

    ak = mw.keymap_editor.tabbed_keycodes.all_keycodes
    bk = mw.keymap_editor.tabbed_keycodes.basic_keycodes

    # at this point we can select all keycodes so basic should be hidden
    assert ak.isVisible()
    assert not bk.isVisible()

    # change current key to B
    assert ak.currentIndex() == 0
    assert ak.tabText(ak.currentIndex()) == "Basic"
    btn = ak.widget(0).layout.itemAt(3).widget().buttons[3]
    assert btn.text == "B"
    qtbot.mouseClick(btn, qt_api.QtCore.Qt.MouseButton.LeftButton)

    # check the new keycode is KC_B
    assert vk.keymap[0][0][0] == 5

    # check that we moved to the next key after setting the first key
    assert mw.keymap_editor.container.active_key == mw.keymap_editor.container.widgets[1]

    def find_key_btn(start, text):
        for w in start.findChildren(SquareButton):
            if w.isVisible() and w.text == text:
                return w
        raise RuntimeError("cannot find a visible key button with text='{}'".format(text))

    # switch to the Quantum tab
    ak.setCurrentIndex(3)
    assert ak.tabText(ak.currentIndex()) == "Quantum"

    # change current key to a masked LCTL()
    btn = find_key_btn(ak, "LCtl\n(kc)")
    qtbot.mouseClick(btn, qt_api.QtCore.Qt.MouseButton.LeftButton)

    # check the new keycode is LCTL()
    assert vk.keymap[0][0][1] == 0x100

    # check that we moved to the next key after setting the second key
    assert mw.keymap_editor.container.active_key == mw.keymap_editor.container.widgets[2]

    # click back on the second key
    point = mw.keymap_editor.container.widgets[1].bbox[0]
    qtbot.mouseClick(mw.keymap_editor.container, qt_api.QtCore.Qt.MouseButton.LeftButton,
                     pos=QPoint(int(point.x()), int(point.y())))

    # check that we have the second key selected & it's not a mask
    assert mw.keymap_editor.container.active_key == mw.keymap_editor.container.widgets[1]
    assert not mw.keymap_editor.container.active_mask

    # click on the mask now by manually calculating somewhere within last 4/5th Y, midpoint X
    bbox = mw.keymap_editor.container.widgets[1].bbox
    min_x = min(p.x() for p in bbox)
    max_x = max(p.x() for p in bbox)
    min_y = min(p.y() for p in bbox)
    max_y = max(p.y() for p in bbox)
    qtbot.mouseClick(mw.keymap_editor.container, qt_api.QtCore.Qt.MouseButton.LeftButton,
                     pos=QPoint(int((min_x + max_x) / 2), int(min_y + (max_y - min_y) * 4/5)))
    # now we must have the inner selected on the same key
    assert mw.keymap_editor.container.active_key == mw.keymap_editor.container.widgets[1]
    assert mw.keymap_editor.container.active_mask

    # now only basic keys should be settable
    assert not ak.isVisible()
    assert bk.isVisible()

    # let's set key C
    btn = find_key_btn(bk, "C")
    qtbot.mouseClick(btn, qt_api.QtCore.Qt.MouseButton.LeftButton)

    # check the new keycode is LCTL(KC_C)
    assert vk.keymap[0][0][1] == 0x106

    # and we should have moved to the next key, setting the full key and not the inner
    assert mw.keymap_editor.container.active_key == mw.keymap_editor.container.widgets[2]
    assert not mw.keymap_editor.container.active_mask
    assert ak.isVisible()
    assert not bk.isVisible()


def test_keymap_zoom(qtbot):
    """ Tests zooming keymap in/out using +/- keys """
    mw, vk = prepare(qtbot, FAKE_KEYBOARD)

    btn_plus = mw.keymap_editor.layout_size.itemAt(0).widget()
    btn_minus = mw.keymap_editor.layout_size.itemAt(1).widget()
    # TODO: resolve this field collision, +/- are SquareButton which overrides text
    assert QPushButton.text(btn_plus) == "+"
    assert QPushButton.text(btn_minus) == "-"

    # grab area for first widget
    scale_initial = mw.keymap_editor.container.scale

    # click the plus button
    qtbot.mouseClick(btn_plus, qt_api.QtCore.Qt.MouseButton.LeftButton)
    # area got bigger
    assert mw.keymap_editor.container.scale > scale_initial

    # click the minus button
    qtbot.mouseClick(btn_minus, qt_api.QtCore.Qt.MouseButton.LeftButton)
    # area back to the initial
    assert abs(mw.keymap_editor.container.scale - scale_initial) < 0.01

    # click the minus button
    qtbot.mouseClick(btn_minus, qt_api.QtCore.Qt.MouseButton.LeftButton)
    # area got smaller
    assert mw.keymap_editor.container.scale < scale_initial


def find_key_btn(start, text):
    for w in start.findChildren(SquareButton):
        if w.isVisible() and w.text == text:
            return w
    raise RuntimeError("cannot find a visible key button with text='{}'".format(text))


def test_layer_switch(qtbot):
    """ Tests setting keycodes across different layers """
    mw, vk = prepare(qtbot, FAKE_KEYBOARD)

    ak = mw.keymap_editor.tabbed_keycodes.all_keycodes
    bk = mw.keymap_editor.tabbed_keycodes.basic_keycodes
    c = mw.keymap_editor.container

    # initial keycode must be KC_NO
    assert vk.keymap[0][0][0] == 0

    # clicking on first key must activate it
    point = c.widgets[0].bbox[0]
    qtbot.mouseClick(c, qt_api.QtCore.Qt.MouseButton.LeftButton,
                     pos=QPoint(int(point.x()), int(point.y())))
    assert c.active_key == c.widgets[0]

    # change current key to Z
    qtbot.mouseClick(find_key_btn(ak, "Z"), qt_api.QtCore.Qt.MouseButton.LeftButton)
    assert vk.keymap[0][0][0] == 0x1D
    assert vk.keymap[1][0][0] == 0

    # make sure display for the widget now says Z
    assert c.widgets[0].text == "Z"
    # and that the next key is selected
    assert c.active_key == c.widgets[1]
    assert not c.active_mask

    # go to layer 1
    btn_layer_0 = mw.keymap_editor.layer_buttons[0]
    btn_layer_1 = mw.keymap_editor.layer_buttons[1]
    # TODO: resolve this field collision, +/- are SquareButton which overrides text
    assert QPushButton.text(btn_layer_0) == "0"
    assert QPushButton.text(btn_layer_1) == "1"

    qtbot.mouseClick(btn_layer_1, qt_api.QtCore.Qt.MouseButton.LeftButton)
    # check the current key got deselected
    assert c.active_key is None
    assert not c.active_mask

    # check the widget now displays layer 1 data, i.e. empty string as it's not set yet
    assert c.widgets[0].text == ""

    # click the key again
    qtbot.mouseClick(c, qt_api.QtCore.Qt.MouseButton.LeftButton,
                     pos=QPoint(int(point.x()), int(point.y())))
    assert c.active_key == c.widgets[0]

    # change current key to Y
    qtbot.mouseClick(find_key_btn(ak, "Y"), qt_api.QtCore.Qt.MouseButton.LeftButton)
    assert vk.keymap[0][0][0] == 0x1D
    assert vk.keymap[1][0][0] == 0x1C

    # make sure display for the widget now says Y
    assert c.widgets[0].text == "Y"

    # go back to the layer 0 and make sure the button got redrawn to Z
    qtbot.mouseClick(btn_layer_0, qt_api.QtCore.Qt.MouseButton.LeftButton)
    assert c.widgets[0].text == "Z"


def test_combos(qtbot):
    from widgets.key_widget import KeyWidget

    """ Tests setting combo keycodes """
    mw, vk = prepare(qtbot, FAKE_KEYBOARD, combos=[[0, 0, 0, 0, 0], [4, 5, 6, 7, 8], [0, 0x106, 0, 0, 0]])

    combos = None
    for x in range(mw.tabs.count()):
        if mw.tabs.tabText(x) == "Combos":
            combos = mw.tabs.widget(x).editor

    assert combos is not None, "could not find the combos tab"
    ct = combos.tabs

    tabs = []
    for x in range(ct.count()):
        tabs.append(ct.tabText(x))
    assert tabs == ["1", "2", "3"]

    def check_tab(idx, keys):
        ct.setCurrentIndex(idx)
        assert ct.tabText(ct.currentIndex()) == str(idx + 1)
        w = ct.widget(ct.currentIndex()).findChildren(KeyWidget)
        assert len(w) == 5
        for x in range(5):
            assert w[x].keycode == keys[x], "unexpected keycode at tab {} position {}: {} vs {}".format(idx, x, w[x].keycode, keys[x])

    check_tab(0, ["KC_NO", "KC_NO", "KC_NO", "KC_NO", "KC_NO"])
    check_tab(1, ["KC_A", "KC_B", "KC_C", "KC_D", "KC_E"])
    check_tab(2, ["KC_NO", "LCTL(KC_C)", "KC_NO", "KC_NO", "KC_NO"])

    # ok now still on tab index 2, let's switch some combos
    # change "Key 1" to "A"
    assert not mw.tray_keycodes.isVisible()
    w = ct.widget(ct.currentIndex()).findChildren(KeyWidget)
    bbox = w[0].widgets[0].bbox
    min_x = min(p.x() for p in bbox)
    max_x = max(p.x() for p in bbox)
    min_y = min(p.y() for p in bbox)
    max_y = max(p.y() for p in bbox)
    pos_mask = QPoint(int((min_x + max_x) / 2), int(min_y + (max_y - min_y) * 4/5))
    pos = QPoint(int(bbox[0].x()), int(bbox[0].y()))
    qtbot.mouseClick(w[0], qt_api.QtCore.Qt.MouseButton.LeftButton, pos=pos)
    assert mw.tray_keycodes.isVisible()

    qtbot.mouseClick(find_key_btn(mw.tray_keycodes, "A"), qt_api.QtCore.Qt.MouseButton.LeftButton)
    assert vk.combos[2] == (4, 0x106, 0, 0, 0)

    # change "Output key" to "B"
    qtbot.mouseClick(w[4], qt_api.QtCore.Qt.MouseButton.LeftButton, pos=pos)
    qtbot.mouseClick(find_key_btn(mw.tray_keycodes, "B"), qt_api.QtCore.Qt.MouseButton.LeftButton)
    assert vk.combos[2] == (4, 0x106, 0, 0, 5)

    ak = mw.tray_keycodes.all_keycodes
    bk = mw.tray_keycodes.basic_keycodes

    # change "Key 4" to LSft(D)
    # first set up LSft(kc)
    qtbot.mouseClick(w[3], qt_api.QtCore.Qt.MouseButton.LeftButton, pos=pos)
    assert ak.isVisible()
    assert not bk.isVisible()
    ak.setCurrentIndex(3)
    assert ak.tabText(ak.currentIndex()) == "Quantum"
    qtbot.mouseClick(find_key_btn(mw.tray_keycodes, "LSft\n(kc)"), qt_api.QtCore.Qt.MouseButton.LeftButton)
    assert vk.combos[2] == (4, 0x106, 0, 0x200, 5)
    # now click the mask and set up D inside
    qtbot.mouseClick(w[3], qt_api.QtCore.Qt.MouseButton.LeftButton, pos=pos_mask)
    assert not ak.isVisible()
    assert bk.isVisible()
    qtbot.mouseClick(find_key_btn(mw.tray_keycodes, "D"), qt_api.QtCore.Qt.MouseButton.LeftButton)
    assert vk.combos[2] == (4, 0x106, 0, 0x207, 5)

    # change "Key 2" to E
    qtbot.mouseClick(w[1], qt_api.QtCore.Qt.MouseButton.LeftButton, pos=pos)
    assert ak.isVisible()
    assert not bk.isVisible()
    ak.setCurrentIndex(0)
    qtbot.mouseClick(find_key_btn(mw.tray_keycodes, "E"), qt_api.QtCore.Qt.MouseButton.LeftButton)
    assert vk.combos[2] == (4, 8, 0, 0x207, 5)

    # check the final result in the gui as well
    check_tab(2, ["KC_A", "KC_E", "KC_NO", "LSFT(KC_D)", "KC_B"])

    # TODO: a future unit test should check switching between multiple keyboards with different number of combos


def test_tap_dance(qtbot):
    from widgets.key_widget import KeyWidget
    from PyQt5.QtWidgets import QSpinBox

    mw, vk = prepare(qtbot, FAKE_KEYBOARD, tap_dance=[[0, 0, 0, 0, 200], [4, 5, 6, 7, 200], [0, 0x106, 0, 0, 500]])

    # TODO: a future unit test should check switching between multiple keyboards with different number of tap dances

    tde = None
    for x in range(mw.tabs.count()):
        if mw.tabs.tabText(x) == "Tap Dance":
            tde = mw.tabs.widget(x).editor

    assert tde is not None, "could not find the combos tab"
    td = tde.tabs

    tabs = []
    for x in range(td.count()):
        tabs.append(td.tabText(x))
    assert tabs == ["0", "1", "2"]

    def check_tab(idx, keys, timeout):
        td.setCurrentIndex(idx)
        assert td.tabText(td.currentIndex()) == str(idx)
        w = td.widget(td.currentIndex()).findChildren(KeyWidget)
        assert len(w) == 4
        for x in range(4):
            assert w[x].keycode == keys[x], "unexpected keycode at tab {} position {}: {} vs {}".format(idx, x, w[x].keycode, keys[x])
        timeout_w = td.widget(td.currentIndex()).findChildren(QSpinBox)[0]
        assert timeout_w.value() == timeout

    check_tab(0, ["KC_NO", "KC_NO", "KC_NO", "KC_NO"], 200)
    check_tab(1, ["KC_A", "KC_B", "KC_C", "KC_D"], 200)
    check_tab(2, ["KC_NO", "LCTL(KC_C)", "KC_NO", "KC_NO"], 500)

    # ok now still on tab index 2, let's switch the tap dance
    # change "Key 1" to "A"
    assert not mw.tray_keycodes.isVisible()
    w = td.widget(td.currentIndex()).findChildren(KeyWidget)
    bbox = w[0].widgets[0].bbox
    min_x = min(p.x() for p in bbox)
    max_x = max(p.x() for p in bbox)
    min_y = min(p.y() for p in bbox)
    max_y = max(p.y() for p in bbox)
    pos_mask = QPoint(int((min_x + max_x) / 2), int(min_y + (max_y - min_y) * 4/5))
    pos = QPoint(int(bbox[0].x()), int(bbox[0].y()))
    qtbot.mouseClick(w[0], qt_api.QtCore.Qt.MouseButton.LeftButton, pos=pos)
    assert mw.tray_keycodes.isVisible()

    # note that for the tap dance the keycode change is immediate but not the timeout change
    qtbot.mouseClick(find_key_btn(mw.tray_keycodes, "A"), qt_api.QtCore.Qt.MouseButton.LeftButton)
    assert vk.tap_dance[2] == (4, 0x106, 0, 0, 500)

    timeout_w = td.widget(td.currentIndex()).findChildren(QSpinBox)[0]
    timeout_w.setValue(123)
    assert vk.tap_dance[2] == (4, 0x106, 0, 0, 500)

    # check that we are adding * to the tab text when there are pending changes
    assert td.tabText(td.currentIndex()) == "2*"
    timeout_w.setValue(500)
    assert td.tabText(td.currentIndex()) == "2"

    # ok commit the change now
    timeout_w.setValue(123)
    assert td.tabText(td.currentIndex()) == "2*"
    qtbot.mouseClick(tde.btn_save, qt_api.QtCore.Qt.MouseButton.LeftButton)
    assert td.tabText(td.currentIndex()) == "2"
    assert vk.tap_dance[2] == (4, 0x106, 0, 0, 123)

    # let's check that reverting works
    assert not tde.btn_save.isEnabled()
    timeout_w.setValue(321)
    assert tde.btn_save.isEnabled()
    assert td.tabText(td.currentIndex()) == "2*"

    qtbot.mouseClick(tde.btn_revert, qt_api.QtCore.Qt.MouseButton.LeftButton)
    assert not tde.btn_save.isEnabled()
    assert td.tabText(td.currentIndex()) == "2"
    assert timeout_w.value() == 123


def test_analog_tab(qtbot):
    """Analog 页冒烟：caps 协商、全局模式只写全局槽、单键写带 OVERRIDE、轮询、复位。"""
    mw, vk = prepare(qtbot, FAKE_KEYBOARD)

    tab = mw.analog_tab
    assert tab.valid()
    assert tab.caps["version"] == 1
    assert tab.num_keys == 4
    assert tab.rows == 2 and tab.cols == 2
    # 窄域(默认满量程 255)：12 字节配置、每包最多 10 条读数
    assert tab.caps["max_travel"] == 255 and tab.max_travel == 255
    assert tab.caps["config_size"] == 12 and tab.caps["max_readings"] == 10
    # caps 自洽性：msg[6] 必须等于满量程推导出的宽度，否则整个标签页不该接管
    assert tab.caps["config_bytes_ok"]

    # 未选键 → 全局模式：手柄显示固件全局槽的值；行程与三个读数是无意义占位
    assert tab.selected is None
    assert tab.track.actuation == 200
    assert tab.track.release == 192
    assert tab.track.travel is None
    assert "—" in tab.lbl_raw.text()
    # 全局模式没有"该键"，跟随全局复选框必须禁用并勾上；触底校准开关初始跟随固件运行态
    assert not tab.chk_follow.isEnabled() and tab.chk_follow.isChecked()
    assert tab.btn_cal_rest.isEnabled() and tab.chk_cal_full.isEnabled()
    assert tab.btn_reset_all.isEnabled()
    assert tab.caps["caps"] & ANALOG_CAP_BOTTOM_OUT_CAL
    assert tab.caps["bottom_out"] == 0 and not tab.chk_cal_full.isChecked()

    # 全局调节：只允许一次 0xF2 到全局槽(0xFFFF)，绝不逐键补写——
    # 固件侧单键写会清 FOLLOW_GLOBAL，逐键写会让全局模式永久失效
    vk.analog_cmd_log.clear()
    tab.track.actuation = 150
    tab.on_slider_changed()  # 模拟拖动手柄结束（set_points 不发 changed，装载语义）
    tab.flush_config()       # 绕过 150ms 防抖定时器直接提交
    sets = [e for e in vk.analog_cmd_log if e[0] == CMD_VIAL_ANALOG_SET_KEY_CONFIG]
    assert sets == [(CMD_VIAL_ANALOG_SET_KEY_CONFIG, 0xFFFF)]
    assert vk.analog_global["actuation"] == 150
    # 防抖前缓存已同步到全部未自定义键（显示用）
    assert tab.configs[0].actuation_point == 150
    assert tab.configs[3].actuation_point == 150
    # 全局槽写入不改变键的"自定义"状态
    assert not vk.analog_keys[0]["customized"]

    # 选中一个键 → 每键模式：带 OVERRIDE 位写单键
    w = tab._ki_widgets[2]
    tab.container.active_key = w
    tab.on_key_clicked()
    assert tab.selected == 2
    assert tab.chk_follow.isEnabled() and tab.chk_follow.isChecked()  # 固件说这键还没被自定义
    vk.analog_cmd_log.clear()
    tab.track.release = 100
    tab.on_slider_changed()
    assert not tab.chk_follow.isChecked()  # 拖动即转自定义，复选框同步取消勾选
    tab.flush_config()
    sets = [e for e in vk.analog_cmd_log if e[0] == CMD_VIAL_ANALOG_SET_KEY_CONFIG]
    assert sets == [(CMD_VIAL_ANALOG_SET_KEY_CONFIG, 2)]
    assert vk.analog_keys[2]["actuation"] == 150
    assert vk.analog_keys[2]["release"] == 100
    assert vk.analog_keys[2]["customized"]
    # 锚点必须原样带回：0xF2 顺写 raw_rest/raw_full，不回带会被默认值(0/255)冲掉
    assert vk.analog_keys[2]["raw_rest"] == 375
    assert vk.analog_keys[2]["raw_full"] == 675

    # 键面显示配置值（触发点\n断开点）
    assert "150" in w.text and "100" in w.text

    # 实时读数轮询（0xF3）：标尺行程指示 + 底部读数行
    vk.analog_readings[2] = (128, 500)
    tab.poll()
    assert tab.track.travel == 128
    assert "500" in tab.lbl_raw.text()

    # 单键重置：回全局组（OVERRIDE 清除），锚点保留，UI 同步
    tab.do_reset_key()
    assert not vk.analog_keys[2]["customized"]
    assert tab.configs[2].actuation_point == 150
    assert tab.configs[2].release_point == 192
    assert not tab.configs[2].is_customized()
    assert tab.track.release == 192
    assert tab.chk_follow.isChecked()  # 复位后重新跟随全局

    # 复选框双向：取消勾选=按当前面板值转自定义；勾回=转回跟随全局(0xF5 单键)
    vk.analog_cmd_log.clear()
    tab.chk_follow.setChecked(False)
    tab.flush_config()
    sets = [e for e in vk.analog_cmd_log if e[0] == CMD_VIAL_ANALOG_SET_KEY_CONFIG]
    assert sets == [(CMD_VIAL_ANALOG_SET_KEY_CONFIG, 2)]
    assert vk.analog_keys[2]["customized"]
    vk.analog_cmd_log.clear()
    tab.chk_follow.setChecked(True)
    resets = [e for e in vk.analog_cmd_log if e[0] == CMD_VIAL_ANALOG_RESET_KEY]
    assert resets == [(CMD_VIAL_ANALOG_RESET_KEY, 2)]
    assert not vk.analog_keys[2]["customized"]

    # 触底校准开关：开=0xF4 mode4、关=mode5 并回读全部锚点；结果写状态行，不被轮询覆盖
    vk.analog_cmd_log.clear()
    tab.chk_cal_full.setChecked(True)
    assert vk.analog_bottom_out is True
    tab.chk_cal_full.setChecked(False)
    assert vk.analog_bottom_out is False
    cals = [e for e in vk.analog_cmd_log if e[0] == CMD_VIAL_ANALOG_CALIBRATE]
    assert cals == [(CMD_VIAL_ANALOG_CALIBRATE, 0xFFFF), (CMD_VIAL_ANALOG_CALIBRATE, 0xFFFF)]
    assert "sampled" in tab.lbl_status.text().lower()

    # 全局重置（出厂）
    vk.analog_cmd_log.clear()
    tab.do_reset_all()
    resets = [e for e in vk.analog_cmd_log if e[0] == CMD_VIAL_ANALOG_RESET_KEY]
    assert resets == [(CMD_VIAL_ANALOG_RESET_KEY, 0xFFFF)]
    assert tab.track.actuation == 200


def test_analog_protocol_version_pinned():
    """协议版本号钉死：bump 必须是有意的，且要同步固件 + 协议文档版本史表。

    两仓库之间没有自动化交叉校验（靠约定），所以至少让"改了但忘同步"在 GUI 侧
    留下一处可见的红色，而不是静默通过。真源：vial-qmk-stg/quantum/vial.c 的
    VIAL_ANALOG_PROTOCOL_VERSION。
    """
    from protocol.constants import ANALOG_PROTOCOL_VERSION
    assert ANALOG_PROTOCOL_VERSION == 1, (
        "协议版本号变了：请同步 vial-qmk-stg/quantum/vial.c 的 "
        "VIAL_ANALOG_PROTOCOL_VERSION 与 docs/vial-analog-protocol.md §2 的版本史表，"
        "然后更新本断言。")


def test_analog_track_scale_adapts(qtbot):
    """进度控件列2 的宽度随满量程位数自适应，且窄域下不改变原有像素栅格。"""
    mw, vk = prepare(qtbot, FAKE_KEYBOARD)
    tab = mw.analog_tab
    track = tab.track

    # 窄域(255)：维持原始栅格，不因自适应而漂移
    track.set_max_travel(255)
    assert track._TRACK_X == 92 and track._LABEL_X == 114, (track._TRACK_X, track._LABEL_X)
    assert track._scale_w == track._SCALE_W_MIN

    # 宽域：满量程数字位数超过窄域时，列2 必须跟着变大、整体右移让位。
    # 用 13 位超大值确保列宽必然越过 _SCALE_W_MIN（与具体字体无关，可复现）。
    track.set_max_travel(10**12)
    assert track._scale_w > track._SCALE_W_MIN, track._scale_w
    assert track._TRACK_X > 92, track._TRACK_X
    assert track._LABEL_X == track._TRACK_X + 22
    # 列宽与轨道中心保持原设计的 2px 间隙（列右缘恰好落在轨道中心线左侧 2px）
    assert track._TRACK_X == track._SCALE_X + track._scale_w + 2


def test_analog_wide_travel(qtbot):
    """满量程 >255：行程域升为 uint16，GUI 必须全程按 caps 自适应，不得写死 255/12 字节。"""
    mw, vk = prepare(qtbot, FAKE_KEYBOARD)
    tab = mw.analog_tab
    assert tab.caps["config_size"] == 12 and tab.caps["max_readings"] == 10

    # 换一台宽域固件，重新握手
    vk.analog_max_travel = 4096
    tab.rebuild(tab.device)
    assert tab.caps["max_travel"] == 4096
    assert tab.caps["config_size"] == 16 and tab.caps["max_readings"] == 7
    assert tab.caps["config_bytes_ok"]
    # 满量程铺到行程轨道、RT 滑块量程与末端刻度
    assert tab.max_travel == 4096 and tab.track.max_travel == 4096
    for s in tab.rt_sliders.values():
        assert s.maximum() == 4096
    for lbl in tab.rt_end_labels.values():
        assert lbl.text() == "4096"

    # 16 字节线格式往返：>255 的行程值必须完整存活（窄格式会截掉高位字节）
    vk.analog_cmd_log.clear()
    g = tab._get_global_config()
    g.actuation_point, g.release_point = 3000, 2500
    assert tab.device.keyboard.analog_set_global_config(g)
    assert vk.analog_global["actuation"] == 3000
    assert vk.analog_global["release"] == 2500

    # 0xF3 条目按 4 字节拆：宽域读数回到 >255 的行程值
    w = tab._ki_widgets[2]
    tab.container.active_key = w
    tab.on_key_clicked()
    vk.analog_readings[2] = (3000, 500)
    tab.poll()
    assert tab.track.travel == 3000
    assert "500" in tab.lbl_raw.text()

    # 键面等宽列随量程位数变宽(4 位 -> 每列 4 字符)，否则宽域数字会串列
    tab._load_all_configs()
    cfg = tab.configs[2]
    cfg.rt_down, cfg.rt_up = 10, 10
    cfg.flags |= ANALOG_FLAG_RT_ENABLED
    tab._update_all_keys_text()
    # act_rel 模式(默认)：上=断开点 下=触发点，各 4 字符右对齐
    expected = "%4d\n%4d" % (cfg.release_point, cfg.actuation_point)
    assert w.text == expected, repr(w.text)
    assert len(w.text.split("\n")[0]) == 4  # 宽域 4 字符；窄域 3 字符
    # RT 模式：切到 RT 显示，开 RT 的键显示灵敏度(0 不显示)
    tab.btn_disp_rt.setChecked(True)
    assert w.text == "%4d\n%4d" % (cfg.rt_up, cfg.rt_down), repr(w.text)
    # RT 值含 0：0 不显示，另一行保留
    cfg.rt_up = 0
    tab._update_all_keys_text()
    assert w.text == "\n%4d" % cfg.rt_down, repr(w.text)
    # 切回 act_rel
    tab.btn_disp_act_rel.setChecked(True)
    assert w.text == expected, repr(w.text)


def test_analog_vil_export_import(qtbot):
    """analog 配置随 .vil 导入导出：只含全局+自定义键阈值/RT/flags，不含锚点；max_travel 不符则不改。"""
    import json
    mw, vk = prepare(qtbot, FAKE_KEYBOARD)
    # 注入：全局值 + 两个自定义键(键1带 CONTINUOUS)，键0/2 跟随全局不导出
    vk.analog_global = {"actuation": 150, "release": 100, "rt_down": 8, "rt_up": 7, "rt": True}
    vk.analog_keys[1].update({"actuation": 130, "release": 90, "rt_down": 5, "rt_up": 6,
                              "rt": True, "customized": True, "continuous": True,
                              "raw_rest": 375, "raw_full": 675})
    vk.analog_keys[3].update({"actuation": 140, "release": 95, "rt_down": 9, "rt_up": 4,
                              "rt": False, "customized": True, "continuous": False,
                              "raw_rest": 375, "raw_full": 675})

    # 导出：.vil JSON 含 analog 块
    data = mw.keymap_editor.save_layout()
    obj = json.loads(data.decode("utf-8"))
    a = obj["analog"]
    assert a["max_travel"] == 255 and a["num_keys"] == 4
    assert a["global"] == {"actuation": 150, "release": 100, "rt_down": 8, "rt_up": 7, "rt_enabled": True}
    assert len(a["keys"]) == 2  # 只导自定义键
    k1 = next(k for k in a["keys"] if k["ki"] == 1)
    assert k1["actuation_point"] == 130 and k1["release_point"] == 90
    assert k1["flags"] & ANALOG_FLAG_ACTUATION_OVERRIDE
    assert k1["flags"] & ANALOG_FLAG_CONTINUOUS
    assert "raw_rest" not in k1 and "raw_full" not in k1  # 锚点不导出

    # 导入：先重置 vk 状态，再 restore
    vk.analog_global = {"actuation": 200, "release": 192, "rt_down": 10, "rt_up": 10, "rt": False}
    for ki in range(4):
        vk.analog_keys[ki].update({"actuation": 200, "release": 192, "rt_down": 10, "rt_up": 10,
                                   "rt": False, "customized": False, "continuous": False,
                                   "raw_rest": 375, "raw_full": 675})
    mw.keymap_editor.restore_layout(data)
    assert vk.analog_global == {"actuation": 150, "release": 100, "rt_down": 8, "rt_up": 7, "rt": True}
    assert vk.analog_keys[1]["actuation"] == 130 and vk.analog_keys[1]["customized"]
    assert vk.analog_keys[1]["continuous"]
    assert vk.analog_keys[1]["raw_rest"] == 375  # 锚点未被导入覆盖
    assert vk.analog_keys[3]["actuation"] == 140

    # max_travel 不符则不动现有
    bad = json.loads(data.decode("utf-8"))
    bad["analog"]["max_travel"] = 4096
    before = dict(vk.analog_global)
    mw.keymap_editor.restore_layout(json.dumps(bad).encode("utf-8"))
    assert vk.analog_global == before


def test_analog_key_index_guard(qtbot):
    """越界 ki 必须在 GUI 侧挡掉：固件对越界的错误码(msg[0]=1)与正常响应不可区分。"""
    import pytest
    mw, vk = prepare(qtbot, FAKE_KEYBOARD)
    kb = mw.analog_tab.device.keyboard
    num_keys = mw.analog_tab.caps["num_keys"]

    with pytest.raises(ValueError):
        kb.analog_get_key_config(num_keys)
    with pytest.raises(ValueError):
        kb.analog_get_key_config(0x1234)

    # 越界请求不该真的发出去
    vk.analog_cmd_log.clear()
    with pytest.raises(ValueError):
        kb.analog_get_key_config(num_keys + 7)
    assert vk.analog_cmd_log == []

    # 合法索引与全局槽(0xFFFF)照常
    assert kb.analog_get_key_config(0) is not None
    assert kb.analog_get_key_config(0xFFFF) is not None


def test_analog_caps_cached(qtbot):
    """caps 是编译期常量：同一连接内重复取应命中缓存，不再发 0xF0。"""
    mw, vk = prepare(qtbot, FAKE_KEYBOARD)
    kb = mw.analog_tab.device.keyboard
    base = vk.analog_caps_count  # 启动握手已查过一次

    first = kb.analog_caps()
    assert kb.analog_caps() is first  # 同一份对象，未重新查询
    assert vk.analog_caps_count == base
    assert kb.analog_caps(refresh=True) is not first  # 显式刷新才重查
    assert vk.analog_caps_count == base + 1

    # .vil 保存/恢复走缓存，不额外发 0xF0
    before = vk.analog_caps_count
    mw.keymap_editor.save_layout()
    mw.keymap_editor.restore_layout(mw.keymap_editor.save_layout())
    assert vk.analog_caps_count == before


def test_analog_unread_keys_block_save(qtbot):
    """批量读有键失败时：占位值不得被当成真值落盘，该键也不得接受编辑。"""
    mw, vk = prepare(qtbot, FAKE_KEYBOARD)
    tab = mw.analog_tab
    # 键 1 读不到（模拟掉线/固件不响应）
    vk.analog_fail_keys.add(1)

    tab._all_configs_loaded = False
    tab.configs = {}
    tab._load_all_configs()

    assert tab._unread_keys == {1}
    assert 1 in tab.configs  # UI 需要该索引存在，仍占位
    # 状态行给出明确提示，且保存被拦住（否则 0xF6 会把占位值写进 EEPROM）
    assert "1" in tab.lbl_status.text()
    tab._dirty = True
    tab._update_save_state()
    assert not tab.btn_save.isEnabled()

    # 该键的编辑不写进设备 RAM：拒绝下发并清掉待发配置
    tab.selected = 1
    tab._pending_config = tab.configs[1]
    vk.analog_cmd_log.clear()
    tab.flush_config()
    assert vk.analog_cmd_log == []
    assert tab._pending_config is None

    # 重连（重新加载且不再失败）后恢复可保存
    vk.analog_fail_keys.clear()
    tab._all_configs_loaded = False
    tab.configs = {}
    tab._load_all_configs()
    assert tab._unread_keys == set()
    tab._dirty = True
    tab._update_save_state()
    assert tab.btn_save.isEnabled()
