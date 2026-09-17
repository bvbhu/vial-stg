# SPDX-License-Identifier: GPL-2.0-or-later
"""Vial Analog Protocol Extension 客户端 (0xF0-0xF6)。

固件侧契约见 vial-qmk-stg/docs/vial-analog-protocol.md。

行程域是 0..max_travel 的整数刻度（释放≈0 / 触底=max_travel），轴体无关
（Hall/EC 共用）。max_travel 不是固定 255：它由固件 ANALOG_MAX_TRAVEL 决定，
经 0xF0 caps 的 msg[8..9] 上报。本模块据此选择线格式的字段宽度——
满量程 <=255 用 uint8（配置 12 字节、0xF3 每条 3 字节），否则用 uint16
（16 字节 / 4 字节）——调用方只需拿 caps 里的值，不必自己判断宽度。

用法：先 analog_get_caps()（它会缓存满量程），后续所有读写自动按该宽度收发。
需要重复取 caps（如 .vil 保存/恢复）时走 analog_caps()，它带实例级缓存，不会每次
都花一次 HID 事务。"""

import struct

from protocol.constants import (
    CMD_VIA_VIAL_PREFIX, CMD_VIAL_ANALOG_GET_CAPS, CMD_VIAL_ANALOG_GET_KEY_CONFIG,
    CMD_VIAL_ANALOG_SET_KEY_CONFIG, CMD_VIAL_ANALOG_GET_KEY_READINGS,
    CMD_VIAL_ANALOG_CALIBRATE, CMD_VIAL_ANALOG_RESET_KEY, CMD_VIAL_ANALOG_PERSIST_COMMIT,
    ANALOG_FLAG_ACTUATION_OVERRIDE, ANALOG_FLAG_RT_ENABLED,
    ANALOG_TRAVEL_NARROW_MAX, ANALOG_PROTOCOL_VERSION,
)

# 默认满量程：尚未握手（未读到 caps）时的保守值，与固件默认 ANALOG_MAX_TRAVEL 一致。
ANALOG_DEFAULT_MAX_TRAVEL = 255

# 每键配置的两种线格式，字段顺序见固件 vial_analog_wire_config_t：
#   4 项行程域阈值(触发/断开/RT 触发距离/RT 释放距离) + flags + reserved
#   + raw_rest/raw_full(原始 ADC 域锚点) + reserved2
_ANALOG_CONFIG_BYTES_NARROW = 12
_ANALOG_CONFIG_BYTES_WIDE = 16

_CONFIG_FMT_NARROW = "<BBBBBBHHH"
_CONFIG_FMT_WIDE = "<HHHHBBHHH"

# 线格式字节数 -> struct 格式串。0xF0 caps 的 msg[6] 声明的就是这个字节数；
# 固件侧尺寸由静态断言绑死在同一组取值上，出现别的值即视为不支持本协议。
_CONFIG_FMT_BY_BYTES = {
    _ANALOG_CONFIG_BYTES_NARROW: _CONFIG_FMT_NARROW,
    _ANALOG_CONFIG_BYTES_WIDE: _CONFIG_FMT_WIDE,
}


def analog_config_bytes(max_travel):
    """caps 里应出现的每键配置字节数：12（uint8 行程域）/ 16（uint16 行程域）。

    这个值是 caps.msg[6] 的期望取值，用于校验固件声明。
    仅按满量程推导；线格式的最终尺寸由 analog_config_size() 从实际 fmt 算出。
    """
    return _ANALOG_CONFIG_BYTES_WIDE if analog_travel_is_wide(max_travel) else _ANALOG_CONFIG_BYTES_NARROW


def analog_travel_is_wide(max_travel):
    """行程域是否升到 uint16。与固件 analog_core.h 的判据逐字对应（<=255 为窄）。"""
    return int(max_travel) > ANALOG_TRAVEL_NARROW_MAX


def _config_fmt(max_travel):
    return _CONFIG_FMT_WIDE if analog_travel_is_wide(max_travel) else _CONFIG_FMT_NARROW


def analog_config_size(max_travel):
    """每键配置的线格式字节数：12（uint8 行程域）或 16（uint16 行程域）。"""
    return struct.calcsize(_config_fmt(max_travel))


def analog_reading_entry_size(max_travel):
    """0xF3 单条读数的字节数 = 行程（sizeof(analog_travel_t)）+ 原始读数 2 字节。"""
    return 4 if analog_travel_is_wide(max_travel) else 3


