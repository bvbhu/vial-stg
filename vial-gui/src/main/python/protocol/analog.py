# SPDX-License-Identifier: GPL-2.0-or-later
"""Vial Analog Protocol Extension 客户端 (0xF0-0xF5)。

固件侧契约见 vial-qmk-wireless/docs/vial-analog-protocol.md。
行程单位 0-255(释放≈0 / 到底≈255)，轴体无关(Hall/EC 共用)。
"""

import struct

from protocol.constants import CMD_VIA_VIAL_PREFIX, CMD_VIAL_ANALOG_GET_CAPS, \
    CMD_VIAL_ANALOG_GET_KEY_CONFIG, CMD_VIAL_ANALOG_SET_KEY_CONFIG, \
    CMD_VIAL_ANALOG_GET_KEY_READINGS, CMD_VIAL_ANALOG_CALIBRATE, CMD_VIAL_ANALOG_RESET_KEY, \
    ANALOG_FLAG_ACTUATION_OVERRIDE

ANALOG_CONFIG_SIZE = 12


class AnalogKeyConfig:
    """12 字节每键配置 + 校准，与固件 analog_key_config_t 一致(小端)。"""

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
    def from_bytes(cls, b):
        c = cls()
        (c.actuation_point, c.release_point, c.rt_down, c.rt_up, c.flags, _,
         c.raw_rest, c.raw_full, _) = struct.unpack("<BBBBBBHHH", b[:ANALOG_CONFIG_SIZE])
        return c

    def to_bytes(self):
        return struct.pack("<BBBBBBHHH", self.actuation_point, self.release_point,
                           self.rt_down, self.rt_up, self.flags, 0,
                           self.raw_rest, self.raw_full, 0)

    def is_rt_enabled(self):
        return bool(self.flags & (1 << 0))

    def is_customized(self):
        """本键是否已单独调节（ACTUATION_OVERRIDE 标志位）。

        GUI 全局调节时跳过带此位的键；固件状态机忽略此位（直接
        用每键 actuation_point），故纯 GUI 标记，无需固件配合。
        """
        return bool(self.flags & ANALOG_FLAG_ACTUATION_OVERRIDE)


class ProtocolAnalog:
    """混入类：挂到 Keyboard 上，提供 0xF0-0xF5 命令封装。"""

    def analog_get_caps(self):
        data = self.usb_send(self.dev, struct.pack("BB", CMD_VIA_VIAL_PREFIX, CMD_VIAL_ANALOG_GET_CAPS),
                             retries=20)
        return {
            "version": data[0],
            "num_keys": data[1] | (data[2] << 8),
            "axis_type": data[3],
            "caps": data[4],
            "max_readings": data[5],
            "config_size": data[6],
            # 固件触底校准开关的运行态(msg[7])：GUI 重启后能把开关对上；旧固件恒 0
            "bottom_out": data[7],
        }

    def analog_get_key_config(self, ki):
        data = self.usb_send(self.dev, struct.pack("<BBH", CMD_VIA_VIAL_PREFIX, CMD_VIAL_ANALOG_GET_KEY_CONFIG, ki),
                             retries=20)
        return AnalogKeyConfig.from_bytes(data)

    def analog_set_key_config(self, ki, cfg):
        data = self.usb_send(self.dev, struct.pack("<BBH", CMD_VIA_VIAL_PREFIX, CMD_VIAL_ANALOG_SET_KEY_CONFIG, ki)
                             + cfg.to_bytes(), retries=20)
        return data[0] == 0

    def analog_get_global_config(self):
        """读固件 EEPROM 全局默认配置（v2：非编译期常量）。"""
        return self.analog_get_key_config(0xFFFF)

    def analog_set_global_config(self, cfg):
        """写固件 EEPROM 全局默认配置（固件自动清 ACTUATION_OVERRIDE 位）。"""
        return self.analog_set_key_config(0xFFFF, cfg)

    def analog_get_key_readings(self, start_ki):
        """返回 [(travel, raw), ...]，每条 3 字节：travel 0-255 + raw 16bit LE。"""
        data = self.usb_send(self.dev, struct.pack("<BBH", CMD_VIA_VIAL_PREFIX, CMD_VIAL_ANALOG_GET_KEY_READINGS, start_ki),
                             retries=3)
        n = data[0]
        readings = []
        for i in range(n):
            travel = data[1 + i * 3]
            raw = data[2 + i * 3] | (data[3 + i * 3] << 8)
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
