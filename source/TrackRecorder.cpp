#include "TrackRecorder.h"

namespace
{
constexpr std::array<double, 6> supportedRates { 22050.0, 44100.0, 48000.0, 88200.0, 96000.0, 192000.0 };
}

RecorderSettings TrackRecorder::getSettings() const
{
    const juce::ScopedLock lock(settingsLock);
    return settings;
}

juce::Result TrackRecorder::setSettings(const RecorderSettings& next, double hostSampleRate)
{
    if (isRecording())
        return juce::Result::fail("Cannot change sample rate or buffer duration while recording");

    RecorderSettings clamped = next;
    clamped.bufferDurationSeconds = clampDuration(next.bufferDurationSeconds);

    if (clamped.matchHostSampleRate)
    {
        if (hostSampleRate <= 0.0)
            return juce::Result::fail("Host sample rate is not available yet");
        clamped.captureSampleRate = hostSampleRate;
    }
    else if (!isSupportedRate(clamped.captureSampleRate))
    {
        return juce::Result::fail("Unsupported capture sample rate");
    }

    const auto channels = juce::jmax(1, activeChannels.load(std::memory_order_relaxed));
    const auto frames = static_cast<juce::int64>(std::llround(clamped.captureSampleRate * clamped.bufferDurationSeconds));
    if (frames < 1)
        return juce::Result::fail("Buffer duration is too short");

    if (frames * channels * static_cast<juce::int64>(sizeof(float)) > maxRamBytes)
        return juce::Result::fail("Requested buffer exceeds the 256 MB RAM limit");

    const juce::ScopedLock lock(settingsLock);
    settings = clamped;
    return juce::Result::ok();
}

juce::Result TrackRecorder::start(double hostSampleRate, int numChannels, const juce::String& newSessionId)
{
    if (isRecording())
        return juce::Result::fail("Already recording");

    if (hostSampleRate <= 0.0)
        return juce::Result::fail("Host sample rate is not available yet");

    numChannels = juce::jlimit(1, maxChannels, numChannels);

    RecorderSettings active;
    {
        const juce::ScopedLock lock(settingsLock);
        active = settings;
        if (active.matchHostSampleRate)
            active.captureSampleRate = hostSampleRate;
    }

    const auto frames = static_cast<int>(std::llround(active.captureSampleRate * active.bufferDurationSeconds));
    if (frames < 1)
        return juce::Result::fail("Buffer duration is too short");

    const auto bytes = static_cast<juce::int64>(frames) * numChannels * static_cast<juce::int64>(sizeof(float));
    if (bytes > maxRamBytes)
        return juce::Result::fail("Requested buffer exceeds the 256 MB RAM limit");

    ring.assign(static_cast<size_t>(frames * numChannels), 0.0f);
    resampleScratch.assign(static_cast<size_t>(frames), 0.0f);

    for (auto& interpolator : interpolators)
        interpolator.reset();

    writeIndex.store(0, std::memory_order_relaxed);
    samplesWritten.store(0, std::memory_order_relaxed);
    wrapped.store(false, std::memory_order_relaxed);
    capacity.store(frames, std::memory_order_relaxed);
    activeChannels.store(numChannels, std::memory_order_relaxed);
    activeCaptureRate.store(active.captureSampleRate, std::memory_order_relaxed);
    resampleRatio.store(hostSampleRate / active.captureSampleRate, std::memory_order_relaxed);

    {
        const juce::ScopedLock lock(settingsLock);
        sessionId = newSessionId;
    }

    recording.store(true, std::memory_order_release);
    return juce::Result::ok();
}

void TrackRecorder::stop() noexcept
{
    recording.store(false, std::memory_order_release);
}

juce::String TrackRecorder::getSessionId() const
{
    const juce::ScopedLock lock(settingsLock);
    return sessionId;
}

