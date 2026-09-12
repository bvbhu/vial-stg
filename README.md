# vial-stg

[Vial](https://get.vial.today) 的非官方衍生仓库。把上游 `vial-kb/vial-web`与 `vial-kb/vial-gui`（vial桌面版和web外壳）合并进同一个仓库，以 vial-web 的目录布局作容器，桌面源码放在 `vial-gui/` 子目录。

## 身份与命名

| 项 | 值 |
|---|---|
| 应用名（标题栏 / exe / 安装包） | `Vial-STG` |
| 组织 | 石塔哥工作室（Shi Ta Ge Studio） |
| 仓库 | https://github.com/bvbhu/vial-stg |

应用名的唯一来源是 `vial-gui/src/build/settings/base.json` 的 `app_name`。
fbs 用它派生 freeze 目录、NSIS 安装包名、.desktop 条目、macOS bundle 名等，
CI 里出现的 `target/Vial-STG`、`Vial-STGSetup.exe`、`Vial-STG-v<版本>-*` 全部由它推出，
改 `app_name` 时要同步改：

- `vial-gui/misc/Vial-STG.yml`（AppImage 配方，文件名里的名字也一起换）
- `.github/workflows/release.yml`、`vial-gui/.github/workflows/main.yml` 的产物路径与 artifact 名
- `vial-gui/src/main/python/constants.py` 的 `APP_NAME`（QSettings 路径与关于对话框文案）
- `vial-gui/src/main/python/i18n_zh.py` 里以 `About Vial-STG...` 为键的译文

## 上游与许可

| 来源                                                                      | 许可证                                                                  |
| ------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `vial-gui/**`                                                           | GPL-2.0-or-later（以 per-file SPDX 头为准，全文见`vial-gui/COPYING`） |
| `src/`、`patches/`、`fetch-*.sh`、`build-deps.sh`、`version.sh` | 来自`vial-kb/vial-web`，**上游未声明许可证**                    |
| `src/simpleeval.py`                                                     | MIT，第三方 vendored（`Copyright (C) 2013-2019 Daniel Fairhead`）     |

本仓库按 GPL-2.0-or-later 发布（PyQt5 是 GPL-3.0，整条链路只能是 GPL 系），
全文见 `LICENSE`。

## 构建

### 环境要求

| 目标 | 需要的东西 |
|---|---|
| **Web 构建** | Linux 或 macOS（Windows 需 WSL2）+ bash / make / patch / wget。工具链 `emsdk 3.1.10`（commit `891b449`）由 `fetch-emsdk.sh` 自动拉取，依赖包由 `fetch-deps.sh` 拉取并做 sha256 校验，都不用手动装。磁盘留 8 GB 以上；首次交叉编译 CPython + Qt5 耗时数小时 |
| **桌面打包** | **Python 3.6** —— fbs / PyInstaller 3.4 官方只支持 3.6，换新版 Python 会直接装不上依赖。Windows 另需 NSIS 3.06.1（`fbs installer` 用）与 VC++ 运行时；Linux 打 AppImage 需 Docker |
| **CI** | GitHub Actions：`ubuntu-22.04`（Web / Linux 包 / 测试）、`windows-2025`、`macos-15` |

### Web

```
git clone https://github.com/bvbhu/vial-stg && cd vial-stg
git clone https://github.com/vial-kb/via-keymap-precompiled.git
./fetch-emsdk.sh
./fetch-deps.sh
./build-deps.sh
cd src && ./build.sh
```

### 桌面版

```
cd vial-gui
python -m venv venv
. venv/bin/activate                 # Windows: . .\venv\Scripts\activate.ps1
pip install -r requirements.txt
fbs run          # 开发用
fbs freeze       # 打成 target/Vial-STG（Windows/Linux 目录，macOS 是 target/Vial-STG.app）
fbs installer    # Windows 需先装 NSIS，产出 target/Vial-STGSetup.exe
```
