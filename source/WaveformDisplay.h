#pragma once

#include <juce_gui_basics/juce_gui_basics.h>

#include <functional>

// Scrolling oscilloscope of the RAM capture. Reads a caller-supplied copy of
// the latest frames; it never touches the audio callback's buffer.
class WaveformDisplay : public juce::Component,
                        private juce::Timer
{
public:
    using SampleSource = std::function<int(float* dest, int numFrames)>;

    WaveformDisplay();
    ~WaveformDisplay() override;

    void setSampleSource(SampleSource source);
    void setActive(bool shouldDraw);

    void paint(juce::Graphics& g) override;

private:
    void timerCallback() override;

    SampleSource sampleSource;
    std::vector<float> samples;
    bool active = false;
};
