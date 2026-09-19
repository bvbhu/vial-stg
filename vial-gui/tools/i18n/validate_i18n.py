# -*- coding: utf-8 -*-
"""校验翻译表：每个 tr() key 是否命中中文；_check 是否丢了条目。"""
import ast, glob, json, os, sys, importlib

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
SRC = os.path.join(ROOT, "src", "main", "python")
os.chdir(ROOT)
sys.path.insert(0, SRC)

i18n_zh = importlib.import_module("i18n_zh")
ZH, RAW = i18n_zh.ZH, i18n_zh._RAW

dropped = [k for k in RAW if k not in ZH]
print("_RAW=%d  ZH=%d  _check 丢弃=%d" % (len(RAW), len(ZH), len(dropped)))
for k in dropped:
    print("   !! 被丢弃:", repr(k)[:70], "->", repr(RAW[k])[:70])

DYNAMIC = {
    "MainWindow": ["Keymap", "Layout", "Macros", "Lighting", "Tap Dance", "Combos",
                   "Key Overrides", "Alt Repeat Key", "QMK Settings", "Matrix tester",
                   "Analog", "Firmware updater"],
    "MenuTheme": ["System", "Light", "Dark", "Arc", "Nord", "Olivia", "Dracula", "Bliss",
                  "Catppuccin Latte", "Catppuccin Frappe", "Catppuccin Macchiato", "Catppuccin Mocha"],
    "KeycodePanel": ["Any"],
    "TabbedKeycodes": ["Basic", "ISO/JIS", "Layers", "Quantum", "Backlight",
                       "Wireless", "App, Media and Mouse", "MIDI", "Tap Dance",
                       "User", "Macro"],
    "AnalogTab": ["Actuation point", "Release point", "RT down sensitivity",
                  "RT up sensitivity", "none", "Hall effect", "Electrostatic"],
}

# 设置页的文案在资源 JSON 里（页签名 + 各字段标题），直接读它，避免两份清单各自漂移
with open(os.path.normpath(os.path.join(SRC, "..", "resources", "base",
                                        "qmk_settings.json")), encoding="utf-8") as _inf:
    _qmk = json.load(_inf)
DYNAMIC["QmkSettings"] = [t["name"] for t in _qmk["tabs"]] + \
                         [f["title"] for t in _qmk["tabs"] for f in t["fields"]]


# 有意不翻译的：主题名是专有名词；"{} (0-255)" 只是外壳，内层标签已单独翻译
EXPECTED_UNTRANSLATED = {
    ("MenuTheme", "Light"), ("MenuTheme", "Dark"), ("MenuTheme", "Arc"),
    ("MenuTheme", "Nord"), ("MenuTheme", "Olivia"), ("MenuTheme", "Dracula"),
    ("MenuTheme", "Bliss"), ("MenuTheme", "Catppuccin Latte"),
    ("MenuTheme", "Catppuccin Frappe"), ("MenuTheme", "Catppuccin Macchiato"),
    ("MenuTheme", "Catppuccin Mocha"),
    ("AnalogTab", "{} (0-255)"),
    ("TabbedKeycodes", "ISO/JIS"), ("TabbedKeycodes", "MIDI"),
}


def resolve(ctx, src):
    hit = ZH.get((ctx, src))
    if hit is None:
        hit = ZH.get(src)
    return hit


def lit(n):
    return n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None


static = set()
for f in glob.glob(SRC + r"\**\*.py", recursive=True):
    if "\\test\\" in f or "i18n" in os.path.basename(f):
        continue
    for node in ast.walk(ast.parse(open(f, encoding="utf-8").read())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr" \
                and len(node.args) >= 2:
            c, s = lit(node.args[0]), lit(node.args[1])
            if c is not None and s is not None:
                static.add((c, s))

miss = [(c, s) for c, s in sorted(static) if resolve(c, s) is None
        and (c, s) not in EXPECTED_UNTRANSLATED]
print("\n静态 tr() key %d 个，意外未命中 %d" % (len(static), len(miss)))
for c, s in miss:
    print("   [MISS %s] %r" % (c, s[:75]))

miss_dyn = [(c, s) for c, vals in DYNAMIC.items() for s in vals
            if resolve(c, s) is None and (c, s) not in EXPECTED_UNTRANSLATED]
print("动态取值意外未命中 %d" % len(miss_dyn))
for c, s in miss_dyn:
    print("   [DYN-MISS %s] %r" % (c, s))

used = set()
for c, s in static | {(c, v) for c, vs in DYNAMIC.items() for v in vs}:
    used.add((c, s))
    used.add(s)
unused = [k for k in ZH if k not in used]
print("表里未被引用的条目 %d" % len(unused))
for k in unused:
    print("   [UNUSED] %r -> %r" % (str(k)[:60], ZH[k][:28]))

ok = (not dropped) and (not miss) and (not miss_dyn)
print("\n== 结论: %s ==" % ("PASS 全部命中" if ok else "FAIL 有漏项"))
sys.exit(0 if ok else 1)
