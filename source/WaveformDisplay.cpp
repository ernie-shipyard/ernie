#include "WaveformDisplay.h"

WaveformDisplay::WaveformDisplay()
{
    samples.resize(1024, 0.0f);
    startTimerHz(30);
}

WaveformDisplay::~WaveformDisplay()
{
    stopTimer();
}

void WaveformDisplay::setSampleSource(SampleSource source)
{
    sampleSource = std::move(source);
}

void WaveformDisplay::setActive(bool shouldDraw)
{
    active = shouldDraw;
    repaint();
}

void WaveformDisplay::timerCallback()
{
    if (!active || sampleSource == nullptr)
    {
        std::fill(samples.begin(), samples.end(), 0.0f);
        repaint();
        return;
    }

    const auto frames = static_cast<int>(samples.size());
    const auto copied = sampleSource(samples.data(), frames);

    if (copied < frames)
    {
        // copyRecent() is left-aligned. Slide it to the right so the trace
        // grows from the right edge, like a scope waiting for a signal.
        std::move_backward(samples.begin(), samples.begin() + copied, samples.end());
        std::fill(samples.begin(), samples.end() - copied, 0.0f);
    }

    repaint();
}

void WaveformDisplay::paint(juce::Graphics& g)
{
    auto bounds = getLocalBounds().toFloat();
    g.setColour(juce::Colour(0xff10140f));
    g.fillRoundedRectangle(bounds, 6.0f);

    g.setColour(juce::Colour(0xff2c3a28));
    g.drawRoundedRectangle(bounds.reduced(0.5f), 6.0f, 1.0f);

    const auto midY = bounds.getCentreY();
    g.setColour(juce::Colour(0xff3d4a34));
    g.drawHorizontalLine(juce::roundToInt(midY), bounds.getX() + 8.0f, bounds.getRight() - 8.0f);

    if (!active)
    {
        g.setColour(juce::Colours::white.withAlpha(0.45f));
        g.setFont(13.0f);
        g.drawFittedText("Recorder idle", getLocalBounds(), juce::Justification::centred, 1);
        return;
    }

    juce::Path path;
    const auto width = bounds.getWidth();
    const auto height = bounds.getHeight() * 0.42f;
    const auto count = static_cast<int>(samples.size());

    if (count < 2)
        return;

    auto xFor = [count, bounds](int index)
    {
        return bounds.getX() + (static_cast<float>(index) / static_cast<float>(count - 1)) * bounds.getWidth();
    };

    juce::ignoreUnused(width);
    path.startNewSubPath(xFor(0), midY - samples.front() * height);

    for (int i = 1; i < count; ++i)
        path.lineTo(xFor(i), midY - samples[static_cast<size_t>(i)] * height);

    g.setColour(juce::Colour(0xffd6ff62));
    g.strokePath(path, juce::PathStrokeType(1.6f));
}
