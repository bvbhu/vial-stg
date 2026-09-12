#!/bin/bash

set -e

source ../version.sh
source ../emsdk/emsdk_env.sh

mkdir -p build
rm -rf build/*

pushd build

VIAL_VER=$(cd ../../vial-gui/ && git rev-parse HEAD)
WEB_VER=$(git rev-parse HEAD)
VIA_STACK_VER=$(cd ../../via-keymap-precompiled/ && git rev-parse HEAD)
UNIQVER=$(echo ${VIAL_VER} ${WEB_VER} ${VIA_STACK_VER} | sha256sum | awk '{print $1}')

cp ../icon.png .
cp -r ../../deps/cpython/builddir/emscripten-browser/usr .
cp ../../via-keymap-precompiled/via_keyboard_stack.json usr/local/via_keyboards.json
cp ../../vial-gui/src/main/resources/base/qmk_settings.json usr/local
cp ../../vial-gui/src/build/settings/base.json usr/local/build_settings.json
cp -r ../../vial-gui/src/main/python/* usr/local/lib/python3.11
cp ../simpleeval.py usr/local/lib/python3.11
# 界面字体：Qt WASM 不带系统字体，i18n 的中文会渲染成方框。复刻桌面端结构
# （Segoe UI 主 + 雅黑 CJK 回退）：Inter（SIL OFL 1.1，Latin，~1.21em≈Segoe UI）
# 作主字体决定行高，Noto Sans SC（思源黑体 Google 发行名，SIL OFL 1.1）作
# CJK 逐字回退、同字号不缩放；webmain.py 启动时 setFamilies 注册。9pt 不补偿，
# 页面与字大小均贴近桌面端。两字体均未修改原版，许可证与出处见 src/fonts/OFL.txt。
mkdir -p usr/local/fonts
cp ../fonts/Inter-Regular.ttf usr/local/fonts/
cp ../fonts/NotoSansSC-Regular.otf usr/local/fonts/
emcc \
    --preload-file="./usr/local" \
    -I ../../deps/cpython/Include/ \
    -I ../../deps/cpython/builddir/emscripten-browser/ \
    -I ../../deps/cpython/Include/internal/ \
    ../../deps/cpython/builddir/emscripten-browser/libpython3.11.a \
    ../../deps/cpython/builddir/emscripten-browser/Modules/_decimal/libmpdec/libmpdec.a \
    ../../deps/cpython/builddir/emscripten-browser/Modules/expat/libexpat.a  \
    -sALLOW_MEMORY_GROWTH \
    -sTOTAL_MEMORY=20971520 \
    -sFORCE_FILESYSTEM \
    -sTEXTDECODER=0 \
    -lidbfs.js \
    -lnodefs.js \
    -lproxyfs.js \
    -lworkerfs.js \
    -O2 -g0 \
    -o main-${UNIQVER}.js \
    -ldl \
    -lm \
    -sUSE_ZLIB \
    -sUSE_BZIP2 \
    -sUSE_ZLIB \
    -L ../../deps/PyQt5-${PYQT5_VER}/QtCore \
    -L ../../deps/qt5/qtbase/lib \
    -L ../../deps/PyQt5_sip-${PYQT5SIP_VER} \
    -L ../../deps/PyQt5-${PYQT5_VER}/QtGui \
    -L ../../deps/PyQt5-${PYQT5_VER}/QtWidgets \
    -L ../../deps/qt5/qtbase/plugins/platforms \
    -L ../../deps/PyQt5-${PYQT5_VER}/QtSvg \
    -L ../../deps/qt5/qtbase/plugins/iconengines \
    -L ../../deps/qt5/qtbase/plugins/imageformats \
    -l QtCore \
    -l Qt5Core \
    -l sip \
    -l QtGui \
    -l QtWidgets \
    -l Qt5Core \
    -l Qt5Gui \
    -l Qt5Widgets \
    -l qwasm \
    -l Qt5FontDatabaseSupport \
    -l qminimal \
    -l Qt5EventDispatcherSupport \
    -l qoffscreen \
    --bind \
    -l qtharfbuzz \
    -l QtSvg \
    -l qsvgicon \
    -l qjpeg \
    -l qsvg \
    -l Qt5Svg \
    -s USE_FREETYPE=1 \
    -s USE_LIBPNG=1 \
    -lqtpcre2 \
    -pthread \
    -sDISABLE_DEPRECATED_FIND_EVENT_TARGET_BEHAVIOR=0 \
    -s EXTRA_EXPORTED_RUNTIME_METHODS=\["UTF16ToString","stringToUTF16","getValue","setValue"\] \
    -sEXPORTED_FUNCTIONS=\["_PyRun_SimpleString","_vialglue_set_response","_vialglue_set_response_error","_vialglue_set_device_desc","_main"\] \
    -sPROXY_TO_PTHREAD \
    -sOFFSCREENCANVAS_SUPPORT \
    -L ../../deps/xz-${XZ_VER}/prefix/lib/ \
    -llzma \
    ../main.c
cp ../index.html .
# coi-serviceworker 必须和 index.html 同级部署：index.html 用相对路径引用它，
# 并由它自身的 URL 决定 SW 的 scope。缺了这个文件，pthread 版在 GitHub Pages
# 上会直接 "SharedArrayBuffer is not defined"。
cp ../coi-serviceworker.min.js .
cat ../worker.js >> main-${UNIQVER}.worker.js
sed -i 's+err("worker sent an unknown command+my_onmessage(e);return;err("worker sent an unknown command/+g' main-${UNIQVER}.js

rm -rf usr

sed -i "s/@VIAL_VER@/${VIAL_VER}/g" index.html
sed -i "s/@WEB_VER@/${WEB_VER}/g" index.html
sed -i "s/@VIA_STACK_VER@/${VIA_STACK_VER}/g" index.html
sed -i "s/@UNIQVER@/${UNIQVER}/g" index.html

popd
