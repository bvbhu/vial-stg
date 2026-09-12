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
| `src/coi-serviceworker.min.js`                                          | MIT，第三方 vendored（Guido Zuidhof and contributors，v0.1.7）        |
| `src/fonts/NotoSansSC-Regular.otf`                                      | SIL OFL 1.1（Noto Sans SC 即思源黑体，未修改原版，全文见 `src/fonts/OFL.txt`） |
| `src/fonts/Inter-Regular.ttf`                                           | SIL OFL 1.1（Inter，未修改原版，全文见 `src/fonts/OFL.txt`）                  |

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

#### Web 版必须跑在跨域隔离的环境里

`src/build.sh` 用 `-pthread` + `-sPROXY_TO_PTHREAD` 链接，emscripten 的 pthread 建在
`SharedArrayBuffer` 上；而 SAB 只在**跨域隔离**（cross-origin isolated）的文档里存在，
需要服务器发出这两个响应头：

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

缺头的话页面一加载就 `Uncaught ReferenceError: SharedArrayBuffer is not defined`。

**GitHub Pages 不能发自定义响应头**（[community#13309](https://github.com/orgs/community/discussions/13309)
多年未实现），所以 `web-pages.yml` 部署到 Pages 时靠
[`src/coi-serviceworker.min.js`](src/coi-serviceworker.min.js) 在客户端注入这两个头。
配套地，`index.html` 不再无条件加载 `main-*.js`，而是等隔离就绪再加载
（SW 接管后会自动刷新一次）——否则首次访问仍会在 SW 生效前撞上同一个崩溃。

换到能发头的主机（Cloudflare Pages / Netlify 放个 `_headers` 文件）就可以把这套去掉。

#### Web 版的界面字体（Latin 主 + CJK 回退）

Qt WebAssembly 不带任何系统字体，中文界面（i18n）会整体渲染成方框。
`src/build.sh` 把**完整未修改**的 Inter（Latin 主字体）与 Noto Sans SC
（思源黑体的 Google 发行名，CJK 回退字体，SIL OFL 1.1，可免费商用与嵌入
捆绑）拷进 preload 文件系统的 `/usr/local/fonts/`，`webmain.py` 启动时经
`QFontDatabase.addApplicationFont` 注册，并用 `QFont.setFamilies` 设为
**Latin 主 + CJK 逐字回退**：Qt 按主字体行高定标（键帽 = 行高 × 3.2 等），
CJK 码位从回退字体同字号取字，不再整体缩放。注册失败只影响显示，不阻断启动。

之所以不复刻旧版（把 CJK 字体直接设为应用字体再缩字号补偿）：桌面端
（main.py）不设字体，用 Qt 默认（Windows: Segoe UI @9pt）+ 系统雅黑 CJK
回退；旧版 CJK 字体行高 ~1.45em，同字号下页面被放大 ~25%，补偿缩字号后
字只有 ~6pt，明显小于桌面端。Inter（~1.21em）与 Segoe UI（~1.19em）行高
接近，9pt 下页面≈桌面端、字号同为 9pt，无需补偿。
许可证与出处见 [`src/fonts/OFL.txt`](src/fonts/OFL.txt)。
（代价是 `.data` 包体 +8.3 MB；若将来在意首屏体积，可用 `pyftsubset`
按实际文案裁剪，但裁剪版属于 Modified Version，须按 OFL 第 3 条改用
非保留字体名。）

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