class AnalogKeyConfig:
    """每键配置 + 校准锚点，与固件 vial_analog_wire_config_t 一致（小端）。

    from_bytes/to_bytes 必须带 max_travel：字段宽度随行程域走，故意不设默认值，
    强制调用方从 caps 取，而不是默默按 255 解析 16 字节的包。
    """

    __slots__ = ("actuation_point", "release_point", "rt_down", "rt_up",
                 "flags", "raw_rest", "raw_full")

    def __init__(self):
        self.actuation_point = 120
        self.release_point = 80
        self.rt_down = 10
        self.rt_up = 10
        self.flags = 0
        self.raw_rest = 0
        self.raw_full = 255

    @classmethod
    def from_bytes(cls, b, max_travel):
        c = cls()
        size = analog_config_size(max_travel)
        (c.actuation_point, c.release_point, c.rt_down, c.rt_up, c.flags, _,
         c.raw_rest, c.raw_full, _) = struct.unpack(_config_fmt(max_travel), b[:size])
        return c

    def to_bytes(self, max_travel):
        return struct.pack(_config_fmt(max_travel), self.actuation_point, self.release_point,
                           self.rt_down, self.rt_up, self.flags, 0,
                           self.raw_rest, self.raw_full, 0)

    def is_rt_enabled(self):
        return bool(self.flags & ANALOG_FLAG_RT_ENABLED)

    def is_customized(self):
        """本键是否已单独调节（ACTUATION_OVERRIDE 标志位）。

        GUI 全局调节时跳过带此位的键；固件状态机忽略此位（直接
        用每键 actuation_point），故纯 GUI 标记，无需固件配合。
        """
        return bool(self.flags & ANALOG_FLAG_ACTUATION_OVERRIDE)