void TrackRecorder::capture(const juce::AudioBuffer<float>& input) noexcept
{
    if (!recording.load(std::memory_order_acquire))
        return;

    const auto frames = capacity.load(std::memory_order_acquire);
    const auto channels = activeChannels.load(std::memory_order_acquire);
    const auto ratio = resampleRatio.load(std::memory_order_acquire);

    if (frames <= 0 || channels <= 0 || ring.empty() || ratio <= 0.0)
        return;

    const auto numInput = input.getNumSamples();
    if (numInput <= 0)
        return;

    const auto usableChannels = juce::jmin(channels, input.getNumChannels());
    const auto index = writeIndex.load(std::memory_order_relaxed);
    const auto unityRate = std::abs(ratio - 1.0) < 1.0e-6;
    const auto produced = unityRate ? juce::jmin(numInput, frames) : outputFramesFor(numInput);

    if (produced <= 0)
        return;

    for (int channel = 0; channel < usableChannels; ++channel)
    {
        const auto* source = input.getReadPointer(channel);

        if (!unityRate)
        {
            resampleChannel(source, numInput, resampleScratch.data(), produced, channel);
            source = resampleScratch.data();
        }

        auto* dest = ring.data() + static_cast<size_t>(channel * frames);

        for (int i = 0; i < produced; ++i)
            dest[(index + i) % frames] = source[i];
    }

    const auto next = index + produced;
    writeIndex.store(next, std::memory_order_release);
    samplesWritten.fetch_add(produced, std::memory_order_relaxed);

    if (next >= frames)
        wrapped.store(true, std::memory_order_relaxed);
}

int TrackRecorder::outputFramesFor(int numInput) const noexcept
{
    const auto ratio = resampleRatio.load(std::memory_order_relaxed);
    if (ratio <= 0.0 || numInput <= 0)
        return 0;

    // Leave a few input samples so the interpolator does not read past the block.
    const auto available = static_cast<double>(juce::jmax(0, numInput - 4));
    return juce::jlimit(0, static_cast<int>(resampleScratch.size()), static_cast<int>(available / ratio));
}

void TrackRecorder::resampleChannel(const float* input, int numInput, float* output, int numOutput, int channel) noexcept
{
    const auto ratio = resampleRatio.load(std::memory_order_relaxed);
    auto& interpolator = interpolators[static_cast<size_t>(channel)];
    interpolator.process(ratio, input, output, numOutput, numInput, 0);
}

int TrackRecorder::copyRecent(float* const* dest, int destChannels, int numFrames) const
{
    if (dest == nullptr || numFrames <= 0)
        return 0;

    const auto frames = capacity.load(std::memory_order_acquire);
    const auto channels = activeChannels.load(std::memory_order_acquire);
    const auto written = samplesWritten.load(std::memory_order_acquire);
    const auto index = writeIndex.load(std::memory_order_acquire);

    if (frames <= 0 || channels <= 0 || written <= 0 || ring.size() < static_cast<size_t>(frames * channels))
        return 0;

    const auto available = static_cast<int>(juce::jmin<juce::int64>(written, frames));
    const auto toCopy = juce::jmin(numFrames, available);
    auto start = static_cast<int>((index - toCopy) % frames);
    if (start < 0)
        start += frames;
    const auto usable = juce::jmin(destChannels, channels);

    for (int channel = 0; channel < usable; ++channel)
    {
        const auto* source = ring.data() + static_cast<size_t>(channel * frames);
        auto* out = dest[channel];
        if (out == nullptr)
            continue;

        const auto first = juce::jmin(toCopy, frames - start);
        juce::FloatVectorOperations::copy(out, source + start, first);
        if (first < toCopy)
            juce::FloatVectorOperations::copy(out + first, source, toCopy - first);
    }

    for (int channel = usable; channel < destChannels; ++channel)
        if (dest[channel] != nullptr)
            juce::FloatVectorOperations::clear(dest[channel], toCopy);

    return toCopy;
}

juce::int64 TrackRecorder::ramBytes() const
{
    const auto frames = capacity.load(std::memory_order_acquire);
    const auto channels = activeChannels.load(std::memory_order_acquire);
    return static_cast<juce::int64>(frames) * channels * static_cast<juce::int64>(sizeof(float));
}

double TrackRecorder::clampDuration(double seconds)
{
    return juce::jlimit(minDurationSeconds, maxDurationSeconds, seconds);
}

double TrackRecorder::clampRate(double sampleRate)
{
    auto best = supportedRates.front();
    auto bestDistance = std::abs(sampleRate - best);

    for (auto rate : supportedRates)
    {
        const auto distance = std::abs(sampleRate - rate);
        if (distance < bestDistance)
        {
            best = rate;
            bestDistance = distance;
        }
    }

    return best;
}

bool TrackRecorder::isSupportedRate(double sampleRate)
{
    for (auto rate : supportedRates)
        if (std::abs(sampleRate - rate) < 0.5)
            return true;

    return false;
}
