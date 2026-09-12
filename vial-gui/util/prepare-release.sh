#!/bin/bash

set -e

VER=$1
DST=$2

pushd $DST

unzip vial-linux.zip
rm vial-linux.zip
mv Vial-STG-x86_64.AppImage Vial-STG-v$VER-x86_64.AppImage

unzip vial-mac.zip
rm vial-mac.zip
mv vial-mac.dmg Vial-STG-v$VER.dmg

unzip vial-win-installer.zip
rm vial-win-installer.zip
mv Vial-STGSetup.exe Vial-STG-v$VER-setup.exe

mv vial-win.zip vial-win2.zip
unzip vial-win2.zip
rm vial-win2.zip
mv vial-win.zip Vial-STG-v$VER-portable.zip

popd

echo "All OK"
