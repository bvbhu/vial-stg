# SPDX-License-Identifier: GPL-2.0-or-later
"""Analog Tab — 磁轴/静电容 每键参数 + 全局参数。

上下布局：上方是真实 KLE 键盘（与"键位映射"同一套 KeyboardWidget，
可解析布局编辑选项），下方是参数面板。

面板（参照用户参考图）：
  最左：选中键预览键帽——显示该键 0 层键码；
  左：行程区——纵向进度条 + 0/行程N/255 刻度列 + 蓝色滑杆轨道（断开点/触发点双手柄）；
  中：RT 开关、RT 触发/断开灵敏度滑块（固定窄栏）、RT 死区说明；
  右：操作按钮（跟随全局复选框 / 重新校准初始读数 / 触底校准开关 / 全部恢复默认）；
  底：原始读数一行（原始 ADC / 初始 / 触底）+ 操作结果状态行。

选中按键时：键盘区键面始终显示配置参数（触发/断开点），该键 0 层键码只显示在
面板最左的预览键帽上；标尺行程指示与底部读数实时刷新。
未选中按键时：全局参数模式——行程与三个读数显示为无意义占位（"—"），
手柄显示固件 EEPROM 全局槽的值；调节经 0xF2/0xFFFF 写全局槽 RAM，由固件
analog_set_global 级联刷新所有跟随键（GUI 不逐键补写）。
(v3 固件)0xF2 只改 RAM 不落盘：点"保存到 EEPROM"(0xF6) 才写入 EEPROM，
一轮调节压成一次 flash 提交；校准(0xF4)/恢复默认(0xF5)仍即时落盘。

协议见 protocol/analog.py 与 vial-qmk-wireless/docs/vial-analog-protocol.md。
"""

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QEvent, QRect, QRectF
from PyQt5.QtGui import QFont, QFontMetrics, QPainter, QPalette, QColor, QPen
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QStyle,
                             QStyleOptionSlider, QCheckBox, QPushButton, QSizePolicy, QApplication)

from editor.basic_editor import BasicEditor
from util import tr, KeycodeDisplay
from widgets.keyboard_widget import KeyboardWidget, KeyWidget
from protocol.constants import (ANALOG_AXIS_NONE, ANALOG_FLAG_RT_ENABLED,
                                ANALOG_FLAG_ACTUATION_OVERRIDE,
                                ANALOG_CAL_SAMPLE_REST, ANALOG_CAL_SAMPLE_FULL,
                                ANALOG_CAL_BOTTOM_OUT_ON, ANALOG_CAL_BOTTOM_OUT_OFF,
                                ANALOG_CAP_BOTTOM_OUT_CAL,
                                ANALOG_PROTOCOL_VERSION)
from protocol.analog import AnalogKeyConfig
from unlocker import Unlocker
from constants import (KEY_SIZE_RATIO, KEY_SPACING_RATIO, KEY_ROUNDNESS,
                       SHADOW_TOP_PADDING, SHADOW_BOTTOM_PADDING)


def _retain_space(widget):
    """隐藏时保留占位。

    面板里凡是会"按状态出现/消失"的控件都要走这里：隐藏只能留白，
    绝不允许把旁边的控件挤动（用户要求：隐藏内容不要影响布局）。
    """
    sp = widget.sizePolicy()
    sp.setRetainSizeWhenHidden(True)
    widget.setSizePolicy(sp)
    return widget


