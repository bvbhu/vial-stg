# SPDX-License-Identifier: GPL-2.0-or-later

KEY_SIZE_RATIO = 3.2
KEY_SPACING_RATIO = 0.2
KEY_ROUNDNESS = 0.08

KEYCODE_BTN_RATIO = 3

WINDOW_WIDTH, WINDOW_HEIGHT = 1024, 768

KEYBOARD_WIDGET_PADDING = 5

KEYBOARD_WIDGET_MASK_HEIGHT = 0.65
KEYBOARD_WIDGET_NONMASK_PADDING = 0.02

SHADOW_SIDE_PADDING = 0.1
SHADOW_TOP_PADDING = 0.05
SHADOW_BOTTOM_PADDING = 0.15

# ---- 应用身份 ----
# 标题栏/exe/安装包名由 fbs 的 src/build/settings/base.json 里的 app_name 决定；
# 这里再抄一份给运行时用（QSettings 路径、关于对话框文案），两处必须保持一致。
APP_NAME = "Vial-STG"
ORG_NAME = "Shi Ta Ge Studio"
ORG_NAME_ZH = "石塔哥工作室"
REPO_URL = "https://github.com/bvbhu/vial-stg"

# QSettings 的组织名与应用名：决定窗口尺寸/语言等设置写到哪里。
# 从上游 Vial 换过来，避免与本仓库的旧设置串味（也意味着首次升级会重置这些设置）。
SETTINGS_ORG = ORG_NAME
SETTINGS_APP = APP_NAME
