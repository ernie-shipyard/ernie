#pragma once

#include <juce_audio_processors/juce_audio_processors.h>

// Snapshot of host context captured on the audio thread and consumed by the
// metadata writer. Optional fields stay disengaged when the host does not
// supply them — callers must not invent defaults.
struct SessionSnapshot
{
    juce::int64 capturedAtMs = 0;

    bool recording = false;
    juce::String sessionId;
    juce::int64 recordingStartedAtMs = 0;

    double hostSampleRate = 0.0;
    int hostBlockSize = 0;
    juce::String hostDescription;
    juce::String pluginFormat;

    bool playHeadAvailable = false;
    bool transportPlaying = false;
    bool transportRecording = false;
    bool transportLooping = false;
    bool playHeadMoving = false;

    bool hasTimeInSamples = false;
    juce::int64 timeInSamples = 0;

    bool hasTimeInSeconds = false;
    double timeInSeconds = 0.0;

    bool hasBpm = false;
    double bpm = 0.0;
    juce::String bpmSource; // "daw", "file", or empty

    bool hasKey = false;
    juce::String key;
    juce::String keySource; // "daw", "file", or empty
    int keyNote = -1;

    bool hasTimeSignature = false;
    int timeSigNumerator = 0;
    int timeSigDenominator = 0;

    bool hasBarCount = false;
    juce::int64 barCount = 0;

    bool hasPpq = false;
    double ppqPosition = 0.0;

    bool hasLastBarPpq = false;
    double ppqOfLastBarStart = 0.0;

    bool hasLoopPoints = false;
    double loopStartPpq = 0.0;
    double loopEndPpq = 0.0;

    bool hasFrameRate = false;
    double frameRate = 0.0;

    bool hasEditOrigin = false;
    double editOriginSeconds = 0.0;

    // Derived only when PPQ and time signature are both present.
    bool hasMeasureBeat = false;
    int measure = 0;          // 1-based
    double beatInMeasure = 0.0; // 1-based

    // Filled by the host via updateTrackProperties. Not all hosts send this.
    bool hasTrackName = false;
    juce::String trackName;
    bool hasTrackColour = false;
    juce::uint32 trackColourArgb = 0;

    // Resampled RAM capture, not the host stream.
    double captureSampleRate = 0.0;
    double bufferDurationSeconds = 0.0;
    int numChannels = 0;
    juce::int64 samplesWritten = 0;
    juce::int64 bufferCapacitySamples = 0;
    bool bufferWrapped = false;
    juce::int64 ramBytes = 0;

    // Host plugin APIs do not expose the rest of the insert chain.
    juce::String siblingPluginsNote {
        "unavailable: VST3 and AU do not let a plugin enumerate other plugins on its track"
    };

    juce::String keyNoteText;
    juce::String bpmNote;
    juce::String playheadNote;
};

struct RecorderSettings
{
    double captureSampleRate = 48000.0;
    double bufferDurationSeconds = 8.0;
    bool matchHostSampleRate = false;
};

class SessionSnapshotStore
{
public:
    void publish(const SessionSnapshot& next)
    {
        const juce::ScopedLock lock(mutex);
        current = next;
    }

    SessionSnapshot read() const
    {
        const juce::ScopedLock lock(mutex);
        return current;
    }

private:
    mutable juce::CriticalSection mutex;
    SessionSnapshot current;
};