class TravelProgressBar(QWidget):
    """行程区（参照用户参考图，从左到右四列）：

    1. 纵向进度条：比背景更暗的凹槽 + 高亮填充，填充高度 = 当前行程（0 在上、255 在下）
    2. 刻度列：顶部 0、其下"行程 N"实时读数、底部 255
    3. 竖线轨道分三段：断开点以上 / 两点之间(死区，颜色与外侧不同) / 触发点以下；
       断开点与触发点的手柄由 QStyle 直接绘制，尺寸和配色与 RT 滑块完全一致（拖动发 changed()）
    4. 手柄名称+数值标注：两手柄挨太近时标注一上一下避让、手柄左右各让一个身位，不重合

    travel=None（全局模式）时进度条整条淡填充、行程读数显示占位。
    程序化装载走 set_points()/set_travel()，不发 changed（不是用户编辑）。
    """

    changed = pyqtSignal()

    _BAR_X0 = 8        # 列1：行程进度条
    _BAR_X1 = 30
    _SCALE_X = 36      # 列2：0 / 行程 N / 255
    _TRACK_X = 92      # 列3：手柄轨道（蓝色竖线）中心
    _LABEL_X = 114     # 列4：手柄名称+数值
    _MARGIN = 4        # 上下留白
    _LABEL_BLOCK = 30  # 两行标注的最小占位高度（碰撞避让用）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.actuation = 200
        self.release = 192
        self.travel = None  # None = 全局模式，行程读数显示占位
        # 列1/列2（行程进度条 + 行程读数）是否绘制：全局模式下这两列无意义，
        # 但列3/列4 的触发/断开设置轨与手柄必须保留。只影响绘制，不改尺寸/布局。
        self._travel_cols = True
        self._drag = None   # "act" | "rel" | None
        # 手柄代理控件：QSS 的 "QSlider::handle" 规则是按控件类名匹配的，
        # 直接把本控件当 widget 传给样式会退化成基础样式（颜色对不上），
        # 所以借一个隐藏的空 QSlider 做样式代理——手柄的颜色和尺寸就与 RT 栏完全同源。
        self._handle_proxy = QSlider(Qt.Horizontal, self)
        self._handle_proxy.hide()
        self.setMinimumSize(200, 260)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

    # ------------------------------------------------------------ 值接口 ----
    def set_points(self, actuation, release, travel=None):
        """程序化装载：不发 changed。"""
        self.actuation = max(0, min(255, int(actuation)))
        self.release = max(0, min(255, int(release)))
        self.travel = travel
        self.update()

    def set_travel(self, travel):
        """轮询更新行程指示（None 隐藏）。"""
        self.travel = travel
        self.update()

    def set_travel_cols_visible(self, visible):
        """显隐"行程进度条 + 行程 N 读数"两列（全局模式隐藏这两列）。

        触发/断开设置轨与手柄（列3/列4）不受影响——全局模式下照样要能调。
        仅切换绘制、不改变控件尺寸，因此不会挤动布局。
        """
        visible = bool(visible)
        if self._travel_cols != visible:
            self._travel_cols = visible
            self.update()

    # ------------------------------------------------------------ 几何 ----
    def _y_of(self, v):
        top = self._MARGIN
        bot = self.height() - self._MARGIN
        return top + (bot - top) * v / 255.0

    def _v_of(self, y):
        top = self._MARGIN
        bot = self.height() - self._MARGIN
        if bot <= top:
            return 0
        return max(0, min(255, round((y - top) * 255.0 / (bot - top))))

    def _label_ys(self):
        """两个标注的中心 y：默认贴各自手柄；手柄间距不足一个标注块时，
        断开点往上、触发点往下对称让开（参考图里两者离得远，不会碰到）。"""
        y_rel = self._y_of(self.release)
        y_act = self._y_of(self.actuation)
        half = self._LABEL_BLOCK / 2.0
        lo = self._MARGIN + half
        hi = self.height() - self._MARGIN - half
        if y_act - y_rel >= self._LABEL_BLOCK or hi <= lo:
            return y_rel, y_act
        mid = (y_rel + y_act) / 2.0
        yr = mid - half
        ya = mid + half
        if yr < lo:                      # 贴顶让不开：整对一起下移
            shift = lo - yr
            yr = lo
            ya = min(ya + shift, hi)
        elif ya > hi:                    # 贴底让不开：整对一起上移
            shift = ya - hi
            ya = hi
            yr = max(yr - shift, lo)
        return yr, ya

    # ------------------------------------------------------------ 手柄 ----
    # 手柄不自己画：交给样式画真实的滑杆手柄，尺寸与配色就和 RT 栏完全一致。
    # 关键是 option.rect 必须保持"一条横滑杆"的几何（缩成手柄大小会改变样式的渐变/边框数学，
    # 画出来就不是同一个东西），再把绘制平移到目标位置。
    _PROXY_SLIDER = QRect(0, 0, 250, 15)   # 与 RT 滑块同尺寸的一条假滑杆

    def _handle_opt(self, v, pressed):
        proxy = self._handle_proxy
        opt = QStyleOptionSlider()
        opt.rect = self._PROXY_SLIDER
        opt.palette = proxy.palette()
        opt.font = proxy.font()
        opt.fontMetrics = QFontMetrics(proxy.font())
        opt.minimum, opt.maximum = 0, 255
        opt.sliderPosition = opt.value = 0        # 贴最左，便于算平移量
        opt.orientation = Qt.Horizontal
        # subControls 必须与真滑块一致（凹槽+手柄）：样式的渐变/描边数学会看"有没有凹槽"，
        # 只给 SC_SliderHandle 会画出另一种样子。绘制时把裁剪限制在手柄矩形内，
        # 凹槽先画、手柄盖在它上面，最终只会留下手柄本身。
        opt.subControls = QStyle.SC_SliderGroove | QStyle.SC_SliderHandle
        opt.activeSubControls = QStyle.SC_SliderHandle if pressed else QStyle.SC_None
        state = QStyle.State_Enabled | QStyle.State_Horizontal
        if pressed:
            state |= QStyle.State_Sunken | QStyle.State_MouseOver | QStyle.State_On
        opt.state = state
        return opt

    def _handle_rect_in_option(self):
        """样式认为手柄在这条假滑杆里的位置与大小。"""
        proxy = self._handle_proxy
        r = proxy.style().subControlRect(QStyle.CC_Slider, self._handle_opt(0, False),
                                         QStyle.SC_SliderHandle, proxy)
        return r

    def _handle_size(self):
        r = self._handle_rect_in_option()
        return max(9, r.width()), max(9, r.height())

    def _draw_handle(self, qp, cx, cy, pressed):
        """把样式画的手柄平移到 (cx, cy) 居中。"""
        proxy = self._handle_proxy
        r = self._handle_rect_in_option()
        target = QRect(int(cx - r.width() / 2.0), int(cy - r.height() / 2.0), r.width(), r.height())
        qp.save()
        qp.setClipRect(target)          # 凹槽只允许在手柄矩形内落笔，随后被手柄盖住
        qp.translate(target.x() - r.x(), target.y() - r.y())
        proxy.style().drawComplexControl(QStyle.CC_Slider, self._handle_opt(0, pressed), qp, proxy)
        qp.restore()
        return r.width(), r.height()

    # ------------------------------------------------------------ 绘制 ----
    def paintEvent(self, ev):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        text_color = pal.color(QPalette.WindowText)
        top = self._MARGIN
        bot = self.height() - self._MARGIN
        bar_w = self._BAR_X1 - self._BAR_X0

        # 列1/列2（行程进度条 + 行程读数）：仅在选中按键时绘制。
        # 全局模式下"当前行程"对全局槽没有意义（用户图1 指的就是这两列），
        # 但列3/列4 的触发/断开设置轨与手柄必须保留——那正是全局参数本身。
        # 只跳过绘制、不改尺寸，所以隐藏这两列不会影响布局。
        if self._travel_cols:
            # 列2 刻度：顶 0、其下"行程 N"实时读数、底 255
            qp.setPen(text_color)
            qp.drawText(QRect(self._SCALE_X, top - 2, 54, 16), Qt.AlignLeft | Qt.AlignVCenter, "0")
            qp.drawText(QRect(self._SCALE_X, bot - 14, 54, 16), Qt.AlignLeft | Qt.AlignVCenter, "255")
            qp.drawText(QRect(self._SCALE_X, top + 20, 54, 16), Qt.AlignLeft | Qt.AlignVCenter,
                        tr("AnalogTab", "Travel"))
            qp.drawText(QRect(self._SCALE_X, top + 38, 54, 20), Qt.AlignLeft | Qt.AlignTop,
                        "—" if self.travel is None else str(self.travel))

            # 列1 行程进度条：比背景更暗的凹槽 + 高亮填充（0 在上，按下后向下生长）
            # 注意：本主题下 Button 与 Window 同色、Dark 反而是浅灰，都不能当凹槽，
            # 所以凹槽按窗口色现算 darker()，保证任何主题下都比背景暗（参考图即如此）
            groove = pal.color(QPalette.Window).darker(190)
            qp.setPen(Qt.NoPen)
            qp.setBrush(groove)
            qp.drawRoundedRect(QRectF(self._BAR_X0, top, bar_w, bot - top), 8, 8)
            if self.travel is None:
                # 全局模式：行程不适用，整条淡填充占位
                faded = QColor(groove)
                faded.setAlpha(150)
                qp.setBrush(faded)
                qp.drawRoundedRect(QRectF(self._BAR_X0, top, bar_w, bot - top), 8, 8)
            elif self.travel > 0:
                qp.setBrush(pal.color(QPalette.Highlight))
                qp.drawRoundedRect(QRectF(self._BAR_X0, top, bar_w,
                                          max(10.0, self._y_of(self.travel) - top)), 8, 8)

        # 列3 手柄轨道：分三段画。两点之间是 RT 死区(迟滞带)，颜色必须与外侧两段不同
        # 画笔必须在这里显式清零：列1/列2 在全局模式下整段不绘制，不能指望它来设
        # NoPen——否则三段轨道会带上默认黑色描边，蓝竖线与手柄看起来就"样式异常"。
        qp.setPen(Qt.NoPen)
        hi = pal.color(QPalette.Highlight)
        y_rel = self._y_of(self.release)
        y_act = self._y_of(self.actuation)
        for y0, y1, c in ((top, y_rel, hi),                # 静置 → 断开点
                          (y_rel, y_act, hi.darker(190)),  # 断开点 ↔ 触发点：死区
                          (y_act, bot, hi)):               # 触发点 → 触底
            qp.setBrush(c)
            qp.drawRect(QRectF(self._TRACK_X - 1, y0, 3, max(0.0, y1 - y0)))

        # 列3/列4 手柄与标注：手柄交给样式绘制(与 RT 滑块同尺寸同配色)，标注按 _label_ys 避让
        ly_rel, ly_act = self._label_ys()
        hw, hh = self._handle_size()
        # 两个阈值常只差几个刻度(如 192/200 仅 8px)，15px 的手柄会叠在一起分不清：
        # 重叠时以轨道线为界左右各让一个身位，y 仍严格等于各自的值
        overlap = abs(y_act - y_rel) < hh + 1
        for name, v, ly in (("rel", self.release, ly_rel), ("act", self.actuation, ly_act)):
            y = self._y_of(v)
            if not overlap:
                cx = self._TRACK_X
            elif name == "rel":
                cx = self._TRACK_X - 2 - hw
            else:
                cx = self._TRACK_X + 2
            self._draw_handle(qp, cx, y, self._drag == name)
            label = tr("AnalogTab", "Release point") if name == "rel" else tr("AnalogTab", "Actuation point")
            qp.setPen(text_color)
            qp.drawText(QRect(self._LABEL_X, int(ly) - self._LABEL_BLOCK // 2, 80, self._LABEL_BLOCK),
                        Qt.AlignLeft | Qt.AlignVCenter,
                        "{}\n{}".format(label, v))

        qp.end()

    # ------------------------------------------------------------ 交互 ----
    def _handle_at(self, pos):
        """取最近的把手（阈值 12px，手柄高 14px），两个点挨得近时不会取错。"""
        best = None
        best_d = 12.0
        for name, v in (("rel", self.release), ("act", self.actuation)):
            d = abs(pos.y() - self._y_of(v))
            if d < best_d:
                best, best_d = name, d
        return best

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            h = self._handle_at(ev.pos())
            if h is not None:
                self._drag = h
                self.update()
                ev.accept()
                return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._drag is None:
            super().mouseMoveEvent(ev)
            return
        v = self._v_of(ev.pos().y())
        if self._drag == "rel":
            self.release = min(v, self.actuation)   # 断开点不允许越过触发点
        else:
            self.actuation = max(v, self.release)   # 触发点不允许越过断开点
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, ev):
        if self._drag is not None:
            self._drag = None
            self.update()
        super().mouseReleaseEvent(ev)


