"""Surface the VST3 process-context chord on AudioPlayHead::PositionInfo.

JUCE requests kNeedChord but drops processContext.chord. Reconfigure runs this
against the fetched tree. It is a no-op when the markers are already present.
"""

import pathlib
import sys

root = pathlib.Path(sys.argv[1])

head = root / "modules/juce_audio_basics/audio_play_head/juce_AudioPlayHead.h"
vst3 = root / "modules/juce_audio_plugin_client/juce_audio_plugin_client_VST3.cpp"

head_text = head.read_text()
if "getKeyNote" not in head_text:
    needle = "        /** The video frame rate, if available. */\n"
    insert = (
        "        /** VST3 process-context chord key, 0-11, when the host marks kChordValid.\n"
        "            JUCE does not surface this; Ernie's wrapper writes it here.\n"
        "        */\n"
        "        Optional<int> getKeyNote() const                                { return getOptional (flagKeyNote, keyNote); }\n"
        "\n"
        "        void setKeyNote (Optional<int> keyNoteIn)                        {        setOptional (flagKeyNote, keyNote, keyNoteIn); }\n"
        "\n"
    )
    if needle not in head_text:
        sys.exit(f"playhead anchor missing: {head}")
    head_text = head_text.replace(needle, insert + needle, 1)
    head_text = head_text.replace(
        "            flagIsLooping       = 1 << 13\n",
        "            flagIsLooping       = 1 << 13,\n            flagKeyNote         = 1 << 14\n",
        1,
    )
    head_text = head_text.replace(
        "        LoopPoints loopPoints;\n",
        "        LoopPoints loopPoints;\n        int keyNote            = -1;\n",
        1,
    )
    head.write_text(head_text)

vst3_text = vst3.read_text()
if "info.setKeyNote" not in vst3_text:
    needle = "                            : nullopt);\n\n        return info;\n"
    insert = (
        "                            : nullopt);\n"
        "\n"
        "        info.setKeyNote ((processContext.state & Vst::ProcessContext::kChordValid) != 0\n"
        "                         ? makeOptional ((int) processContext.chord.keyNote)\n"
        "                         : nullopt);\n"
        "\n"
        "        return info;\n"
    )
    count = vst3_text.count(needle)
    if count != 1:
        sys.exit(f"expected one VST3 getPosition anchor, found {count}")
    vst3.write_text(vst3_text.replace(needle, insert, 1))

print("host key markers present")
