#include "TrackRecorder.h"

#include <juce_audio_basics/juce_audio_basics.h>
#include <cstring>
#include <iostream>

namespace
{
int failures = 0;

void expect(bool condition, const char* message)
{
    if (!condition)
    {
        std::cerr << "FAIL: " << message << '\n';
        ++failures;
    }
}
}

int main()
{
    TrackRecorder recorder;
    RecorderSettings settings;
    settings.captureSampleRate = 48000.0;
    settings.bufferDurationSeconds = 1.0;
    settings.matchHostSampleRate = false;
    expect(recorder.setSettings(settings, 44100.0).wasOk(), "settings should apply while idle");

    juce::AudioBuffer<float> input(2, 512);
    for (int i = 0; i < input.getNumSamples(); ++i)
    {
        const auto sample = std::sin(2.0 * juce::MathConstants<double>::pi * 440.0 * i / 44100.0);
        input.setSample(0, i, static_cast<float>(sample));
        input.setSample(1, i, static_cast<float>(-sample));
    }

    const auto inputCopy = input;
    expect(recorder.start(44100.0, 1, "test-session").wasOk(), "start should arm a stereo ring even from a mono bus");
    expect(recorder.getActiveChannels() == 2, "capture is always stereo");
    expect(recorder.setSettings(settings, 44100.0).failed(), "settings must be frozen while recording");

    for (int block = 0; block < 100; ++block)
        recorder.capture(input);

    expect(std::memcmp(input.getReadPointer(0), inputCopy.getReadPointer(0), sizeof(float) * static_cast<size_t>(input.getNumSamples())) == 0,
           "capture must not modify channel 0");
    expect(std::memcmp(input.getReadPointer(1), inputCopy.getReadPointer(1), sizeof(float) * static_cast<size_t>(input.getNumSamples())) == 0,
           "capture must not modify channel 1");
    expect(recorder.getSamplesWritten() > 0, "resampled frames should land in RAM");
    expect(recorder.ramBytes() == 48000 * 2 * static_cast<juce::int64>(sizeof(float)), "ring should be 1s stereo at 48 kHz");

    std::vector<float> recent(256, -1.0f);
    float* channels[] = { recent.data() };
    const auto copied = recorder.copyRecent(channels, 1, 256);
    expect(copied == 256, "scope should read a full recent window");

    auto energy = 0.0;
    for (auto sample : recent)
        energy += std::abs(sample);
    expect(energy > 1.0, "captured audio should not be silence");

    recorder.stop();
    const auto written = recorder.getSamplesWritten();
    recorder.capture(input);
    expect(recorder.getSamplesWritten() == written, "capture must ignore audio after stop");

    std::cout << (failures == 0 ? "recorder tests passed\n" : "recorder tests failed\n");
    return failures == 0 ? 0 : 1;
}