class _UnitKeyDesc:
    """标准 1u 键的占位描述：只为借用 KeyWidget 的真实几何与绘制路径。"""

    x = y = 0.0
    x2 = y2 = 0.0
    width = height = 1.0
    width2 = height2 = 1.0
    rotation_angle = rotation_x = rotation_y = 0.0


class KeycapPreview(QWidget):
    """参数面板左侧的选中键预览键帽。

    键盘区键面始终显示配置参数（触发/断开点）；选中键的 0 层键码只显示在这里。

    几何与配色复刻"键位映射"页的键帽（KeyboardWidget）：
      **尺寸、圆角、阴影全部借用键盘组件的真实几何** —— 内部持有一个标准 1u 的
      KeyWidget（与键盘区同一套代码、同一个布局字体行高），绘制也直接用它的
      background/foreground 路径，因此与键盘区键帽**逐像素同样大小**。
      文字仍用控件自身字体、原字号居中绘制（不随键帽缩放），并保留键码标签自带的
      换行（如 "Locking\\nCaps" 渲染成两行）。
    配色：键帽底 = Button、键帽面 = Button.lighter(120)、文字 = ButtonText；
    按下时整帽套 Highlight（与键盘区"按下变蓝"同一套色）。
    """

    _CAP_SCALE = 1.0  # 相对键盘区键帽的倍数：1.0 = 与键盘区键帽完全同样大小

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._pressed = False
        self._has_key = False
        self._scale_source = None  # fn() -> 键盘区布局字体行高
        self._kw = None
        # 隐藏时保留占位：键帽出现/消失不得挤动面板布局
        sp = self.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.setSizePolicy(sp)
        self._apply_metrics()

    def set_scale_source(self, fn):
        """fn() 返回键盘区的布局字体行高；据此把键帽做成与键盘区键帽同样大小。"""
        self._scale_source = fn
        self._apply_metrics()

    def _apply_metrics(self):
        """重建 1u KeyWidget：尺寸/圆角/阴影全部取自键盘组件的同一套几何代码。

        这里只改控件尺寸、不改字体：文字始终取控件自身字体，
        所以键帽尺寸与键盘区对齐后文字也不会被缩放。
        """
        scale = float(self.fontMetrics().height())
        if self._scale_source is not None:
            try:
                scale = float(self._scale_source()) or scale
            except Exception:
                pass
        self._kw = KeyWidget(_UnitKeyDesc(), scale)
        self.setFixedSize(max(16, int(round(self._kw.w * self._CAP_SCALE))),
                          max(16, int(round(self._kw.h * self._CAP_SCALE))))

    def changeEvent(self, ev):
        if ev.type() == QEvent.FontChange:
            self._apply_metrics()
        super().changeEvent(ev)

    def set_key(self, text):
        """text=None 表示当前无选中键（全局模式），隐藏键帽。"""
        self._has_key = text is not None
        self._text = text or ""
        self._pressed = False
        self.setVisible(self._has_key)
        self.update()

    def set_pressed(self, pressed):
        pressed = bool(pressed) and self._has_key
        if self._pressed != pressed:
            self._pressed = pressed
            self.update()

    def paintEvent(self, _ev):
        kw = self._kw
        if kw is None:
            return
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)

        pal = QApplication.palette()
        base = pal.color(QPalette.Button)
        face = base.lighter(120)
        if self._pressed:
            base = pal.color(QPalette.Highlight)
            face = base.lighter(120)

        # 键帽底与键帽面：直接画 KeyWidget 算好的真实路径，
        # 尺寸/圆角/阴影与键盘区键帽逐像素同源（不再自己造几何）
        qp.setPen(Qt.NoPen)
        qp.setBrush(base)
        qp.drawPath(kw.background_draw_path)
        qp.setBrush(face)
        qp.drawPath(kw.foreground_draw_path)

        # 键码文字：用控件自身字体（与键盘区键面同字号）、居中、保留标签自带的
        # 换行；文字不随键帽尺寸缩放，长键码靠 \n 分成多行显示
        if self._text:
            qp.setPen(pal.color(QPalette.ButtonText))
            qp.setFont(self.font())
            qp.drawText(kw.text_rect, Qt.AlignCenter, self._text)
        qp.end()


