# -*- coding: utf-8 -*-
"""简体中文词条表。

键的写法（由 i18n.translate 按此顺序查找）：
    ("Context", "English source")   优先，用于限定上下文、避免一词多义的条目
    "English source"                通用，多个上下文共用同一译名时用它
查不到就原样返回英文，所以漏译永远不会显示成空白。

译名优先沿用老版汉化（program\\Vial\\Vial.exe 里提取出的 115 条术语，
以及老版 qmk_settings.json.bak 里的 62 条设置项文案），标注 [老] 的与老版
一致，便于老用户无缝迁移。老版没有、按 QMK 官方文档语义新译的条目标注了来源。
keycode 名（KC_A / MO(1) / QK_BOOT ...）刻意保持英文：它们是要写进固件的标识。
"""

import logging
import re

_RAW = {
    # ==================== 菜单栏 ====================
    "File": "文件",
    "Keyboard layout": "键盘布局",
    "Security": "安全",
    "Theme": "主题",
    "Language": "语言",
    "About": "关于",
    ("MenuTheme", "System"): "系统",
    ("MenuFile", "Load saved layout..."): "加载已保存的配置...",
    ("MenuFile", "Save current layout..."): "保存当前配置...",
    ("MenuFile", "Sideload VIA JSON..."): "外部载入 VIA JSON...",
    ("MenuFile", "Download VIA definitions"): "下载 VIA 定义",          # [老]
    ("MenuFile", "Load dummy JSON..."): "加载示例 JSON...",
    ("MenuFile", "Exit"): "退出",                                       # [老]
    ("MenuSecurity", "Lock"): "锁定",                                   # [老]
    ("MenuSecurity", "Reboot to bootloader"): "重启到固件刷写模式",      # [老]
    ("MenuAbout", "About Vial..."): "关于 Vial...",

    # ==================== 主窗口 / 页签名 ====================
    "Refresh": "刷新",                                             # [老]
    "Keymap": "键位映射",                                          # [老]
    "Layout": "布局编辑",                                          # [老]
    "Macros": "宏功能",                                            # [老]
    "Lighting": "灯光管理",                                        # [老]
    "Tap Dance": "多用键",                                         # [老]
    "Combos": "并击设置",                                          # [老]
    "Key Overrides": "键值覆盖",                                   # [老]
    "Alt Repeat Key": "替代重复键",
    "QMK Settings": "设置",                                        # [老]
    "Matrix tester": "矩阵测试",                                   # [老]
    "Analog": "模拟量",
    "Firmware updater": "固件更新",                                # [老]
    "About {}...": "关于 {}...",
    "In order to fully apply the theme you should restart the application.":
        "完整应用当前主题需要重启程序。",                            # [老]
    "In order to fully apply the language you should restart the application.":
        "切换语言后需要重启程序才能完整生效。",
    'No devices detected. Connect a Vial-compatible device and press "Refresh"<br>'
    'or select "File" → "Download VIA definitions" in order to enable support for VIA keyboards.':
        '未检测到设备。请连接一台兼容 Vial 的设备并点击“刷新”<br>'
        '或在菜单中选择“文件” → “下载 VIA 定义”，以启用对 VIA 键盘的支持。',
    '<br><br>On Linux you need to set up a custom udev rule for keyboards to be detected. '
    'Follow the instructions linked below:<br>'
    '<a href="https://get.vial.today/manual/linux-udev.html">'
    'https://get.vial.today/manual/linux-udev.html</a>':
        '<br><br>在 Linux 上需要为键盘设置自定义 udev 规则才能被检测到。'
        '请参照以下链接中的说明：<br>'
        '<a href="https://get.vial.today/manual/linux-udev.html">'
        'https://get.vial.today/manual/linux-udev.html</a>',

    # ==================== 解锁对话框 ====================
    "In order to proceed, the keyboard must be set into unlocked mode.\n"
    "You should only perform this operation on computers that you trust.":
        "要继续操作，必须先把键盘切换到解锁模式。\n请仅在信任的计算机上执行此操作。",
    "To exit this mode, you will need to replug the keyboard\n"
    "or select Security->Lock from the menu.":
        "要退出该模式，需要重新插拔键盘数据线，\n或在菜单中选择“安全 → 锁定”。",
    "Press and hold the following keys until the progress bar below fills up:":
        "按住以下按键，直到下方进度条走满：",                        # [老]

    # ==================== 键位/按键面板 ====================
    ("KeycodePanel", "Any"): "任意",
    ("TabbedKeycodes", "Basic"): "基本按键",                            # [老]
    ("TabbedKeycodes", "ISO/JIS"): "ISO/JIS",
    ("TabbedKeycodes", "Layers"): "层控制",                             # [老]
    ("TabbedKeycodes", "Quantum"): "功能键",
    ("TabbedKeycodes", "Backlight"): "灯光控制",                        # [老]
    ("TabbedKeycodes", "Bluetooth/Wireless"): "蓝牙/无线",
    ("TabbedKeycodes", "App, Media and Mouse"): "程序和鼠标",            # [老]
    ("TabbedKeycodes", "MIDI"): "MIDI",
    ("TabbedKeycodes", "Tap Dance"): "多用键",                          # [老]
    ("TabbedKeycodes", "User"): "用户按键",                             # [老]
    ("TabbedKeycodes", "Macro"): "宏按键",                              # [老]
    ("Combos", "Key {}"): "按键 {}",
    "Output key": "输出按键",                                      # [老]

    # ==================== 通用按钮 ====================
    "Save": "保存",                                                # [老]
    "Revert": "恢复",                                              # [老]
    "Undo": "撤销",                                                # [老]
    "Reset": "重置",                                               # [老]
    "Unlock": "解锁",                                              # [老]
    "Apply": "应用",                                               # [老]
    "Cancel": "取消",                                              # [老]
    "Copy": "复制",                                                # [老]
    "Paste": "粘贴",                                               # [老]
    "Options": "选项",                                             # [老]
    "Enable": "启用功能",                                          # [老]

    # ==================== 键值覆盖 ====================
    "Enable all": "启用全部",                                      # [老]
    "Disable all": "禁用全部",                                     # [老]
    "Enable on layers": "在指定层上启用",                          # [老]
    "Trigger": "触发按键",                                         # [老]
    "Trigger mods": "触发修饰键",                                  # [老]
    "Negative mods": "失效修饰键",                                 # [老]
    "Suppressed mods": "禁用修饰键",                               # [老]
    "Replacement": "替换按键",
    "Activate when the trigger key is pressed down": "在触发按键按下时激活",
    "Activate when a necessary modifier is pressed down": "在触发修饰键按下时激活",
    "Activate when a negative modifier is released": "在失效修饰键抬起时激活",
    "Activate on one modifier": "任意修饰键按下时激活",
    "Don't deactivate when another key is pressed down": "仅当其他按键按下时失效",
    "Don't register the trigger key again after the override is deactivated":
        "键值覆盖失效后不再触发",

    # ==================== 替代重复键（术语按用户指定）====================
    "Last key": "上次按键",
    "Alt key": "替代键",
    "Allowed mods": "允许的修饰键",
    "Default to this alt key": "默认使用此替代键",
    "Bidirectional": "交换上次按键和替代键也生效",
    "Ignore mod handedness": "不区分左右修饰键",

    # ==================== QMK 设置（来源：老版 qmk_settings.json.bak）====================
    # 页签名单独看易歧义，故用限定上下文
    ("QmkSettings", "Magic"): "魔法按键",                            # [老]
    ("QmkSettings", "Grave Escape"): "Grave-Esc两用键",              # [老]
    ("QmkSettings", "Tap-Hold"): "短按-按住",                        # [老]
    ("QmkSettings", "Auto Shift"): "自动Shift键",                    # [老]
    ("QmkSettings", "Combo"): "并击",                                # [老]
    ("QmkSettings", "One Shot Keys"): "粘滞键",                      # [老]
    ("QmkSettings", "Mouse keys"): "鼠标键",                         # [老]
    # 魔法按键
    "Swap Caps Lock and Left Control": "交换Caps键和左Ctrl键",       # [老]
    "Treat Caps Lock as Control": "将Caps键用作Ctrl键",               # [老]
    "Swap Left Alt and GUI": "交换左Alt键和GUI键",                   # [老]
    "Swap Right Alt and GUI": "交换右Alt键和GUI键",                  # [老]
    "Disable the GUI keys": "禁用GUI键",                             # [老]
    "Swap ` and Escape": "交换~键和Esc键",                           # [老]
    "Swap \\ and Backspace": "交换\\键和退格键",                     # [老]
    "Enable N-key rollover": "开启全键无冲",                          # [老]
    "Swap Left Control and GUI": "交换左Ctrl键和GUI键",              # [老]
    "Swap Right Control and GUI": "交换右Ctrl键和GUI键",             # [老]
    # Grave-Esc 两用键
    "Always send Escape if Alt is pressed": "Alt键按下时发送Esc键",   # [老]
    "Always send Escape if Control is pressed": "Ctrl键按下时发送Esc键",  # [老]
    "Always send Escape if GUI is pressed": "GUI键按下时发送Esc键",   # [老]
    "Always send Escape if Shift is pressed": "Shift键按下时发送Esc键",  # [老]
    # 短按-按住
    "Tapping Term": "短按判定时间",                                  # [老]
    "Permissive Hold": "允许短按触发按住",                            # [老]
    "Ignore Mod Tap Interrupt": "忽略修饰键按键中断",                 # [老，原文残缺已补全]
    "Tapping Force Hold": "多次按下触发按住",                         # [老]
    "Retro Tapping": "按住后无动作恢复短按",                          # [老]
    "Hold On Other Key Press": "按下其他按键时判定为按住",             # 新增，QMK 官方文档
    "Quick Tap Term": "快速短按判定时间（0 禁用自动重复）",            # 新增，QMK 官方文档
    "Tap Code Delay": "按下抬起按键延迟",                             # [老]
    "Tap Hold Caps Delay": "激活Cap键延迟时间",                      # [老]
    "Tapping Toggle": "多次按下次数设置",                             # [老]
    "Chordal Hold": "和弦按住（同手判定为短按）",                      # 新增，QMK 官方文档
    "Flow Tap": "连打短按（抑制按住判定）",                           # 新增，QMK 官方文档
    # 自动 Shift
    "Enable for modifiers": "对修饰键启用",                           # [老]
    "Timeout": "按下触发时间",                                       # [老]
    "Do not Auto Shift special keys": "对符号键不启用",               # [老]
    "Do not Auto Shift numeric keys": "对数字键不启用",               # [老]
    "Do not Auto Shift alpha characters": "对字母键不启用",           # [老]
    "Enable keyrepeat": "启用按键重复",                               # [老]
    "Disable keyrepeat when timeout is exceeded": "超出触发时间后停用按键重复",  # [老]
    # 并击
    "Time out period for combos": "并击触发时间",                    # [老]
    # 粘滞键
    "Tapping this number of times holds the key until tapped once again":
        "触发粘滞键的按下次数（再次按下释放）",                        # [老]
    "Time (in ms) before the one shot key is released":
        "粘滞键保持时间（毫秒）",                                     # [老]
    # 鼠标键
    "Delay between pressing a movement key and cursor movement":
        "方向键与指针移动间隔",                                       # [老]
    "Time between cursor movements in milliseconds": "指针移动间隔（毫秒）",  # [老]
    "Step size": "指针移动步长",                                     # [老]
    "Maximum cursor speed at which acceleration stops": "无加速最大指针速度",  # [老]
    "Time until maximum cursor speed is reached": "最大指针速度加速时间",  # [老]
    "Delay between pressing a wheel key and wheel movement": "滚轮按下与滚轮移动间隔",  # [老]
    "Time between wheel movements": "滚轮移动间隔（毫秒）",           # [老]
    "Maximum number of scroll steps per scroll action": "单次滚动操作最大步数",  # [老]
    "Time until maximum scroll speed is reached": "最大滚轮速度加速时间",  # [老]

    # ==================== 多用键(Tap Dance) ====================
    "On tap": "短按",                                              # [老]
    "On hold": "按住",                                             # [老]
    "On double tap": "双击",                                       # [老]
    "On tap + hold": "短按并按住",                                  # [老]
    "Tapping term (ms)": "短按判定时间 (毫秒)",
    "Use <code>TD({})</code> to set up this action in the keymap.":
        "在键位映射中使用 <code>TD({})</code> 来关联此操作。",

    # ==================== 宏 ====================
    "Record macro": "录制宏",                                      # [老]
    "Stop recording": "停止记录",                                  # [老]
    "Add action": "添加动作",                                      # [老]
    "Append to current": "附加到当前",                             # [老]
    "Replace everything": "替换全部",                              # [老]
    "Tap Enter": "按下 Enter",
    "Open Text Editor...": "打开文本编辑器...",
    "Memory used by macros: {}/{}": "宏占用空间：{}/{}",

    # ==================== 灯光 ====================
    "Underglow Effect": "底灯灯效",                                # [老]
    "Underglow Brightness": "底灯亮度",                            # [老]
    "Underglow Color": "底灯颜色",                                 # [老]
    "Backlight Brightness": "按键灯亮度",                          # [老]
    "Backlight Breathing": "按键灯呼吸",                           # [老]
    "RGB Effect": "灯效",                                          # [老]
    "RGB Color": "颜色",                                           # [老]
    "RGB Brightness": "亮度",                                      # [老]
    "RGB Speed": "速度",                                           # [老]

    # ==================== 刷写 / 设置 / 测试 ====================
    "Select file...": "选择文件...",                               # [老]
    "Flash": "刷写固件",                                           # [老]
    "Restore current layout after flashing": "刷写固件后恢复当前配置",
    "Reset all settings to default values?": "确定要把所有设置恢复为默认值吗？",
    "Unlock the keyboard before testing:": "测试前请先解锁键盘：",   # [老]
    "Layer": "图层",
    "Saved keymap belongs to a different keyboard, are you sure you want to continue?":
        "保存的键位配置属于另一把键盘，确定要继续吗？",

    # ==================== 任意键值对话框 ====================
    "Enter an arbitrary keycode": "输入任意键值",                   # [老]
    "Enter an expression": "输入表达式",
    "Invalid input": "无效输入",                                    # [老]
    "Invalid input: {}": "无效输入：{}",
    "Computed value: 0x{:X}": "对应键值：0x{:X}",
    "About {}": "关于 {}",

    # ==================== 模拟量(磁轴/静电容) ====================
    "Not connected / this keyboard has no analog backend (ANALOG_ENABLE)":
        "未连接 / 该键盘没有模拟量后端（ANALOG_ENABLE）",
    "Per-key settings": "每键参数",
    "Global parameters": "全局参数",
    "Not connected": "未连接",
    "Travel: {}/255  |  Raw ADC: {}": "行程：{}/255  |  原始 ADC：{}",
    "Enable Rapid Trigger (RT)": "启用 Rapid Trigger (RT)",
    "Sample rest position (all keys)": "采样释放位置（全部）",
    "Sample bottom-out (all keys)": "采样到底位置（全部）",
    "Auto-calibrate (all keys)": "自动校准（全部）",
    "Reset to global default": "重置为全局默认",
    "Reset all (factory)": "全局重置（出厂）",
    "Axis: {} | Keys: {} | Travel: 0-255": "轴类型：{} | 键数：{} | 行程：0-255",
    "Key #{} (row {}, col {})": "按键 #{}（行 {}，列 {}）",
    "raw: rest={} bottom={} (calibration)": "raw: 释放={} 到底={}（校准值）",
    "Sampled rest position raw={}": "已采样释放位置 raw={}",
    "Sampled bottom-out raw={}": "已采样到底位置 raw={}",
    "Auto-calibration done raw={}": "自动校准完成 raw={}",
    ("AnalogTab", "Actuation point"): "触发点",
    ("AnalogTab", "Release point"): "断开点",
    ("AnalogTab", "RT down sensitivity"): "RT 下行灵敏度",
    ("AnalogTab", "RT up sensitivity"): "RT 上行灵敏度",
    ("AnalogTab", "none"): "无",
    ("AnalogTab", "Hall effect"): "磁轴 (Hall)",
    ("AnalogTab", "Electrostatic"): "静电容 (EC)",
}


def _placeholders(text):
    """取出 format 占位符集合，用于校验译文没有把 {} 弄丢/写错。"""
    return sorted(re.findall(r"\{[^{}]*\}", text) + re.findall(r"%[sd]", text))


def _source_of(key):
    """取出一条词条对应的英文原文（限定键的第一个元素是上下文，不参与校验）。"""
    return key[1] if isinstance(key, tuple) else key


def _check(table):
    """丢掉占位符不匹配的条目并告警。

    译文表以后会被人工编辑，少写一个 {} 就会让调用点 .format() 直接抛
    IndexError / KeyError，把界面刷崩；这里宁可退回英文也不能崩。
    """
    ok = {}
    for key, value in table.items():
        if not value:
            continue
        want = _placeholders(_source_of(key))
        got = _placeholders(value)
        if want != got:
            logging.warning("i18n: placeholder mismatch, entry ignored: %r (%r != %r)",
                            _source_of(key)[:60], want, got)
            continue
        ok[key] = value
    return ok


ZH = _check(_RAW)
