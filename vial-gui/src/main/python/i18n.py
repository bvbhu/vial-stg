# SPDX-License-Identifier: GPL-2.0-or-later
"""
轻量运行时翻译层（Vial 上游只留了个空壳 tr()，从不安装 QTranslator）。

用法不变，全站继续写 `tr("Context", "English source")`：

    tr("MainWindow", "Refresh")     ->  中文模式返回 "刷新"
                                       英文模式 / 表里没有该条时原样返回英文

查表顺序： "Context|English" -> "English" -> 原文（保证漏译只会掉回英文，
不会显示空白，也不会影响 keycode 名等刻意保持英文的内容）。

语言解析优先级：
    环境变量 VIAL_LANG  >  QSettings(SETTINGS_ORG, SETTINGS_APP)["language"]  >  系统 locale

注意：翻译发生在控件构造期，所以切换语言需要重启程序（与主题切换的行为一致，
沿用同一个提示交互）。
"""
import logging
import os

from PyQt5.QtCore import QSettings, QLocale

from constants import SETTINGS_APP, SETTINGS_ORG

EN = "en"
ZH = "zh"

# 语言菜单里显示的是各语言自己的写法（endonym），所以这里不经过 tr()
_LANGUAGES = [
    (EN, "English"),
    (ZH, "简体中文"),
]

_cached = None


def languages():
    """返回 [(code, 显示名), ...]，供语言菜单使用。"""
    return list(_LANGUAGES)


def get_language():
    global _cached
    if _cached is None:
        _cached = _detect()
    return _cached


def set_language(code):
    """程序内切换（不写设置），由 MainWindow.set_language() 调用。"""
    global _cached
    if code in (EN, ZH):
        _cached = code
    return _cached


def _normalize(value):
    if not value:
        return None
    value = str(value).strip().lower().replace("-", "_")
    if value in ("zh", "zh_cn", "zh_hans", "chinese", "cn"):
        return ZH
    if value in ("en", "en_us", "en_gb", "english"):
        return EN
    return None


def _detect():
    env = _normalize(os.environ.get("VIAL_LANG"))
    if env:
        logging.debug("i18n: language from VIAL_LANG=%s", env)
        return env

    try:
        saved = _normalize(QSettings(SETTINGS_ORG, SETTINGS_APP).value("language", None))
    except Exception:
        logging.exception("i18n: cannot read language setting")
        saved = None
    if saved:
        logging.debug("i18n: language from settings=%s", saved)
        return saved

    try:
        loc = (QLocale.system().name() or "").lower()
    except Exception:
        loc = ""
    detected = ZH if loc.startswith("zh") else EN
    logging.debug("i18n: language from locale %r -> %s", loc, detected)
    return detected


def translate(context, source, *args, **kwargs):
    """签名与 QCoreApplication.translate(context, text, disambiguation, n) 兼容。

    注意：与 Qt 原版**不等价**。Qt 会用 disambiguation 区分同文案不同语义的条目，
    并支持 %n 复数形式（按 n 选单/复数译文）；本实现两者都不做，多余参数直接丢弃。
    目前工程里没有用到这两项，但新增调用时若传了参数，会在这里留一条 warning——
    否则它会静默失效（译文照出，复数/消歧没生效），很难查。
    """
    if (args or kwargs) and source:
        logging.warning(
            "i18n: translate(%r, %r) 收到 Qt 专有参数 args=%r kwargs=%r，本实现会忽略它们"
            "（disambiguation 与 %%n 复数未支持）",
            context, source, args, kwargs,
        )
    if not isinstance(source, str) or not source:
        return source
    if get_language() != ZH:
        return source

    table = _table()
    if table is None:
        return source

    # 先查限定到上下文的条目 (context, source)，再查通用条目 source。
    # 用元组而不是 "Ctx|English" 这种拼接键：UI 文案里本来就可能出现 "|"。
    hit = table.get((context, source))
    if hit is None:
        hit = table.get(source)
    return hit if hit else source


_TABLE = None
_TABLE_TRIED = False


def _table():
    global _TABLE, _TABLE_TRIED
    if not _TABLE_TRIED:
        _TABLE_TRIED = True
        try:
            from i18n_zh import ZH as table
        except Exception:
            logging.exception("i18n: translation table unavailable")
            table = None
        _TABLE = table
    return _TABLE


def stats():
    """调试/自检用：表里有多少条、当前语言。"""
    table = _table() or {}
    return {"language": get_language(), "entries": len(table)}
