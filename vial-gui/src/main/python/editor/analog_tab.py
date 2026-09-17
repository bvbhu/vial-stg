# SPDX-License-Identifier: GPL-2.0-or-later
"""Analog Tab — 磁轴/静电容 每键参数 + 全局参数。

上下布局：上方是真实 KLE 键盘（与"键位映射"同一套 KeyboardWidget，
可解析布局编辑选项），下方是参数面板。

面板（参照用户参考图）：
  最左：选中键预览键帽——显示该键 0 层键码；
  左：行程区——纵向进度条 + 0/行程N/255 刻度列 + 滑杆轨道（断开点/触发点双手柄，
      轨道与手柄都由 QStyle 绘制，样式与 RT 调节条完全一致）；
  中：RT 开关、RT 触发/断开灵敏度滑块（固定窄栏）、RT 死区说明；
  右：操作按钮（跟随全局复选框 / 重新校准初始读数 / 触底校准开关 / 全部恢复默认）；
  底：原始读数一行（原始 ADC / 初始 / 触底）+ 操作结果状态行。

选中按键时：键盘区键面始终显示配置参数（触发/断开点），该键 0 层键码只显示在
面板最左的预览键帽上；标尺行程指示与底部读数实时刷新。
未选中按键时：全局参数模式——行程与三个读数显示为无意义占位（"—"），
手柄显示固件 EEPROM 全局槽的值；调节经 0xF2/0xFFFF 写全局槽 RAM，由固件
analog_set_global 级联刷新所有跟随键（GUI 不逐键补写）。
0xF2 只改 RAM 不落盘：点"保存到 EEPROM"(0xF6) 才写入 EEPROM，
一轮调节压成一次 flash 提交；校准(0xF4)/恢复默认(0xF5)仍即时落盘。

行程域满量程(最大键程)由固件 ANALOG_MAX_TRAVEL 决定，经 0xF0 caps 上报：
GUI 不假设它是 255，所有行程量纲的控件(标尺、手柄、RT 滑块)按上报值定量程，
线格式宽度(12/16 字节)也由该值决定。

协议见 protocol/analog.py 与 vial-qmk-stg/docs/vial-analog-protocol.md。
"""

import time

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QEvent, QRect, QRectF
from PyQt5.QtGui import QFont, QFontMetrics, QPainter, QPalette, QColor
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QStyle,
                             QStyleOptionSlider, QCheckBox, QPushButton, QSizePolicy, QApplication,
                             QButtonGroup)

from editor.basic_editor import BasicEditor
from util import tr, KeycodeDisplay
from widgets.keyboard_widget import KeyboardWidget, KeyWidget
from protocol.constants import (ANALOG_AXIS_NONE, ANALOG_FLAG_RT_ENABLED,
                                ANALOG_FLAG_ACTUATION_OVERRIDE,
                                ANALOG_CAL_SAMPLE_REST,
                                ANALOG_CAL_BOTTOM_OUT_ON, ANALOG_CAL_BOTTOM_OUT_OFF,
                                ANALOG_CAP_BOTTOM_OUT_CAL,
                                ANALOG_PROTOCOL_VERSION)
from protocol.analog import AnalogKeyConfig, ANALOG_DEFAULT_MAX_TRAVEL
from unlocker import Unlocker


def _retain_space(widget):
    """隐藏时保留占位。

    面板里凡是会"按状态出现/消失"的控件都要走这里：隐藏只能留白，
    绝不允许把旁边的控件挤动。
    """
    sp = widget.sizePolicy()
    sp.setRetainSizeWhenHidden(True)
    widget.setSizePolicy(sp)
    return widget