class AnalogKeyboardWidget(KeyboardWidget):
    """Analog tab 专用键盘组件：键体放大 key_scale 倍，字号不变。

    通过临时放大字体驱动键位布局（使键体变大），然后在绘制时恢复
    原始字号。这样键体变大但文字大小不变，参数数字能完整显示。
    """

    def __init__(self, layout_editor, key_scale=1.25):
        super().__init__(layout_editor)
        self._key_scale = key_scale
        self._base_font = QFont(self.font())
        self._suppressing = False

    def event(self, ev):
        if ev.type() == QEvent.LayoutRequest and self._suppressing:
            return True
        return super().event(ev)

    def layout_scale(self):
        """本组件布局用的字体行高（键位几何 = 该值 × KEY_SIZE_RATIO 等常数）。

        预览键帽靠它把自己做成"和键盘区键帽同样大小"，所以必须与 update_layout
        里实际用的放大字体一致。
        """
        f = QFont(self._base_font)
        f.setPointSize(round(f.pointSize() * self._key_scale))
        return float(QFontMetrics(f).height())

    def update_layout(self):
        if self._suppressing:
            return
        self._suppressing = True
        big_font = QFont(self._base_font)
        big_font.setPointSize(round(big_font.pointSize() * self._key_scale))
        self.setFont(big_font)
        super().update_layout()
        self.setFont(self._base_font)
        self._suppressing = False


class ClickableWidget(QWidget):
    """包裹键盘区域，点击空白处取消选择（与 KeymapEditor 同一手法）。"""

    clicked = pyqtSignal()

    def mousePressEvent(self, evt):
        super().mousePressEvent(evt)
        self.clicked.emit()


