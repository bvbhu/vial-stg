#!/bin/bash

set -e

export LD_LIBRARY_PATH=/vial-gui/util/python36/prefix/lib/

cd /vial-gui
./util/python36/prefix/bin/python3 -m venv docker_venv
. docker_venv/bin/activate
pip install -r requirements.txt
fbs freeze
fbs installer
deactivate
/pkg2appimage-*/pkg2appimage misc/Vial-STG.yml
mv out/Vial-STG-*.AppImage /output/Vial-STG-x86_64.AppImage
