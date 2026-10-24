# plugin/ — JUCE pass-through analyzer

Not yet implemented. Spec is in `docs/ARCHITECTURE.md` ("Plugin → companion contract").

Until it exists, the same data path works with an exported bounce:
`POST /advise/audio` (UI: Mix notes → "Advise this bounce") or `POST /ingest/audio` with a label.

Suggested skeleton (Projucer / CMake, VST3 + AU):
```
plugin/
  Source/PluginProcessor.{h,cpp}   processBlock copies into a lock-free ring buffer, touches nothing else
  Source/PluginEditor.{h,cpp}      one text box: the bus label ("Master", "Kick"...)
  Source/Analyzer.{h,cpp}          worker thread: FFT, RMS/peak, crest, stereo correlation
  Source/Uplink.{h,cpp}            juce::URL POST to http://127.0.0.1:8000/ingest/metrics every ~2 s
```