class TravelProgressBar(QWidget):
    """行程区（参照用户参考图，从左到右四列）：

    1. 纵向进度条：比背景更暗的凹槽 + 高亮填充，填充高度 = 当前行程（0 在上、满量程在下）
    2. 刻度列：顶部 0、其下"行程 N"实时读数、底部满量程
    3. 竖线轨道分三段：断开点以上 / 两点之间(死区) / 触发点以下。
       轨道三段与两个手柄全部交给 QStyle 绘制（样式凹槽 + 样式填充 + 样式手柄），
       配色、渐变、暗边与 RT 滑块逐像素同源（拖动发 changed()）
    4. 手柄名称+数值标注：两手柄挨太近时标注一上一下避让、手柄左右各让一个身位，不重合

    travel=None（全局模式）时进度条整条淡填充、行程读数显示占位。
    程序化装载走 set_points()/set_travel()，不发 changed（不是用户编辑）。
    """

    changed = pyqtSignal()

    _BAR_X0 = 8        # 列1：行程进度条
    _BAR_X1 = 30
    _SCALE_X = 36      # 列2：0 / 行程 N / 255   —— 列宽见 _apply_scale_metrics()
    _TRACK_X = 92      # 列3：手柄轨道（蓝色竖线）中心
    _LABEL_X = 114     # 列4：手柄名称+数值
    _SCALE_W_MIN = 54  # 列2 最小宽度：窄域(255)下的原始像素栅格，保证不后退
    _scale_w = _SCALE_W_MIN  # 列2 实际宽度，由 _apply_scale_metrics() 按字体实测更新
    _MARGIN = 4        # 上下留白
    _LABEL_BLOCK = 30  # 两行标注的最小占位高度（碰撞避让用）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.actuation = 200
        self.release = 192
        self.travel = None  # None = 全局模式，行程读数显示占位
        # 行程域满量程（固件 ANALOG_MAX_TRAVEL，经 0xF0 caps 上报）。
        # 本控件所有"值 ↔ 像素"换算与上限都取它，而不是写死 255：
        # 满量程大于 255 时行程/阈值是 uint16，刻度与手柄位置必须同比缩放。
        self.max_travel = ANALOG_DEFAULT_MAX_TRAVEL
        # 列1/列2（行程进度条 + 行程读数）是否绘制：全局模式下这两列无意义，
        # 但列3/列4 的触发/断开设置轨与手柄必须保留。只影响绘制，不改尺寸/布局。
        self._travel_cols = True
        self._drag = None   # "act" | "rel" | None
        # 手柄代理控件：QSS 的 "QSlider::handle" 规则是按控件类名匹配的，
        # 直接把本控件当 widget 传给样式会退化成基础样式（颜色对不上），
        # 所以借一个隐藏的空 QSlider 做样式代理——手柄的颜色和尺寸就与 RT 栏完全同源。
        self._handle_proxy = QSlider(Qt.Horizontal, self)
        self._handle_proxy.hide()
        # 轨道同理：借一个隐藏的竖版 QSlider 做代理，凹槽的宽度/渐变/暗边与 RT 横滑杆同源。
        self._track_proxy = QSlider(Qt.Vertical, self)
        self._track_proxy.hide()
        self.setMinimumSize(200, 260)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._apply_scale_metrics()

    def _apply_scale_metrics(self):
        """按标尺所需的列宽重算列2 宽度与列3/列4 的 x，替掉固定像素栅格。

        用位数（而非运行环境字体度量）估算宽度，保证布局只随满量程位数变化、
        不随系统字体/缩放漂移：窄域(255，"Travel"6字符)恰好落在原始栅格
        (_TRACK_X=92、_LABEL_X=114)；满量程数字位数更多时才右移让位。
        """
        # 每字符固定估算宽度（与默认 GUI 字体近似）；取 "0"/满量程/"Travel" 中最长串。
        longest = max(("0", str(self.max_travel), tr("AnalogTab", "Travel")), key=len)
        need = len(longest) * 8 + 6
        self._scale_w = max(self._SCALE_W_MIN, need)
        self._TRACK_X = self._SCALE_X + self._scale_w + 2
        self._LABEL_X = self._TRACK_X + 22
        self.update()

    # ------------------------------------------------------------ 值接口 ----
    def set_max_travel(self, max_travel):
        """设定行程域满量程（连接固件后由 caps 传入）。

        满量程变小（如换了一台窄域的键盘）时，手上的旧阈值会越界：一律收进新域，
        否则手柄/滑块会落在刻度区外、而写回固件的值也是越界的。
        """
        max_travel = max(1, int(max_travel))
        if max_travel == self.max_travel:
            return
        self.max_travel = max_travel
        self.actuation = max(0, min(max_travel, self.actuation))
        self.release = max(0, min(max_travel, self.release))
        if self.travel is not None:
            self.travel = max(0, min(max_travel, self.travel))
        # 满量程位数变了，标尺列宽随之重算（窄 3 位 -> 宽 4 位）
        self._apply_scale_metrics()
        self.update()

    def set_points(self, actuation, release, travel=None):
        """程序化装载：不发 changed。"""
        self.actuation = max(0, min(self.max_travel, int(actuation)))
        self.release = max(0, min(self.max_travel, int(release)))
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
        return top + (bot - top) * v / float(self.max_travel)

    def _v_of(self, y):
        top = self._MARGIN
        bot = self.height() - self._MARGIN
        if bot <= top:
            return 0
        return max(0, min(self.max_travel, round((y - top) * self.max_travel / float(bot - top))))

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

    # ------------------------------------------------------------ 轨道 ----
    # 轨道与手柄同一招：交给样式绘制，配色/渐变/暗边就与 RT 横滑杆完全同源。
    # 样式一次只画"凹槽 + 一侧填充"两段，而行程条要三段（参考图：断开点以上 /
    # 死区 / 触发点以下），所以用同一套几何画三遍，每遍只 clip 出该露的那一段：
    #   上段 filled=True  → upsideDown=False + sliderPosition=255：从凹槽顶端往下填
    #   下段 filled=True  → upsideDown=True  + sliderPosition=255：一直填到凹槽底端
    #   死区 filled=False → sliderPosition=0：整条只有凹槽（与 RT 未填充段同色）
    # opt.rect 每次朝填充的反方向多伸 _TRACK_PAD（大于手柄尺寸）：样式的填充边界正是
    # 卡在手柄边缘上的，把手柄推到裁剪区外，段内才能被真正填满；顺带让凹槽端头的
    # 1px 暗边落在轨道两端，与 RT 滑杆的端头一致。
    _TRACK_THICK = 15     # 与 RT 滑杆同厚度（那条横滑杆是 250x15）
    _TRACK_PAD = 40       # opt.rect 朝裁剪区外多伸的长度

    def _track_opt(self, filled, fill_to_bottom):
        top = self._MARGIN
        bot = self.height() - self._MARGIN
        opt = QStyleOptionSlider()
        opt.initFrom(self._track_proxy)
        opt.rect = QRect(self._TRACK_X - self._TRACK_THICK // 2,
                         top - self._TRACK_PAD if fill_to_bottom else top,
                         self._TRACK_THICK, (bot - top) + self._TRACK_PAD)
        opt.orientation = Qt.Vertical
        opt.minimum, opt.maximum = 0, 255
        opt.sliderPosition = opt.value = 255 if filled else 0
        opt.upsideDown = bool(fill_to_bottom)
        # 只要凹槽（subControls 带手柄的话，pos=0 的那个手柄会落在死区裁剪区内）
        opt.subControls = QStyle.SC_SliderGroove
        opt.activeSubControls = QStyle.SC_None
        opt.state = QStyle.State_Enabled | QStyle.State_Horizontal
        return opt

    def _track_groove(self):
        """样式给出的竖直凹槽几何（相对 self 的 x 与宽度）。"""
        proxy = self._track_proxy
        r = proxy.style().subControlRect(QStyle.CC_Slider, self._track_opt(False, False),
                                         QStyle.SC_SliderGroove, proxy)
        return r.x(), r.width()

    def _draw_track(self, qp, gx, gw, y0, y1, filled, fill_to_bottom):
        """画某一段轨道（填充段或死区凹槽段），其余部分靠 clip 挡掉。"""
        if y1 <= y0:
            return
        proxy = self._track_proxy
        qp.save()
        qp.setClipRect(QRect(gx, y0, gw, y1 - y0), Qt.IntersectClip)
        proxy.style().drawComplexControl(QStyle.CC_Slider, self._track_opt(filled, fill_to_bottom),
                                         qp, proxy)
        qp.restore()

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
        # 全局模式下"当前行程"对全局槽没有意义，指的就是这两列，
        # 但列3/列4 的触发/断开设置轨与手柄必须保留——那正是全局参数本身。
        # 只跳过绘制、不改尺寸，所以隐藏这两列不会影响布局。
        if self._travel_cols:
            # 列2 刻度：顶 0、其下"行程 N"实时读数、底 满量程(固件 ANALOG_MAX_TRAVEL)
            qp.setPen(text_color)
            sw = self._scale_w
            qp.drawText(QRect(self._SCALE_X, top - 2, sw, 16), Qt.AlignLeft | Qt.AlignVCenter, "0")
            qp.drawText(QRect(self._SCALE_X, bot - 14, sw, 16), Qt.AlignLeft | Qt.AlignVCenter,
                        str(self.max_travel))
            qp.drawText(QRect(self._SCALE_X, top + 20, sw, 16), Qt.AlignLeft | Qt.AlignVCenter,
                        tr("AnalogTab", "Travel"))
            qp.drawText(QRect(self._SCALE_X, top + 38, sw, 20), Qt.AlignLeft | Qt.AlignTop,
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

        # 列3 手柄轨道：分三段画，两点之间是 RT 死区(迟滞带)，与外侧两段明显不同。
        # 三段都是样式画的（与 RT 滑杆同源），坐标取整后逐段相邻，不留缝不重叠。
        y_rel = self._y_of(self.release)
        y_act = self._y_of(self.actuation)
        gx, gw = self._track_groove()
        ys = (int(round(top)), int(round(y_rel)), int(round(y_act)), int(round(bot)))
        for y0, y1, filled, to_bottom in ((ys[0], ys[1], True, False),   # 静置 → 断开点
                                          (ys[1], ys[2], False, False),  # 断开点 ↔ 触发点：死区
                                          (ys[2], ys[3], True, True)):   # 触发点 → 触底
            self._draw_track(qp, gx, gw, y0, y1, filled, to_bottom)

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
        # 行程域满量程（固件 ANALOG_MAX_TRAVEL）。握手前按默认 255，
        # 读到 caps 后由 _apply_max_travel 铺到行程轨道与 RT 滑块上。
        self.max_travel = ANALOG_DEFAULT_MAX_TRAVEL
        self.rt_end_labels = {}  # RT 滑块末端刻度标签，随满量程改字
        self.configs = {}
        self.selected = None
        self.keyboard = None
        self._selected_widget = None  # 当前选中键的 KeyWidget 引用
        self._ki_widgets = {}  # ki → KeyWidget 映射（批量更新键面文字用）
        self._all_configs_loaded = False  # 全局模式：是否已加载全部键配置
        self._unread_keys = set()  # 最近一次批量读失败的键索引（非空即禁止落盘）
        self._global_cfg = None  # 全局默认配置缓存（固件 0xFFFF 槽）
        self._unlock_checked = 0.0  # 上次查 get_unlock_status 的 monotonic 时刻
        self._unlock_state = None   # 缓存值：None=尚未查过
        self._loading_ui = False  # 程序化装载 UI 期间抑制 on_slider_changed
        self._active = False      # 本标签当前是否激活(显示中)，决定 rebuild 后是否重启轮询
        # 保存语义：_dirty=RAM 有改动未落盘；_save_supported=固件支持 0xF6 显式落盘
        self._dirty = False
        self._save_supported = False
        self._display_mode = "act_rel"  # "act_rel"=触发/断开，"rt"=RT 灵敏度

        self._timer = QTimer()
        self._timer.setInterval(20)
        self._timer.timeout.connect(self.poll)

        self._flush_timer = QTimer()
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(150)
        self._flush_timer.timeout.connect(self.flush_config)
        self._pending_config = None

        # 键面文字重绘的合并定时器（拖动滑块时避免整张键盘每像素重刷）
        self._keys_text_timer = QTimer()
        self._keys_text_timer.setSingleShot(True)
        self._keys_text_timer.timeout.connect(self._on_keys_text_timer)

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
        # 键面显示模式切换
        disp_row = QHBoxLayout()
        disp_row.addWidget(QLabel(tr("AnalogTab", "Key display:")))
        self.btn_disp_act_rel = QPushButton(tr("AnalogTab", "Act/Rel"))
        self.btn_disp_act_rel.setCheckable(True)
        self.btn_disp_act_rel.setChecked(True)
        self.btn_disp_rt = QPushButton(tr("AnalogTab", "RT"))
        self.btn_disp_rt.setCheckable(True)
        self._disp_group = QButtonGroup(self)
        self._disp_group.setExclusive(True)
        self._disp_group.addButton(self.btn_disp_act_rel)
        self._disp_group.addButton(self.btn_disp_rt)
        self.btn_disp_act_rel.toggled.connect(lambda c: c and self._set_display_mode("act_rel"))
        self.btn_disp_rt.toggled.connect(lambda c: c and self._set_display_mode("rt"))
        for b in (self.btn_disp_act_rel, self.btn_disp_rt):
            b.setEnabled(False)
            disp_row.addWidget(b)
        disp_row.addStretch()
        kbd_layout.addLayout(disp_row)
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
            end_lbl = QLabel("255")
            ends.addWidget(end_lbl)
            mid_col.addLayout(ends)
            self.rt_sliders[name] = s
            self.rt_end_labels[name] = end_lbl

        note = QLabel(tr("AnalogTab", "In RT mode, actuation and release points act as the dead zone"))
        note.setStyleSheet("color: gray;")
        note.setWordWrap(True)
        mid_col.addWidget(note)

        # 跟随全局复选框放在 RT 栏下方
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
        # 0xF2 只改 RAM，点此钮才发 0xF6 把 RAM 全量落盘 EEPROM
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
        # 选中键的键码显示在左侧预览键帽上，本行不重复显示
        self.lbl_raw = _retain_space(QLabel(""))
        dv.addWidget(self.lbl_raw)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color: gray;")
        dv.addWidget(self.lbl_status)

        # 操作结果状态行：显示若干秒后自动清空，免得旧结果一直挂着被误读成当前状态。
        # 必须放在 lbl_status 创建之后（本方法内），否则 __init__ 阶段连它时报 AttributeError。
        self._status_timer = QTimer()
        self._status_timer.setSingleShot(True)
        self._status_timer.setInterval(4000)
        self._status_timer.timeout.connect(self.lbl_status.clear)

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
    def _apply_max_travel(self, max_travel):
        """把固件上报的行程域满量程铺给所有以"行程"为单位的控件。

        行程轨道按满量程缩放；RT 距离滑块落在同一行程域上（它调的是两点之差），
        量程与末端刻度必须跟着走，否则宽域固件下这些控件一半的量程够不着。
        """
        self.max_travel = max(1, int(max_travel))
        self.track.set_max_travel(self.max_travel)
        for s in self.rt_sliders.values():
            s.setRange(0, self.max_travel)
        for lbl in self.rt_end_labels.values():
            lbl.setText(str(self.max_travel))

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
        self._unread_keys = set()
        self._unlock_state = None
        self._global_cfg = None
        # 设备变更：上一设备未下发的编辑/未保存标记全部作废（RAM 状态已不可知）
        self._pending_config = None
        self._dirty = False
        self._save_supported = False
        self._update_save_state()
        self._clear_selection_ui()
        self.btn_disp_act_rel.setEnabled(False)
        self.btn_disp_rt.setEnabled(False)
        if device is None or device.keyboard is None:
            self.container.setEnabled(False)
            self._force_bottom_out_off()
            self._untoggle(self.chk_cal_full, False)
            return
        try:
            # 重新握手：必须发 0xF0 取最新 caps，不能命中 _analog_caps 缓存
            # （设备可能已更换 / 固件运行态已变）。refresh=True 同时刷新缓存，
            # 保证后续 .vil 保存/恢复走的是这一份。
            caps = device.keyboard.analog_caps(refresh=True)
        except Exception:
            caps = None
        # 不支持 analog 的固件会把请求包原样回显，于是 version 会读成 0xFE/0xFF、
        # num_keys 读成 0x00F0 之类的垃圾值。必须先用版本号挡掉，不能只靠 axis_type。
        # 版本号等值判定：GUI 与固件同步更新，不接受任何其他版本。
        if not caps or caps["version"] != ANALOG_PROTOCOL_VERSION:
            self.container.setEnabled(False)
            self._force_bottom_out_off()
            return
        if caps["axis_type"] == ANALOG_AXIS_NONE or caps["num_keys"] == 0:
            self.container.setEnabled(False)
            self._force_bottom_out_off()
            return
        # 固件声明的配置字节数(msg[6])与满量程推导出的宽度必须一致：不一致说明按
        # 错误偏移解析整条配置，直接判为不支持，别带着错位去读写设备。
        if not caps.get("config_bytes_ok", False):
            self.container.setEnabled(False)
            self._force_bottom_out_off()
            return
        self.caps = caps
        self.num_keys = caps["num_keys"]
        # 行程域宽度由固件决定，所有行程量纲的控件据此重设量程
        self._apply_max_travel(caps["max_travel"])
        # 保存语义：0xF2 仅写 RAM，0xF6 显式落 EEPROM
        self._save_supported = True
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
        self.btn_disp_act_rel.setEnabled(True)
        self.btn_disp_rt.setEnabled(True)
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
        # 触底校准是**固件侧的运行态**：开关还开着就离开（切页/切键盘/拔线），
        # 固件会一直停在"全部键等效 KC_NO"，键盘看起来像坏了。离开即显式关闭。
        if self.chk_cal_full.isChecked():
            self._untoggle(self.chk_cal_full, False)
            self._force_bottom_out_off()

    def _force_bottom_out_off(self):
        """尽力让固件退出触底校准模式（不发信号，不依赖 UI 开关状态）。

        用于 rebuild/deactivate 等"离开"路径：只要可能处于该模式就补发一次 OFF。
        失败静默——设备可能已断开，此时无从补救，下次重连时 rebuild 会再试。
        """
        try:
            kb = getattr(self.device, "keyboard", None) if self.device else None
            if kb is not None:
                kb.analog_calibrate(ANALOG_CAL_BOTTOM_OUT_OFF, 0xFFFF)
        except Exception:
            pass

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
        """连接时一次性读全部键配置（0xF1 × num_keys 次事务），只读一次。

        批量读用 retries=3：默认的 20 次重试是给单次交互的，96 键板叠加起来在
        掉线时会长时间阻塞 GUI 线程。读不到的键先占位（UI 需要该索引存在），
        但记进 _unread_keys 并在保存前拦住——否则 0xF6 会把占位的默认值当真值
        写进 EEPROM，而这些键的真实配置就被覆盖了。
        """
        if self._all_configs_loaded or self.device is None:
            return
        unread = []
        for ki in range(self.num_keys):
            try:
                self.configs[ki] = self.device.keyboard.analog_get_key_config(ki, retries=3)
            except Exception:
                self.configs[ki] = AnalogKeyConfig()
                unread.append(ki)
        self._all_configs_loaded = True
        self._unread_keys = set(unread)
        if unread:
            self._flash_status(tr("AnalogTab", "{} key(s) could not be read; saving is disabled")
                               .format(len(unread)))
            self._update_save_state()

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
        """更新所有键面的配置值文字。两种显示模式由顶部按钮切换：
        act_rel：上=断开点，下=触发点；rt：上=RT上行灵敏度，下=RT下行灵敏度，
        未开 RT 或值为 0 不显示。列宽按满量程位数取，等宽对齐。"""
        colw = len(str(self.max_travel))
        for ki, w in self._ki_widgets.items():
            cfg = self.configs.get(ki)
            if cfg is None:
                w.text = ""
                continue
            if self._display_mode == "rt":
                if not cfg.is_rt_enabled():
                    w.text = ""
                    continue
                top = "%*d" % (colw, cfg.rt_up) if cfg.rt_up != 0 else ""
                bot = "%*d" % (colw, cfg.rt_down) if cfg.rt_down != 0 else ""
                w.text = "%s\n%s" % (top, bot) if (top or bot) else ""
            else:
                w.text = "%*d\n%*d" % (colw, cfg.release_point, colw, cfg.actuation_point)
        self.container.update()

    def _set_display_mode(self, mode):
        """切换键面显示模式：act_rel=触发/断开，rt=RT 灵敏度。"""
        self._display_mode = mode
        self._update_all_keys_text()

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
        # 先落旧键，再清 selected：直接停 _flush_timer 会丢弃防抖窗内的编辑，
        # 等于静默吞掉用户最后一次拖动。
        self._flush_pending()
        self.selected = None
        # 不停 _timer：矩阵按下高亮与选键无关，只要本标签激活就持续刷新
        self._clear_selection_ui()

    def on_layout_changed(self):
        if self.keyboard is None:
            return
        self.container.update_layout()
        # 切换配列会换掉一批 KeyWidget：place_widgets 按当前布局选项从
        # widgets_for_layout 里重新挑一组放进 self.widgets（widget 对象还是
        # 那些，但活动集合变了）。ki→widget 映射若还是旧的，受影响键的键面
        # 参数就写到已弃用的 widget 上——新键面空着或显示别的键的参数。
        self._build_ki_widget_map()
        if self.selected is not None:
            self._selected_widget = self._ki_widgets.get(self.selected)
        self._update_all_keys_text()
        self._render_selected_keycode()
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
        self._schedule_keys_text_update()
        self._flush_timer.start()

    def _schedule_keys_text_update(self):
        """合并高频重绘：拖动滑块时每次像素变化都重刷整张键盘会很卡。

        缓存更新(pending/configs)必须立刻做，但"刷新所有键面文字 + 重绘"这类
        纯显示动作可以合并——用一个 0 延时的单发定时器，在一轮事件循环内只做一次。
        """
        if not self._keys_text_timer.isActive():
            self._keys_text_timer.start(0)

    def _on_keys_text_timer(self):
        self._keys_text_timer.stop()
        self._update_all_keys_text()

    def flush_config(self):
        """把 _pending_config 下发到设备。返回 True 表示已成功写入（或无需写入）。"""
        if self._pending_config is None:
            return True
        if self.selected is not None and self.selected in self._unread_keys:
            # 本键在加载时没读到，面板上是从占位值改出来的：写进去只会往设备 RAM
            # 灌一份伪造的锚点/阈值。直接拒答，等重连后重新加载。
            self._pending_config = None
            self._flash_status(tr("AnalogTab", "This key could not be read; reconnect to edit it"))
            return False
        try:
            if self.selected is None:
                # 全局模式：只写固件全局槽(0xFFFF)。跟随全局键由固件 analog_set_global
                # 级联刷新；这里绝不能逐键补写——0xF2 单键写在固件侧会清 FOLLOW_GLOBAL，
                # 一次全局调节就会把所有键都标成"已自定义"，全局模式从此失效。
                ok = self.device.keyboard.analog_set_global_config(self._pending_config)
            else:
                cfg = self._pending_config
                ok = self.device.keyboard.analog_set_key_config(self.selected, cfg)
                # 只有确认写入成功才更新缓存，否则 GUI 与设备 RAM 会背离
                if ok:
                    self.configs[self.selected] = cfg
        except Exception as e:
            # 下发失败：设备 RAM 没变，保留 _pending_config 待下次（切键/离开页面）
            # 重试；绝不能置 _dirty——否则"保存到 EEPROM"会把旧值全量刷一遍并报成功。
            self._flash_status(tr("AnalogTab", "Write failed: {}").format(e))
            return False
        if not ok:
            # 协议层返回 False = 固件拒绝（如越界/不支持）。保留 pending 待重试，
            # 且不置 _dirty，避免随后 0xF6 把旧值全量落盘并报成功。
            self._flash_status(tr("AnalogTab", "Write failed: {}").format(
                tr("AnalogTab", "rejected by firmware")))
            return False
        self._pending_config = None
        # 0xF2 只写 RAM——标记"有未保存改动"，由"保存到 EEPROM"按钮提交。
        self._dirty = True
        self._update_save_state()
        return True

    def _flush_pending(self):
        """把 150ms 防抖窗内未下发的编辑立即同步写进"当前 selected"的 RAM 槽。

        返回 True 表示窗口内没有待下发内容，或下发成功。
        """
        if self._pending_config is not None:
            self._flush_timer.stop()
            return self.flush_config()
        return True

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
        if self._unread_keys:
            # 有键没读到（占位值只在 RAM/GUI 里）：一旦 0xF6 全量提交，占位值会把
            # 设备上那些键的真实配置覆盖掉。宁可挡住保存。
            self.btn_save.setEnabled(False)
            self.btn_save.setToolTip(tr("AnalogTab", "{} key(s) could not be read; reconnect before saving")
                                     .format(len(self._unread_keys)))
            return
        self.btn_save.setEnabled(self._dirty)
        self.btn_save.setToolTip(
            tr("AnalogTab", "Write current values to keyboard EEPROM") if self._dirty
            else tr("AnalogTab", "All values are saved"))

    def do_save(self):
        """点"保存到 EEPROM"：把 RAM 中全部模拟参数一次性提交(0xF6)。

        0xF2 只改 RAM，拖动期间零 flash 写入；这里先同步补发防抖窗内
        的最后一次编辑，再发 0xF6 全量落盘——整轮调节只产生一次 EEPROM 提交。
        校准(0xF4)/恢复默认(0xF5)不走本按钮：它们在固件侧本就即时落盘。
        """
        if self.device is None or not self._save_supported:
            return
        # 补发防抖窗内的最后一次编辑。若补发失败，绝不能继续 0xF6：
        # 0xF6 是"把当前 RAM 全量落盘"，那笔编辑根本没进设备 RAM，
        # 继续提交只会把旧值写进 EEPROM 并报成功——用户以为存上了，实际丢了。
        if not self._flush_pending():
            self._flash_status(tr("AnalogTab", "Not saved: last change was not written"))
            return
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
        (Highlight.darker(150))，而按键高亮只应表示"当前正按下"，故只用 pressed。

        通用解锁逻辑：secure 固件下 matrix_poll 受 unlock 门控(via.c 的 unlock 检查)，先查
        get_unlock_status，未解锁则显示 Unlock 按钮并清高亮；本固件 VIAL_INSECURE=yes
        时 get_unlock_status 恒 1，解锁 UI 永不出现、matrix_poll 直接可用。

        解锁态按 1 Hz 查询而不是每轮 50 次：该值在一次连接里几乎不变（本固件恒 1），
        而这条协议要跑在蓝牙空口上，省下的是实打实的带宽。用户点 Unlock 后由
        unlock() 主动作废缓存，无需等下一个周期。
        """
        if self.keyboard is None:
            return
        now = time.monotonic()
        if self._unlock_state is None or now - self._unlock_checked >= 1.0:
            try:
                self._unlock_state = self.keyboard.get_unlock_status(3)
            except (RuntimeError, ValueError):
                return
            self._unlock_checked = now
        if not self._unlock_state:
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
            # 刚解锁完，让下一轮 poll 立刻重查而不是走 1 Hz 缓存
            self._unlock_state = None

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
        结果不能写在 lbl_raw 上——那里每 40ms 被轮询刷新一次，用户看不到。
        显示若干秒后自动清空，免得旧结果一直挂着被误读成当前状态。"""
        self.lbl_status.setText(text)
        self._status_timer.start()

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