class AnalogTab(BasicEditor):

    def __init__(self, layout_editor):
        super().__init__(parent=None)
        self.layout_editor = layout_editor
        self.caps = None
        self.num_keys = 0
        self.rows = self.cols = 0
        self.configs = {}
        self.selected = None
        self.keyboard = None
        self._selected_widget = None  # 当前选中键的 KeyWidget 引用
        self._ki_widgets = {}  # ki → KeyWidget 映射（批量更新键面文字用）
        self._all_configs_loaded = False  # 全局模式：是否已加载全部键配置
        self._global_cfg = None  # 全局默认配置缓存（固件 0xFFFF 槽）
        self._loading_ui = False  # 程序化装载 UI 期间抑制 on_slider_changed
        self._active = False      # 本标签当前是否激活(显示中)，决定 rebuild 后是否重启轮询
        # v3 协议保存语义：_dirty=RAM 有改动未落盘；_save_supported=固件支持 0xF6
        self._dirty = False
        self._save_supported = False

        self._timer = QTimer()
        self._timer.setInterval(20)
        self._timer.timeout.connect(self.poll)

        self._flush_timer = QTimer()
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(150)
        self._flush_timer.timeout.connect(self.flush_config)
        self._pending_config = None

        self._build_ui()
        layout_editor.changed.connect(self.on_layout_changed)

    # ---------------------------------------------------------------- UI ----
    def _build_ui(self):
        # ---- 键盘（真实 KLE 布局，可点击选择）----
        self.container = AnalogKeyboardWidget(self.layout_editor, key_scale=1.25)
        self.container.clicked.connect(self.on_key_clicked)
        self.container.deselected.connect(self.on_key_deselected)

        kbd_area = ClickableWidget()
        kbd_layout = QVBoxLayout(kbd_area)
        kbd_layout.addWidget(self.container)
        kbd_layout.setAlignment(self.container, Qt.AlignHCenter)
        kbd_area.clicked.connect(self.on_empty_space_clicked)

        # ---- 参数面板（下方；无区域标题，参考图要求）----
        panel = QWidget()
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(0, 0, 0, 0)

        # 模式指示（全局参数 / 按键 #N），加粗
        self.lbl_key = QLabel(tr("AnalogTab", "Not connected"))
        self.lbl_key.setStyleSheet("font-weight: bold;")
        pv.addWidget(self.lbl_key)

        # 解锁提示行：secure 固件下 matrix_poll 受 unlock 门控才需要；
        # 本固件 VIAL_INSECURE=yes 时 get_unlock_status 恒 1，此行永不显示。
        self.unlock_lbl = QLabel(tr("AnalogTab", "Unlock the keyboard to show key presses:"))
        self.unlock_btn = QPushButton(tr("AnalogTab", "Unlock"))
        self.unlock_btn.clicked.connect(self.unlock)
        unlock_row = QHBoxLayout()
        unlock_row.addStretch()
        unlock_row.addWidget(self.unlock_lbl)
        unlock_row.addWidget(self.unlock_btn)
        pv.addLayout(unlock_row)
        _retain_space(self.unlock_lbl)
        _retain_space(self.unlock_btn)
        self.unlock_lbl.hide()
        self.unlock_btn.hide()

        # 详情容器：未连接时隐藏
        self._detail_panel = QWidget()
        dv = QVBoxLayout(self._detail_panel)
        dv.setContentsMargins(0, 0, 0, 0)

        main_row = QHBoxLayout()
        # 最左：选中键预览键帽——显示该键 0 层键码（键盘区键面只显示配置参数）
        self.keycap = KeycapPreview()
        # 键帽尺寸与键盘区键帽完全一致：直接问键盘组件要它的布局字体行高
        self.keycap.set_scale_source(self.container.layout_scale)
        main_row.addWidget(self.keycap, 0, Qt.AlignTop)
        # 键帽与行程区之间留白 + 右端等量留白 → 其后三栏整体居中
        main_row.addStretch(1)

        # 左：纵向行程进度条
        self.track = _retain_space(TravelProgressBar())
        self.track.changed.connect(self.on_slider_changed)
        main_row.addWidget(self.track)

        # 中：RT 开关 + 两个灵敏度滑块（参考图：固定窄栏，不随窗口拉伸）
        mid_box = QWidget()
        mid_box.setFixedWidth(250)
        mid_col = QVBoxLayout(mid_box)
        mid_col.setContentsMargins(0, 0, 0, 0)
        self.chk_rt = QCheckBox(tr("AnalogTab", "Enable Rapid Trigger (RT)"))
        self.chk_rt.stateChanged.connect(self.on_slider_changed)
        mid_col.addWidget(self.chk_rt)

        self.rt_sliders = {}
        for name, label in (("rt_down", "RT down sensitivity"), ("rt_up", "RT up sensitivity")):
            hdr = QHBoxLayout()
            hdr.addWidget(QLabel(tr("AnalogTab", label)))
            val_lbl = QLabel("0")
            val_lbl.setMinimumWidth(30)
            hdr.addWidget(val_lbl)
            hdr.addStretch(1)
            mid_col.addLayout(hdr)
            s = QSlider(Qt.Horizontal)
            s.setRange(0, 255)
            s.valueChanged.connect(self.on_slider_changed)
            s.valueChanged.connect(lambda v, l=val_lbl: l.setText(str(v)))
            mid_col.addWidget(s)
            ends = QHBoxLayout()
            ends.addWidget(QLabel("0"))
            ends.addStretch(1)
            ends.addWidget(QLabel("255"))
            mid_col.addLayout(ends)
            self.rt_sliders[name] = s

        note = QLabel(tr("AnalogTab", "In RT mode, actuation and release points act as the dead zone"))
        note.setStyleSheet("color: gray;")
        note.setWordWrap(True)
        mid_col.addWidget(note)

        # "该键跟随全局值"移到 RT 栏下方（用户要求）
        mid_col.addStretch(1)
        self.chk_follow = _retain_space(QCheckBox(tr("AnalogTab", "This key follows global values")))
        self.chk_follow.toggled.connect(self.on_follow_toggled)
        mid_col.addWidget(self.chk_follow)
        main_row.addWidget(mid_box)
        # 行程区与 RT 栏之间留一段空白；RT 栏与按钮列之间只留窄间隔（不再顶到两边）
        main_row.insertSpacing(2, 30)
        main_row.addSpacing(24)

        # 右：操作按钮（按钮只放短动作名，注意事项一律走下方灰字）
        btn_col = QVBoxLayout()
        self.btn_cal_rest = QPushButton(tr("AnalogTab", "Initial calibration"))
        self.lbl_rest_note = QLabel(tr("AnalogTab", "Do not press any key when calibrating"))
        self.lbl_rest_note.setStyleSheet("color: gray;")
        self.lbl_rest_note.setWordWrap(True)
        self.chk_cal_full = QPushButton(tr("AnalogTab", "Bottom-out calibration"))
        self.chk_cal_full.setCheckable(True)  # 开关：开启期间全部键等效 KC_NO
        self.chk_full_note = QLabel(tr("AnalogTab", "After enabling, press every key in turn, hold for a few seconds, release, then switch off to finish calibration"))
        self.chk_full_note.setStyleSheet("color: gray;")
        self.chk_full_note.setWordWrap(True)
        # v3 固件：0xF2 只改 RAM，点此钮才发 0xF6 把 RAM 全量落盘 EEPROM
        self.btn_save = QPushButton(tr("AnalogTab", "Save (all)"))
        self.btn_reset_all = QPushButton(tr("AnalogTab", "Restore defaults (all)"))
        self.btn_cal_rest.clicked.connect(lambda: self.do_calibrate(ANALOG_CAL_SAMPLE_REST))
        self.chk_cal_full.toggled.connect(self.on_cal_full_toggled)
        self.btn_reset_all.clicked.connect(self.do_reset_all)
        self.btn_save.clicked.connect(self.do_save)
        btn_col.addWidget(self.btn_cal_rest)
        btn_col.addWidget(self.lbl_rest_note)
        btn_col.addWidget(self.chk_cal_full)
        btn_col.addWidget(self.chk_full_note)
        btn_col.addWidget(self.btn_save)
        btn_col.addWidget(self.btn_reset_all)
        btn_col.addStretch(1)
        self._update_save_state()
        main_row.addLayout(btn_col)
        main_row.addStretch(1)  # 与左端等量 → 整体居中

        dv.addLayout(main_row)

        # 底：原始读数一行（原始 ADC / 初始 / 触底）+ 操作结果状态行
        # 选中键的键码显示在左侧预览键帽上，不再单独占一行
        self.lbl_raw = _retain_space(QLabel(""))
        dv.addWidget(self.lbl_raw)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color: gray;")
        dv.addWidget(self.lbl_status)

        pv.addWidget(self._detail_panel)
        self._detail_panel.hide()

        # ---- 组装：上键盘(3) 下参数(1) ----
        self.addWidget(kbd_area, 3)
        self.addWidget(panel, 1)

    def _clear_selection_ui(self):
        # 键面始终显示配置参数(_update_all_keys_text)，取消选中不再清文字；键码改在底部栏显示
        self._selected_widget = None
        self.container.update()
        if self.device is not None and self.caps is not None:
            self._show_global_mode()
        else:
            self.lbl_key.setText(tr("AnalogTab", "Not connected"))
            self._detail_panel.hide()

    def on_empty_space_clicked(self):
        self.container.deselect()
        self.container.update()

    @staticmethod
    def _untoggle(chk, value):
        """程序化改复选框：屏蔽信号，避免被当成用户操作触发协议写入。"""
        chk.blockSignals(True)
        chk.setChecked(value)
        chk.blockSignals(False)

    # ------------------------------------------------------------ interface
    def valid(self):
        return self.caps is not None and self.caps["axis_type"] != ANALOG_AXIS_NONE

    def rebuild(self, device):
        self.device = device
        self._timer.stop()
        self._flush_timer.stop()
        self.unlock_lbl.hide()
        self.unlock_btn.hide()
        self.caps = None
        self.selected = None
        self.configs = {}
        self.keyboard = None
        self._selected_widget = None
        self._all_configs_loaded = False
        self._global_cfg = None
        # 设备变更：上一设备未下发的编辑/未保存标记全部作废（RAM 状态已不可知）
        self._pending_config = None
        self._dirty = False
        self._save_supported = False
        self._update_save_state()
        self._clear_selection_ui()
        if device is None or device.keyboard is None:
            self.container.setEnabled(False)
            self._untoggle(self.chk_cal_full, False)
            return
        try:
            caps = device.keyboard.analog_get_caps()
        except Exception:
            caps = None
        # 不支持 analog 的固件会把请求包原样回显，于是 version 会读成 0xFE/0xFF、
        # num_keys 读成 0x00F0 之类的垃圾值。必须先用版本号挡掉，不能只靠 axis_type。
        if not caps or not (1 <= caps["version"] <= ANALOG_PROTOCOL_VERSION):
            self.container.setEnabled(False)
            return
        if caps["axis_type"] == ANALOG_AXIS_NONE or caps["num_keys"] == 0:
            self.container.setEnabled(False)
            return
        self.caps = caps
        self.num_keys = caps["num_keys"]
        # v3+ 才有"保存到 EEPROM"按钮（0xF2 仅 RAM + 0xF6 显式落盘）；
        # v2 固件维持"每次 0xF2 防抖落盘"的旧语义，按钮禁用并提示自动保存
        self._save_supported = caps["version"] >= 3
        self._update_save_state()
        # 触底校准开关按固件当前运行态初始化：GUI 重启后能对上，不会误以为已关闭
        self._untoggle(self.chk_cal_full, bool(caps.get("bottom_out", 0)))
        self.rows = device.keyboard.rows
        self.cols = device.keyboard.cols
        self.keyboard = device.keyboard

        # 用真实 KLE 布局填充键盘组件（与 KeymapEditor 同一接口）
        self.container.set_keys(self.keyboard.keys, self.keyboard.encoders)
        self._build_ki_widget_map()
        self.container.setEnabled(True)
        self._show_global_mode()
        # 设备在行程页激活期间发生变更(热插拔/刷新)：rebuild 顶部停了 timer，
        # 这里按激活态恢复，否则要切走再切回标签高亮才恢复
        if self._active:
            self._timer.start()

    def activate(self):
        self._active = True
        # 计时器只要本标签激活且固件支持 analog 就跑：矩阵按下高亮与是否选键无关
        if self.caps is not None:
            self._timer.start()
            if self.selected is not None:
                self._render_selected_keycode()

    def deactivate(self):
        self._active = False
        self._timer.stop()
        # 离开页面前把未下发编辑同步落进 RAM（仍不发 0xF6，落盘由用户点"保存"）
        self._flush_pending()

    # ------------------------------------------------------ global mode ----
    def _show_key_detail(self, visible):
        """按键专属内容的显示开关（两种模式下面板布局完全一致）。

        - 行程区：控件本体**始终保留** —— 全局模式下它仍要提供触发/断开设置轨，
          只隐去"行程进度条 + 行程 N 读数"两列（图1 指的正是这两列，不含设置轨）；
        - 读数行（图2）与"该键跟随全局值"复选框（图3）：全局模式整体隐藏，
          两者都设了 retainSizeWhenHidden，隐藏只留白、不挤动布局。
        """
        self.track.set_travel_cols_visible(visible)
        for w in (self.lbl_raw, self.chk_follow):
            w.setVisible(visible)

    def _show_global_mode(self):
        """未选键时进入全局参数模式：手柄显示固件全局槽的值，
        行程与三个读数显示无意义占位（全局槽不含锚点、无"当前行程"概念）。"""
        self.selected = None
        self.lbl_key.setText(tr("AnalogTab", "Global parameters"))
        self._detail_panel.show()
        self._show_key_detail(False)
        self._load_all_configs()
        cfg = self._get_global_config()
        self._load_config_to_ui(cfg)
        self.track.set_travel(None)
        self._set_readings(None, None, None)

    def _load_all_configs(self):
        """懒加载全部键配置（0xF1 × num_keys 次事务），仅首次真正读 USB。"""
        if self._all_configs_loaded or self.device is None:
            return
        for ki in range(self.num_keys):
            try:
                self.configs[ki] = self.device.keyboard.analog_get_key_config(ki)
            except Exception:
                self.configs[ki] = AnalogKeyConfig()
        self._all_configs_loaded = True

    def _get_global_config(self):
        """全局默认配置：优先 RAM 缓存（全局调节即时生效），否则读固件 0xFFFF 槽。"""
        if self._global_cfg is not None:
            return self._global_cfg
        if self.device is None:
            return AnalogKeyConfig()
        try:
            self._global_cfg = self.device.keyboard.analog_get_global_config()
            return self._global_cfg
        except Exception:
            return AnalogKeyConfig()

    def _build_ki_widget_map(self):
        """构建 ki → KeyWidget 映射，用于批量更新所有键面文字。"""
        self._ki_widgets = {}
        for w in self.container.widgets:
            d = w.desc
            if hasattr(d, 'row') and d.row is not None and d.col is not None:
                ki = d.row * self.cols + d.col
                if ki < self.num_keys:
                    self._ki_widgets[ki] = w

    def _update_all_keys_text(self):
        """更新所有键面的配置值文字。仅数字，无符号。
        第一行=触发点[+RT下行]，第二行=断开点[+RT上行]。
        RT 关："{触发点}\\n{断开点}"；RT 开：四个数字各占 3 字符（右对齐前补空格），
        这样每个键面都是等宽两行，键与键之间纵向对得齐。"""
        for ki, w in self._ki_widgets.items():
            cfg = self.configs.get(ki)
            if cfg is None:
                w.text = ""
                continue
            if cfg.is_rt_enabled():
                # 行序与行程区标尺一致：上=断开点/断开RT，下=触发点/触发RT
                # 固定宽度：每个数字右对齐占 3 字符（前补空格），键与键之间才对得齐
                w.text = "%3d %3d\n%3d %3d" % (cfg.release_point, cfg.rt_up,
                                               cfg.actuation_point, cfg.rt_down)
            else:
                w.text = "{}\n{}".format(cfg.release_point, cfg.actuation_point)
        self.container.update()

    # -------------------------------------------------------------- events
    def on_key_clicked(self):
        # 切键前先把防抖窗内的编辑落进"旧选中键"的槽：flush 按 self.selected 写，
        # 必须赶在 selected 改写前执行，否则 A 键的编辑会写进 B 键（150ms 竞态）。
        self._flush_pending()
        widget = self.container.active_key
        if widget is None or widget.desc.row is None or widget.desc.col is None:
            return
        ki = widget.desc.row * self.cols + widget.desc.col
        if ki >= self.num_keys:
            return
        self.selected = ki
        self._selected_widget = widget
        cfg = self.configs.get(ki)
        if cfg is None:
            try:
                cfg = self.device.keyboard.analog_get_key_config(ki)
                self.configs[ki] = cfg
            except Exception:
                return
        self._load_config_to_ui(cfg)
        self._show_key_detail(True)
        self._detail_panel.show()
        self.container.update()
        self._timer.start()

    def on_key_deselected(self):
        # 同 on_key_clicked：先落旧键，再清 selected（原来的 _flush_timer.stop()
        # 直接丢弃编辑，等于静默吞掉用户最后一次拖动）
        self._flush_pending()
        self.selected = None
        # 不停 _timer：矩阵按下高亮与选键无关，只要本标签激活就持续刷新
        self._clear_selection_ui()

    def on_layout_changed(self):
        if self.keyboard is None:
            return
        self.container.update_layout()
        self.container.update()
        self.container.updateGeometry()

    # ------------------------------------------------------- UI <-> config
    def _load_config_to_ui(self, cfg):
        # setValue/stateChanged 会同步触发 on_slider_changed：装载期间必须抑制，
        # 否则"读取固件值回填控件"会被当成用户编辑——重置为全局后立刻又被标回
        # 自定义(OVERRIDE)，全局模式也会多触发一次无意义的全局回写。
        # 标尺的 set_points 本身不发 changed（装载语义），无需守卫。
        self._loading_ui = True
        try:
            if self.selected is None:
                # 全局模式：复选框禁用并勾上（全局槽本身就是"跟随"语义）
                self.chk_follow.setEnabled(False)
                self._untoggle(self.chk_follow, True)
            else:
                self.chk_follow.setEnabled(True)
                self._untoggle(self.chk_follow, not cfg.is_customized())
                self.lbl_key.setText(tr("AnalogTab", "Key #{} (row {}, col {})").format(
                    self.selected, self.selected // self.cols, self.selected % self.cols))
            self.rt_sliders["rt_down"].setValue(cfg.rt_down)
            self.rt_sliders["rt_up"].setValue(cfg.rt_up)
            self.chk_rt.setChecked(cfg.is_rt_enabled())
        finally:
            self._loading_ui = False
        self.track.set_points(cfg.actuation_point, cfg.release_point)
        if self.selected is not None:
            self._set_readings(None, cfg.raw_rest, cfg.raw_full)
        self._update_all_keys_text()
        self._render_selected_keycode()

    def _set_readings(self, raw, rest, full):
        """底部读数一行；None 显示为无意义占位。"""
        dash = "—"
        self.lbl_raw.setText(tr("AnalogTab", "Raw ADC: {}  Rest: {}  Bottom: {}").format(
            dash if raw is None else raw,
            dash if rest is None else rest,
            dash if full is None else full))

    def _config_from_ui(self):
        cfg = AnalogKeyConfig()
        cfg.actuation_point = self.track.actuation
        cfg.release_point = self.track.release
        cfg.rt_down = self.rt_sliders["rt_down"].value()
        cfg.rt_up = self.rt_sliders["rt_up"].value()
        if self.chk_rt.isChecked():
            cfg.flags |= ANALOG_FLAG_RT_ENABLED
        # 单独调节某个键时标记"已自定义"，全局调节不再覆盖它
        if self.selected is not None:
            cfg.flags |= ANALOG_FLAG_ACTUATION_OVERRIDE
            # 锚点必须原样带回：0xF2 会顺写 raw_rest/raw_full，而 AnalogKeyConfig 的默认值
            # 是 0/255——不回带就会把已校准的锚点冲掉，界面随即显示 Rest 0 / Bottom 255，
            # 看着就像"这个键的校准读数不对"。
            old = self.configs.get(self.selected)
            if old is not None:
                cfg.raw_rest = old.raw_rest
                cfg.raw_full = old.raw_full
        return cfg

    def on_slider_changed(self):
        if self._loading_ui:
            return  # 程序化装载(读取固件值回填)，不是用户编辑
        if self.selected is None:
            # 全局模式：立即更新缓存（非自定义键），写固件由 flush 完成
            self._load_all_configs()
            new_cfg = self._config_from_ui()
            self._pending_config = new_cfg
            self._global_cfg = new_cfg  # 同步全局缓存
            for ki, kcfg in self.configs.items():
                if not kcfg.is_customized():
                    kcfg.actuation_point = new_cfg.actuation_point
                    kcfg.release_point = new_cfg.release_point
                    kcfg.rt_down = new_cfg.rt_down
                    kcfg.rt_up = new_cfg.rt_up
                    # 只动 RT 位：别整个覆盖 flags，保留键上原有的其它位（如 CONTINUOUS）
                    kcfg.flags = (kcfg.flags & ~ANALOG_FLAG_RT_ENABLED) | (new_cfg.flags & ANALOG_FLAG_RT_ENABLED)
        else:
            new_cfg = self._config_from_ui()
            self._pending_config = new_cfg
            self.configs[self.selected] = new_cfg
            # 拖动即"转自定义"：跟随全局复选框同步取消勾选（信号已屏蔽，不会递归）
            if self.chk_follow.isChecked():
                self._untoggle(self.chk_follow, False)
        self._update_all_keys_text()
        self._flush_timer.start()

    def flush_config(self):
        if self._pending_config is None:
            return
        if self.selected is None:
            # 全局模式：只写固件全局槽(0xFFFF)。跟随全局键由固件 analog_set_global
            # 级联刷新；这里绝不能逐键补写——0xF2 单键写在固件侧会清 FOLLOW_GLOBAL，
            # 一次全局调节就会把所有键都标成"已自定义"，全局模式从此失效。
            try:
                self.device.keyboard.analog_set_global_config(self._pending_config)
            except Exception:
                pass
        else:
            cfg = self._pending_config
            self.configs[self.selected] = cfg
            try:
                self.device.keyboard.analog_set_key_config(self.selected, cfg)
            except Exception:
                pass
        self._pending_config = None
        # v3：0xF2 只写 RAM——标记"有未保存改动"，由"保存到 EEPROM"按钮提交。
        # (v2 固件这一步实际已自动落盘，但 _save_supported=False，标记不外显)
        self._dirty = True
        self._update_save_state()

    def _flush_pending(self):
        """把 150ms 防抖窗内未下发的编辑立即同步写进"当前 selected"的 RAM 槽。"""
        if self._pending_config is not None:
            self._flush_timer.stop()
            self.flush_config()

    def _update_save_state(self):
        """保存按钮三态：可点(有未保存改动) / 已保存(置灰) / 固件不支持(置灰+说明)。"""
        if self.device is None:
            self.btn_save.setEnabled(False)
            self.btn_save.setToolTip(tr("AnalogTab", "Not connected"))
            return
        if not self._save_supported:
            self.btn_save.setEnabled(False)
            self.btn_save.setToolTip(tr("AnalogTab", "This firmware saves changes automatically"))
            return
        self.btn_save.setEnabled(self._dirty)
        self.btn_save.setToolTip(
            tr("AnalogTab", "Write current values to keyboard EEPROM") if self._dirty
            else tr("AnalogTab", "All values are saved"))

    def do_save(self):
        """点"保存到 EEPROM"：把 RAM 中全部模拟参数一次性提交(0xF6)。

        v3 固件下 0xF2 只改 RAM，拖动期间零 flash 写入；这里先同步补发防抖窗内
        的最后一次编辑，再发 0xF6 全量落盘——整轮调节只产生一次 EEPROM 提交。
        校准(0xF4)/恢复默认(0xF5)不走本按钮：它们在固件侧本就即时落盘。
        """
        if self.device is None or not self._save_supported:
            return
        self._flush_pending()
        try:
            ok = self.device.keyboard.analog_persist_commit()
        except Exception as e:
            self._flash_status(tr("AnalogTab", "Save failed: {}").format(e))
            return
        if not ok:
            self._flash_status(tr("AnalogTab", "Save failed"))
            return
        self._dirty = False
        self._update_save_state()
        self._flash_status(tr("AnalogTab", "Saved to EEPROM"))

    # --------------------------------------------------------------- polling
    def poll(self):
        if self.device is None or self.caps is None:
            return
        # ① 矩阵按下高亮：与是否选键无关，每轮都刷
        self._poll_matrix()
        # ② 选中键的实时行程读数（analog 命令不受 unlock 门控，照常轮询）
        if self.selected is None or self.selected >= self.num_keys:
            return
        try:
            readings = self.device.keyboard.analog_get_key_readings(self.selected)
        except Exception:
            return
        if readings:
            travel, raw = readings[0]
            self.track.set_travel(travel)
            cfg = self.configs.get(self.selected)
            rest = cfg.raw_rest if cfg is not None else None
            full = cfg.raw_full if cfg is not None else None
            self._set_readings(raw, rest, full)

    def _poll_matrix(self):
        """轮询整张矩阵按下态：正在按的键渲染成蓝(主题 Highlight)，松开即回普通色。

        刻意不调 setOn()：矩阵测试里"按下过"的深蓝 latch 色就是 on 态
        (Highlight.darker(150))，用户要求按下过=未按下的普通色，故只用 pressed。

        通用解锁逻辑：secure 固件下 matrix_poll 受 unlock 门控(via.c:266)，先查
        get_unlock_status，未解锁则显示 Unlock 按钮并清高亮；本固件 VIAL_INSECURE=yes
        时 get_unlock_status 恒 1，解锁 UI 永不出现、matrix_poll 直接可用。
        """
        if self.keyboard is None:
            return
        try:
            unlocked = self.keyboard.get_unlock_status(3)
        except (RuntimeError, ValueError):
            return
        if not unlocked:
            self._reset_press()
            self.unlock_lbl.show()
            self.unlock_btn.show()
            return
        self.unlock_lbl.hide()
        self.unlock_btn.hide()

        try:
            data = self.keyboard.matrix_poll()
        except (RuntimeError, ValueError):
            return
        if not data or len(data) < 2:
            return
        row_size = (self.cols + 7) // 8
        changed = False
        for w in self.container.widgets:
            d = w.desc
            if getattr(d, "row", None) is None or getattr(d, "col", None) is None:
                continue
            # data[0:2] 为 VIAL 头；每行 row_size 字节，列 bit 从行尾字节往低字节排(同 matrix_test)
            idx = 2 + d.row * row_size + (row_size - 1 - d.col // 8)
            pressed = bool((data[idx] >> (d.col % 8)) & 1) if 0 <= idx < len(data) else False
            if w.pressed != pressed:
                w.setPressed(pressed)
                changed = True
        if changed:
            self.container.update()
        # 预览键帽跟随选中键的按下态（按下同样变蓝）
        if self.selected is not None:
            sel = self._ki_widgets.get(self.selected)
            self.keycap.set_pressed(bool(sel is not None and sel.pressed))

    def _reset_press(self):
        """清掉所有键的按下高亮（锁定时调用，避免残留蓝色）。"""
        changed = False
        for w in self.container.widgets:
            if w.pressed:
                w.setPressed(False)
                changed = True
        if changed:
            self.container.update()
        self.keycap.set_pressed(False)

    def _render_selected_keycode(self):
        """把选中键的 0 层键码显示在面板最左的预览键帽上。

        键盘区键面始终由 _update_all_keys_text 显示配置参数，键码不占用键面；
        未选中(全局模式)则隐藏预览键帽。
        """
        if self.selected is None or self.keyboard is None:
            self.keycap.set_key(None)
            return
        row = self.selected // self.cols
        col = self.selected % self.cols
        code = self.keyboard.layout.get((0, row, col))
        label = KeycodeDisplay.get_label(code) if code is not None else "—"
        # 保留标签自带的换行（如 "Locking\nCaps"）：与"键位映射"页键面渲染一致
        self.keycap.set_key(label)
        # 选中瞬间即同步该键当前按下态，避免残留蓝色
        w = self._ki_widgets.get(self.selected)
        self.keycap.set_pressed(bool(w is not None and w.pressed))

    def unlock(self):
        """点击 Unlock 按钮：弹 vial 解锁对话框(已解锁则直接返回)。"""
        if self.keyboard is not None:
            Unlocker.unlock(self.keyboard)

    # ------------------------------------------------------------ calibrate
    def _reload_all_configs(self):
        """校准改动了所有键的锚点：整份缓存作废重拉(0xF1 × num_keys)。
        只刷新当前选中键的话，其它键的 Rest/Bottom 会一直显示校准前的旧值。"""
        self._all_configs_loaded = False
        self._global_cfg = None
        self.configs = {}
        self._load_all_configs()
        if self.selected is not None and self.selected < self.num_keys:
            cfg = self.configs.get(self.selected)
            if cfg is not None:
                self._set_readings(None, cfg.raw_rest, cfg.raw_full)

    def _flash_status(self, text):
        """操作结果状态行：轮询 poll() 不会覆盖它。
        旧写法把结果写在 lbl_raw 上，40ms 内就被下一次轮询刷掉，用户根本看不到。"""
        self.lbl_status.setText(text)

    def do_calibrate(self, mode):
        if self.device is None:
            return
        try:
            ok, _ = self.device.keyboard.analog_calibrate(mode, 0xFFFF)
        except Exception as e:
            self._flash_status(tr("AnalogTab", "Calibration failed: {}").format(e))
            return
        if not ok:
            self._flash_status(tr("AnalogTab", "Calibration failed"))
            return
        self._reload_all_configs()
        if mode == ANALOG_CAL_SAMPLE_REST:
            self._flash_status(tr("AnalogTab", "Rest readings sampled"))
        else:
            self._flash_status(tr("AnalogTab", "Bottom readings sampled"))

    def on_cal_full_toggled(self, on):
        """触底校准开关：开=固件抑制全部键输出(等效 KC_NO)、逐个按满即自动采集触底锚点；
        关=固件结束模式并回读全部锚点。协议失败则把开关弹回原状态。"""
        if self.device is None or self.caps is None or \
                not (self.caps["caps"] & ANALOG_CAP_BOTTOM_OUT_CAL):
            self._untoggle(self.chk_cal_full, False)
            return
        try:
            ok, _ = self.device.keyboard.analog_calibrate(
                ANALOG_CAL_BOTTOM_OUT_ON if on else ANALOG_CAL_BOTTOM_OUT_OFF, 0xFFFF)
        except Exception:
            ok = False
        if not ok:
            self._untoggle(self.chk_cal_full, not on)
            self._flash_status(tr("AnalogTab", "Calibration failed"))
            return
        if on:
            self._flash_status(tr("AnalogTab", "Press every key fully, then switch off"))
        else:
            self._reload_all_configs()
            self._flash_status(tr("AnalogTab", "Bottom readings sampled"))

    def on_follow_toggled(self, checked):
        """勾选=转回跟随全局(0xF5 单键复位，保留本键锚点)；取消=按当前面板值转自定义。"""
        if self._loading_ui or self.selected is None:
            return
        if checked:
            self._pending_config = None
            self._flush_timer.stop()
            self.do_reset_key()
        else:
            self._pending_config = self._config_from_ui()
            self.configs[self.selected] = self._pending_config
            self._update_all_keys_text()
            self._flush_timer.start()

    def do_reset_key(self):
        """该键跟随全局值（固件 0xF5 单键：回全局组，保留本键校准锚点）。"""
        if self.device is None or self.selected is None:
            return
        try:
            self.device.keyboard.analog_reset_key(self.selected)
            cfg = self.device.keyboard.analog_get_key_config(self.selected)
            self.configs[self.selected] = cfg
            self._load_config_to_ui(cfg)
        except Exception:
            pass

    def do_reset_all(self):
        """所有键恢复默认值（固件 0xF5/0xFFFF 出厂重置，连锚点一起回出厂并立即落盘）。"""
        if self.device is None:
            return
        # 先退出触底校准模式，否则重置完键盘看起来像坏的
        if self.chk_cal_full.isChecked():
            self.chk_cal_full.setChecked(False)  # 走 on_cal_full_toggled(False)：先解除 KC_NO 抑制
        try:
            self.device.keyboard.analog_reset_key(0xFFFF)
        except Exception:
            return
        # 使缓存失效，重新从固件加载
        self._reload_all_configs()
        # 出厂重置在固件侧即时落盘：RAM 与 EEPROM 已一致，不存在待保存改动
        self._dirty = False
        self._update_save_state()
        if self.selected is None:
            self._show_global_mode()
        else:
            cfg = self.configs.get(self.selected)
            self._load_config_to_ui(cfg if cfg is not None else AnalogKeyConfig())
        self._flash_status(tr("AnalogTab", "All keys reset to defaults"))
