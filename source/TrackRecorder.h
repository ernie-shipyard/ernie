#pragma once

#include "SessionMetadata.h"

#include <juce_audio_basics/juce_audio_basics.h>
#include <juce_dsp/juce_dsp.h>

#include <array>
#include <atomic>
#include <vector>

// Copies incoming audio into a fixed RAM ring. The caller's buffer is never
// written. Sample-rate conversion, when needed, happens only on the copy.
//
// Settings cannot change while recording is armed. The ring is allocated on
// the message thread before the audio thread is allowed to write.
class TrackRecorder
{
public:
    static constexpr int maxChannels = 2;
    static constexpr double minDurationSeconds = 0.25;
    static constexpr double maxDurationSeconds = 120.0;
    static constexpr juce::int64 maxRamBytes = 256ll * 1024 * 1024;

    TrackRecorder() = default;

    RecorderSettings getSettings() const;
    juce::Result setSettings(const RecorderSettings& next, double hostSampleRate);

    bool isRecording() const noexcept { return recording.load(std::memory_order_acquire); }
    juce::int64 getSamplesWritten() const noexcept { return samplesWritten.load(std::memory_order_relaxed); }
    int getCapacity() const noexcept { return capacity.load(std::memory_order_acquire); }
    double getActiveCaptureRate() const noexcept { return activeCaptureRate.load(std::memory_order_acquire); }
    int getActiveChannels() const noexcept { return activeChannels.load(std::memory_order_acquire); }
    bool hasWrapped() const noexcept { return wrapped.load(std::memory_order_relaxed); }
    juce::String getSessionId() const;

    // Message thread. Allocates the ring, then arms the audio thread.
    juce::Result start(double hostSampleRate, int numChannels, const juce::String& sessionId);
    void stop() noexcept;

    // Audio thread. Reads `input` and does not modify it.
    void capture(const juce::AudioBuffer<float>& input) noexcept;

    // Copies the most recent samples (oldest first) into `dest`.
    // Returns the number of frames actually copied.
    int copyRecent(float* const* dest, int destChannels, int numFrames) const;

    juce::int64 ramBytes() const;

    static double clampDuration(double seconds);
    static double clampRate(double sampleRate);
    static bool isSupportedRate(double sampleRate);

private:
    int outputFramesFor(int numInput) const noexcept;
    void resampleChannel(const float* input, int numInput, float* output, int numOutput, int channel) noexcept;

    RecorderSettings settings;
    mutable juce::CriticalSection settingsLock;

    std::atomic<bool> recording { false };
    std::atomic<int> capacity { 0 };
    std::atomic<int> activeChannels { 0 };
    std::atomic<double> activeCaptureRate { 0.0 };
    std::atomic<double> resampleRatio { 1.0 }; // host samples advanced per output sample
    std::atomic<juce::int64> samplesWritten { 0 };
    std::atomic<juce::int64> writeIndex { 0 };
    std::atomic<bool> wrapped { false };

    juce::String sessionId;
    std::vector<float> ring; // planar, channel-major, capacity frames each

    std::array<juce::LagrangeInterpolator, maxChannels> interpolators;
    std::vector<float> resampleScratch;
};
