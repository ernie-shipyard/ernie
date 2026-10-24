# ernie

Stereo audio effect plugin template (AU, VST3, and Standalone) built with JUCE 9.0.4 and CMake.

JUCE is fetched automatically on the first configure. To use a local checkout instead:

```sh
cmake -S . -B build -DJUCE_SOURCE_DIR=/path/to/JUCE
```

## Build

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build --config Debug
```

On macOS the AU and VST3 bundles land under `build/Ernie_artefacts/`. The standalone app is the fastest way to listen while developing.

`COPY_PLUGIN_AFTER_BUILD` is off, so builds do not install into system plugin folders. Copy the bundles yourself, or flip that flag in `CMakeLists.txt` once you want hosts to pick them up.

## Layout

- `CMakeLists.txt` — plugin target, formats, and JUCE fetch
- `source/PluginProcessor.*` — processor, gain parameter, state
- `source/PluginEditor.*` — editor with a gain slider