class ProtocolAnalog:
    """混入类：挂到 Keyboard 上，提供 0xF0-0xF6 命令封装。

    analog_get_caps() 会把固件上报的满量程缓存在实例上，后续读写全部据此选宽度。
    会话/设备重建即是新实例，缓存随之作废——固件是编译期常量，同一设备不会变。
    """

    def analog_max_travel(self):
        """当前设备的行程域满量程。未握手时按默认 255（窄格式）。"""
        return getattr(self, "_analog_max_travel", ANALOG_DEFAULT_MAX_TRAVEL)

    def analog_caps(self, refresh=False):
        """取 caps，并在实例上缓存。

        caps 除 msg[7]（触底校准开关，运行态）外全是编译期常量
        （见协议文档 §1.2），一次连接内不会变；而 `.vil` 保存/恢复这类
        路径会反复问它。缓存后非 analog 固件不再每次保存都被白问一遍
        `0xF0`，也没有了"同一会话里两次 caps 结果不同"的窗口。
        refresh=True 供需要重读的场景：目前调用方是 AnalogTab 重握手
        (editor/analog_tab.py 的 analog_caps(refresh=True))——设备可能已更换，
        caps 里的 msg[7] 又是运行态，不能沿用旧缓存。
        """
        cached = getattr(self, "_analog_caps", None)
        if cached is not None and not refresh:
            return cached
        caps = self.analog_get_caps()
        self._analog_caps = caps
        return caps

    def analog_get_caps(self):
        data = self.usb_send(self.dev, struct.pack("BB", CMD_VIA_VIAL_PREFIX, CMD_VIAL_ANALOG_GET_CAPS),
                             retries=20)
        caps = {
            "version": data[0],
            "num_keys": data[1] | (data[2] << 8),
            "axis_type": data[3],
            "caps": data[4],
            "max_readings": data[5],
            "config_size": data[6],
            # 固件触底校准开关的运行态(msg[7])：GUI 重启后能把开关对上
            "bottom_out": data[7],
            # 行程域满量程(msg[8..9] 小端)
            "max_travel": data[8] | (data[9] << 8),
        }
        # 满量程为 0 属异常/不支持的固件：回落到默认值，免得后面所有宽度判断
        # 都按 0 走（0 会让行程域宽度判断与各种比例换算全部失去意义）。
        if not caps["max_travel"]:
            caps["max_travel"] = ANALOG_DEFAULT_MAX_TRAVEL
        # 固件在 caps.msg[6] 里声明的配置字节数必须与满量程推导出的宽度自洽。
        # 二者由固件同一条判据推出、并由静态断言绑死；一旦不一致，后续按错误
        # 偏移解析整条配置都是错的——宁可在这里就认定"不支持"。
        caps["config_bytes_ok"] = (caps["config_size"] == analog_config_bytes(caps["max_travel"]))
        self._analog_max_travel = caps["max_travel"]
        self._analog_num_keys = caps["num_keys"]
        return caps

    def analog_get_key_config(self, ki, retries=20):
        """读单键配置。批量遍历（如 _load_all_configs 的 0xF1 × num_keys）应显式
        传小 retries：默认的 20 次重试是给单次交互用的，叠加到几十上百次批量读上，
        设备掉线时会把 GUI 线程阻塞到看起来像卡死。

        越界 ki 在协议里没有可靠错误码：固件对越界返回 msg[0]=1，而已解锁的正常
        响应 msg[0] 也可能是 1（= 触发行程 1），无法区分（协议文档 §0xF1 已声明）。
        故这里主动挡掉，不把请求发出去靠回包猜。
        """
        num_keys = getattr(self, "_analog_num_keys", 0)
        if ki != 0xFFFF and num_keys and ki >= num_keys:
            raise ValueError("analog key index %d out of range (num_keys=%d)" % (ki, num_keys))
        data = self.usb_send(self.dev, struct.pack("<BBH", CMD_VIA_VIAL_PREFIX, CMD_VIAL_ANALOG_GET_KEY_CONFIG, ki),
                             retries=retries)
        return AnalogKeyConfig.from_bytes(data, self.analog_max_travel())

    def analog_set_key_config(self, ki, cfg):
        data = self.usb_send(self.dev, struct.pack("<BBH", CMD_VIA_VIAL_PREFIX, CMD_VIAL_ANALOG_SET_KEY_CONFIG, ki)
                             + cfg.to_bytes(self.analog_max_travel()), retries=20)
        return data[0] == 0

    def analog_get_global_config(self):
        """读固件 EEPROM 全局默认配置（非编译期常量）。"""
        return self.analog_get_key_config(0xFFFF)

    def analog_set_global_config(self, cfg):
        """写固件全局默认配置（仅改 RAM，点"保存"后才落 EEPROM；固件自动清 ACTUATION_OVERRIDE 位）。"""
        return self.analog_set_key_config(0xFFFF, cfg)

    def analog_get_key_readings(self, start_ki):
        """返回 [(travel, raw), ...]，每条 = 行程(1 或 2 字节 LE) + raw 16bit LE。"""
        data = self.usb_send(self.dev, struct.pack("<BBH", CMD_VIA_VIAL_PREFIX, CMD_VIAL_ANALOG_GET_KEY_READINGS, start_ki),
                             retries=3)
        n = data[0]
        max_travel = self.analog_max_travel()
        wide = analog_travel_is_wide(max_travel)
        stride = analog_reading_entry_size(max_travel)
        readings = []
        for i in range(n):
            o = 1 + i * stride
            if wide:
                travel = data[o] | (data[o + 1] << 8)
                o += 2
            else:
                travel = data[o]
                o += 1
            raw = data[o] | (data[o + 1] << 8)
            readings.append((travel, raw))
        return readings

    def analog_calibrate(self, mode, ki):
        """ki=0xFFFF 表示全部键。返回 (ok, sampled_raw)。"""
        data = self.usb_send(self.dev, struct.pack("<BBBH", CMD_VIA_VIAL_PREFIX, CMD_VIAL_ANALOG_CALIBRATE, mode, ki),
                             retries=20)
        sampled = data[1] | (data[2] << 8)
        return data[0] == 0, sampled

    def analog_reset_key(self, ki):
        data = self.usb_send(self.dev, struct.pack("<BBH", CMD_VIA_VIAL_PREFIX, CMD_VIAL_ANALOG_RESET_KEY, ki),
                             retries=20)
        return data[0] == 0

    def analog_persist_commit(self):
        """0xF6：把当前 RAM 中的模拟配置全量提交到 EEPROM（GUI"保存"按钮）。

        固件按记录比对后仅对差异页做真实擦写，重复保存不额外磨损。
        """
        data = self.usb_send(self.dev, struct.pack("BB", CMD_VIA_VIAL_PREFIX, CMD_VIAL_ANALOG_PERSIST_COMMIT),
                             retries=20)
        return data[0] == 0
