#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
source="$root/build/Ernie_artefacts/Debug/VST3/Ernie.vst3"
dest="$HOME/Library/Audio/Plug-Ins/VST3/Ernie.vst3"

if [ ! -d "$source" ]; then
    echo "missing debug VST3: $source" >&2
    echo "build it with: cmake --build build --target Ernie_VST3 --config Debug" >&2
    exit 1
fi

mkdir -p "$HOME/Library/Audio/Plug-Ins/VST3"
rm -rf "$dest"
mv "$source" "$dest"
echo "moved $source"
echo "to $dest"
