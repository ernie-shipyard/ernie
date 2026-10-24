#include "WaveformDisplay.h"

WaveformDisplay::WaveformDisplay()
{
    left.resize(1024, 0.0f);
    right.resize(1024, 0.0f);
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
        std::fill(left.begin(), left.end(), 0.0f);
        std::fill(right.begin(), right.end(), 0.0f);
        repaint();
        return;
    }

    const auto frames = static_cast<int>(left.size());
    float* channels[] = { left.data(), right.data() };
    const auto copied = sampleSource(channels, 2, frames);

    auto slide = [copied, frames](std::vector<float>& samples)
    {
        if (copied < frames)
        {
            std::move_backward(samples.begin(), samples.begin() + copied, samples.end());
            std::fill(samples.begin(), samples.end() - copied, 0.0f);
        }
    };

    slide(left);
    slide(right);
    repaint();
}

void WaveformDisplay::paint(juce::Graphics& g)
{
    auto bounds = getLocalBounds().toFloat();
    g.setColour(juce::Colour(0xff10140f));
    g.fillRoundedRectangle(bounds, 6.0f);

    g.setColour(juce::Colour(0xff2c3a28));
    g.drawRoundedRectangle(bounds.reduced(0.5f), 6.0f, 1.0f);

    auto drawTrace = [&g, bounds](const std::vector<float>& samples, juce::Rectangle<float> lane, juce::Colour colour)
    {
        const auto midY = lane.getCentreY();
        g.setColour(juce::Colour(0xff3d4a34));
        g.drawHorizontalLine(juce::roundToInt(midY), lane.getX() + 8.0f, lane.getRight() - 8.0f);

        const auto count = static_cast<int>(samples.size());
        if (count < 2)
            return;

        juce::Path path;
        const auto height = lane.getHeight() * 0.42f;
        auto xFor = [count, lane](int index)
        {
            return lane.getX() + (static_cast<float>(index) / static_cast<float>(count - 1)) * lane.getWidth();
        };

        path.startNewSubPath(xFor(0), midY - samples.front() * height);
        for (int i = 1; i < count; ++i)
            path.lineTo(xFor(i), midY - samples[static_cast<size_t>(i)] * height);

        g.setColour(colour);
        g.strokePath(path, juce::PathStrokeType(1.6f));
        juce::ignoreUnused(bounds);
    };

    if (!active)
    {
        g.setColour(juce::Colours::white.withAlpha(0.45f));
        g.setFont(13.0f);
        g.drawFittedText("Recorder idle", getLocalBounds(), juce::Justification::centred, 1);
        return;
    }

    auto leftLane = bounds.removeFromTop(bounds.getHeight() * 0.5f).reduced(0.0f, 4.0f);
    auto rightLane = bounds.reduced(0.0f, 4.0f);
    drawTrace(left, leftLane, juce::Colour(0xffd6ff62));
    drawTrace(right, rightLane, juce::Colour(0xff7ee0ff));
}
