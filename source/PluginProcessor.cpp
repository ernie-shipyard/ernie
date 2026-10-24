#include "PluginProcessor.h"
#include "PluginEditor.h"
#include <juce_audio_formats/juce_audio_formats.h>

namespace
{
juce::String wrapperName(juce::AudioProcessor::WrapperType type)
{
    const auto* name = juce::AudioProcessor::getWrapperTypeDescription(type);
    return name != nullptr ? juce::String(name) : juce::String("Unknown");
}

void fillPlayHead(SessionSnapshot& snapshot, const juce::AudioPlayHead::PositionInfo& position)
{
    snapshot.playHeadAvailable = true;
    snapshot.transportPlaying = position.getIsPlaying();
    snapshot.transportRecording = position.getIsRecording();
    snapshot.transportLooping = position.getIsLooping();

    if (const auto samples = position.getTimeInSamples())
    {
        snapshot.hasTimeInSamples = true;
        snapshot.timeInSamples = *samples;
    }

    if (const auto seconds = position.getTimeInSeconds())
    {
        snapshot.hasTimeInSeconds = true;
        snapshot.timeInSeconds = *seconds;
    }

    if (const auto bpm = position.getBpm())
    {
        snapshot.hasBpm = true;
        snapshot.bpm = *bpm;
    }

    if (const auto signature = position.getTimeSignature())
    {
        snapshot.hasTimeSignature = true;
        snapshot.timeSigNumerator = signature->numerator;
        snapshot.timeSigDenominator = signature->denominator;
    }

    if (const auto bars = position.getBarCount())
    {
        snapshot.hasBarCount = true;
        snapshot.barCount = *bars;
    }

    if (const auto ppq = position.getPpqPosition())
    {
        snapshot.hasPpq = true;
        snapshot.ppqPosition = *ppq;
    }

    if (const auto lastBar = position.getPpqPositionOfLastBarStart())
    {
        snapshot.hasLastBarPpq = true;
        snapshot.ppqOfLastBarStart = *lastBar;
    }

    if (const auto loop = position.getLoopPoints())
    {
        snapshot.hasLoopPoints = true;
        snapshot.loopStartPpq = loop->ppqStart;
        snapshot.loopEndPpq = loop->ppqEnd;
    }

    snapshot.keyNote = -1;
    if (const auto key = position.getKeyNote())
    {
        if (*key >= 0 && *key < 128)
            snapshot.keyNote = *key % 12;
    }

    if (const auto frameRate = position.getFrameRate())
    {
        snapshot.hasFrameRate = true;
        snapshot.frameRate = frameRate->getEffectiveRate();
    }

    if (const auto origin = position.getEditOriginTime())
    {
        snapshot.hasEditOrigin = true;
        snapshot.editOriginSeconds = *origin;
    }

    if (snapshot.hasPpq && snapshot.hasTimeSignature && snapshot.timeSigDenominator > 0 && snapshot.timeSigNumerator > 0)
    {
        const auto quartersPerBeat = 4.0 / static_cast<double>(snapshot.timeSigDenominator);
        const auto quartersPerBar = quartersPerBeat * snapshot.timeSigNumerator;
        if (quartersPerBar > 0.0)
        {
            const auto measureIndex = std::floor(snapshot.ppqPosition / quartersPerBar);
            snapshot.hasMeasureBeat = true;
            snapshot.measure = static_cast<int>(measureIndex) + 1;
            snapshot.beatInMeasure = (snapshot.ppqPosition - measureIndex * quartersPerBar) / quartersPerBeat + 1.0;
        }
    }
}

void clearPlayheadLocation(SessionSnapshot& snapshot)
{
    snapshot.hasTimeInSamples = false;
    snapshot.hasTimeInSeconds = false;
    snapshot.hasBarCount = false;
    snapshot.hasPpq = false;
    snapshot.hasLastBarPpq = false;
    snapshot.hasMeasureBeat = false;
    snapshot.hasLoopPoints = false;
    snapshot.hasFrameRate = false;
    snapshot.hasEditOrigin = false;
}

juce::String noteName(int note)
{
    static const char* names[] = { "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B" };
    if (note < 0)
        return {};
    return names[note % 12];
}

bool parseKeyNote(const juce::String& key, int& note)
{
    static const char* names[] = { "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B" };
    auto text = key.trim().toUpperCase().replace("-MIN", "m").replace(" MIN", "m").replace("MINOR", "m").replace("MAJOR", "");
    if (text.startsWith("DB")) text = text.replaceFirstOccurrenceOf("DB", "C#");
    if (text.startsWith("EB")) text = text.replaceFirstOccurrenceOf("EB", "D#");
    if (text.startsWith("GB")) text = text.replaceFirstOccurrenceOf("GB", "F#");
    if (text.startsWith("AB")) text = text.replaceFirstOccurrenceOf("AB", "G#");
    if (text.startsWith("BB")) text = text.replaceFirstOccurrenceOf("BB", "A#");

    for (int i = 0; i < 12; ++i)
    {
        if (text.startsWith(names[i]))
        {
            note = i;
            return true;
        }
    }

    return false;
}
}

ErnieAudioProcessor::ErnieAudioProcessor()
    : AudioProcessor(BusesProperties()
#if !JucePlugin_IsMidiEffect
#if !JucePlugin_IsSynth
                         .withInput("Input", juce::AudioChannelSet::stereo(), true)
#endif
                         .withOutput("Output", juce::AudioChannelSet::stereo(), true)
#endif
                         ),
      parameters(*this, nullptr, "PARAMETERS", createParameterLayout()),
      metadataWriter(snapshots),
      api([this](const RecordingCommand& command) { return handleApiCommand(command); },
          [this] { return makeStatus(); },
          [this] { return getLastCaptureFile(); })
{
    gainParameter = parameters.getRawParameterValue("gain");

    auto settings = recorder.getSettings();
    settings.captureSampleRate = 48000.0;
    settings.bufferDurationSeconds = 8.0;
    settings.matchHostSampleRate = false;
    recorder.setSettings(settings, 0.0);

    if (auto result = api.start(); result.failed())
    {
        const juce::ScopedLock lock(stateLock);
        lastError = result.getErrorMessage();
    }
}

ErnieAudioProcessor::~ErnieAudioProcessor()
{
    stopRecording();
    api.shutdown();
    cancelPendingUpdate();
}

bool ErnieAudioProcessor::isRecording() const noexcept
{
    return recorder.isRecording()
           && (!transportListenArmed.load(std::memory_order_acquire)
               || transportListenStarted.load(std::memory_order_acquire));
}

bool ErnieAudioProcessor::isWaitingForPlayback() const noexcept
{
    return transportListenArmed.load(std::memory_order_acquire)
           && !transportListenStarted.load(std::memory_order_acquire);
}

juce::AudioProcessorValueTreeState::ParameterLayout ErnieAudioProcessor::createParameterLayout()
{
    juce::AudioProcessorValueTreeState::ParameterLayout layout;

    layout.add(std::make_unique<juce::AudioParameterFloat>(
        juce::ParameterID { "gain", 1 },
        "Gain",
        juce::NormalisableRange<float> { 0.0f, 1.0f, 0.001f },
        1.0f));

    return layout;
}

const juce::String ErnieAudioProcessor::getName() const
{
    return JucePlugin_Name;
}

bool ErnieAudioProcessor::acceptsMidi() const
{
#if JucePlugin_WantsMidiInput
    return true;
#else
    return false;
#endif
}

bool ErnieAudioProcessor::producesMidi() const
{
#if JucePlugin_ProducesMidiOutput
    return true;
#else
    return false;
#endif
}

bool ErnieAudioProcessor::isMidiEffect() const
{
#if JucePlugin_IsMidiEffect
    return true;
#else
    return false;
#endif
}

double ErnieAudioProcessor::getTailLengthSeconds() const
{
    return 0.0;
}

int ErnieAudioProcessor::getNumPrograms()
{
    return 1;
}

int ErnieAudioProcessor::getCurrentProgram()
{
    return 0;
}

void ErnieAudioProcessor::setCurrentProgram(int index)
{
    juce::ignoreUnused(index);
}

const juce::String ErnieAudioProcessor::getProgramName(int index)
{
    juce::ignoreUnused(index);
    return {};
}

void ErnieAudioProcessor::changeProgramName(int index, const juce::String& newName)
{
    juce::ignoreUnused(index, newName);
}

void ErnieAudioProcessor::prepareToPlay(double sampleRate, int samplesPerBlock)
{
    hostSampleRate.store(sampleRate);
    hostBlockSize.store(samplesPerBlock);
}

void ErnieAudioProcessor::releaseResources()
{
}

bool ErnieAudioProcessor::isBusesLayoutSupported(const BusesLayout& layouts) const
{
#if JucePlugin_IsMidiEffect
    juce::ignoreUnused(layouts);
    return true;
#else
    if (layouts.getMainOutputChannelSet() != juce::AudioChannelSet::mono()
        && layouts.getMainOutputChannelSet() != juce::AudioChannelSet::stereo())
        return false;

#if !JucePlugin_IsSynth
    if (layouts.getMainOutputChannelSet() != layouts.getMainInputChannelSet())
        return false;
#endif

    return true;
#endif
}

void ErnieAudioProcessor::processBlock(juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midiMessages)
{
    juce::ignoreUnused(midiMessages);
    juce::ScopedNoDenormals noDenormals;

    juce::Optional<juce::AudioPlayHead::PositionInfo> position;
    if (auto* playHead = getPlayHead())
        position = playHead->getPosition();

    // A requested listen arms the ring, but only copies audio while the DAW
    // transport is playing. Stop at the first stopped block after playback.
    if (transportListenArmed.load(std::memory_order_acquire))
    {
        const auto playing = position.hasValue() && (*position).getIsPlaying();
        if (playing)
        {
            transportListenStarted.store(true, std::memory_order_release);
            recorder.capture(buffer);
            if (recorder.hasWrapped())
            {
                transportListenArmed.store(false, std::memory_order_release);
                transportListenStarted.store(false, std::memory_order_release);
                recorder.stop();
                finalizeListenRequested.store(true, std::memory_order_release);
                triggerAsyncUpdate();
            }
        }
        else if (transportListenStarted.exchange(false, std::memory_order_acq_rel))
        {
            transportListenArmed.store(false, std::memory_order_release);
            recorder.stop();
            finalizeListenRequested.store(true, std::memory_order_release);
            triggerAsyncUpdate();
        }
    }
    else if (recorder.isRecording())
    {
        // Manual recording keeps its existing behavior.
        recorder.capture(buffer);
    }

    publishSnapshot(position.hasValue() ? &(*position) : nullptr);

    if (recorder.isRecording())
    {
        // Keep extra output channels deterministic without touching inputs.
        for (auto i = getTotalNumInputChannels(); i < getTotalNumOutputChannels(); ++i)
            buffer.clear(i, 0, buffer.getNumSamples());
        return;
    }

    const auto totalNumInputChannels = getTotalNumInputChannels();
    const auto totalNumOutputChannels = getTotalNumOutputChannels();

    for (auto i = totalNumInputChannels; i < totalNumOutputChannels; ++i)
        buffer.clear(i, 0, buffer.getNumSamples());

    const auto gain = gainParameter != nullptr ? gainParameter->load() : 1.0f;
    if (std::abs(gain - 1.0f) < 1.0e-6f)
        return;

    for (int channel = 0; channel < totalNumInputChannels; ++channel)
        buffer.applyGain(channel, 0, buffer.getNumSamples(), gain);
}

bool ErnieAudioProcessor::hasEditor() const
{
    return true;
}

juce::AudioProcessorEditor* ErnieAudioProcessor::createEditor()
{
    return new ErnieAudioProcessorEditor(*this);
}

void ErnieAudioProcessor::getStateInformation(juce::MemoryBlock& destData)
{
    if (auto state = parameters.copyState(); state.isValid())
    {
        const auto settings = recorder.getSettings();
        state.setProperty("captureSampleRate", settings.captureSampleRate, nullptr);
        state.setProperty("bufferDurationSeconds", settings.bufferDurationSeconds, nullptr);
        state.setProperty("matchHostSampleRate", settings.matchHostSampleRate, nullptr);

        if (auto xml = state.createXml())
            copyXmlToBinary(*xml, destData);
    }
}

void ErnieAudioProcessor::setStateInformation(const void* data, int sizeInBytes)
{
    if (auto xml = getXmlFromBinary(data, sizeInBytes))
    {
        if (xml->hasTagName(parameters.state.getType()))
        {
            auto state = juce::ValueTree::fromXml(*xml);
            parameters.replaceState(state);

            RecorderSettings settings;
            settings.captureSampleRate = static_cast<double>(state.getProperty("captureSampleRate", 48000.0));
            settings.bufferDurationSeconds = static_cast<double>(state.getProperty("bufferDurationSeconds", 8.0));
            settings.matchHostSampleRate = static_cast<bool>(state.getProperty("matchHostSampleRate", false));
            setRecorderSettings(settings);
        }
    }
}

void ErnieAudioProcessor::updateTrackProperties(const TrackProperties& properties)
{
    {
        const juce::ScopedLock lock(stateLock);
        hasTrackName = properties.name.has_value();
        trackName = hasTrackName ? *properties.name : juce::String();
        hasTrackColour = properties.colourARGB.has_value();
        trackColourArgb = hasTrackColour ? *properties.colourARGB : 0;
    }

    notifyListeners();
}

juce::Result ErnieAudioProcessor::setRecorderSettings(const RecorderSettings& settings)
{
    const auto result = recorder.setSettings(settings, hostSampleRate.load());

    {
        const juce::ScopedLock lock(stateLock);
        lastError = result.failed() ? result.getErrorMessage() : juce::String();
    }

    notifyListeners();
    return result;
}

juce::Result ErnieAudioProcessor::startRecording()
{
    if (recorder.isRecording())
        return juce::Result::ok();

    const auto channels = juce::jmax(1, getTotalNumInputChannels());
    const auto sessionId = juce::Time::getCurrentTime().formatted("%Y%m%d-%H%M%S")
                           + "-" + juce::Uuid().toDashedString().substring(0, 8);

    const auto result = recorder.start(hostSampleRate.load(), channels, sessionId);
    if (result.failed())
    {
        const juce::ScopedLock lock(stateLock);
        lastError = result.getErrorMessage();
        notifyListeners();
        return result;
    }

    {
        const juce::ScopedLock lock(stateLock);
        activeSessionId = sessionId;
        recordingStartedAtMs = juce::Time::currentTimeMillis();
        lastError.clear();
        cachedFileTags = fileMetadata.read();
    }

    metadataWriter.startWriting(sessionId);
    notifyListeners();
    return juce::Result::ok();
}

void ErnieAudioProcessor::stopRecording()
{
    if (!recorder.isRecording())
        return;

    transportListenArmed.store(false, std::memory_order_release);
    transportListenStarted.store(false, std::memory_order_release);
    recorder.stop();
    finalizeCapture();
    notifyListeners();
}

void ErnieAudioProcessor::finalizeCapture()
{
    metadataWriter.stopWriting();
    if (transportListenCapture.exchange(false, std::memory_order_acq_rel))
        writeCaptureWav();
    const juce::ScopedLock lock(stateLock);
    recordingStartedAtMs = 0;
}

void ErnieAudioProcessor::writeCaptureWav()
{
    const auto frames = static_cast<int>(juce::jmin<juce::int64>(recorder.getSamplesWritten(), recorder.getCapacity()));
    const auto channels = juce::jlimit(1, TrackRecorder::maxChannels, recorder.getActiveChannels());
    const auto sampleRate = recorder.getActiveCaptureRate();
    if (frames <= 0 || sampleRate <= 0.0)
        return;

    juce::AudioBuffer<float> audio(channels, frames);
    float* destinations[TrackRecorder::maxChannels] = { audio.getWritePointer(0), nullptr };
    if (channels > 1)
        destinations[1] = audio.getWritePointer(1);
    if (recorder.copyRecent(destinations, channels, frames) != frames)
        return;

    const auto directory = juce::File::getSpecialLocation(juce::File::userDocumentsDirectory)
                               .getChildFile("ernie").getChildFile("captures");
    if (!directory.createDirectory().wasOk())
        return;

    const auto session = recorder.getSessionId().isNotEmpty()
                             ? recorder.getSessionId()
                             : juce::Uuid().toDashedString();
    const auto file = directory.getChildFile("capture-" + session + ".wav");
    std::unique_ptr<juce::OutputStream> stream(file.createOutputStream().release());
    if (stream == nullptr)
        return;

    juce::WavAudioFormat wav;
    auto* writerRaw = wav.createWriterFor(stream.get(), sampleRate, static_cast<unsigned int>(channels), 24, {}, 0);
    if (writerRaw == nullptr)
        return;
    stream.release(); // the writer owns the output stream from here
    std::unique_ptr<juce::AudioFormatWriter> writer(writerRaw);
    if (!writer->writeFromAudioSampleBuffer(audio, 0, frames))
        return;
    writer.reset(); // closes and flushes the file before it is published

    const juce::ScopedLock lock(stateLock);
    lastCaptureFile = file;
}

juce::File ErnieAudioProcessor::getLastCaptureFile() const
{
    const juce::ScopedLock lock(stateLock);
    return lastCaptureFile;
}

RecordingStatus ErnieAudioProcessor::getRecordingStatus() const
{
    return makeStatus();
}

juce::String ErnieAudioProcessor::getLastError() const
{
    const juce::ScopedLock lock(stateLock);
    return lastError;
}

int ErnieAudioProcessor::copyRecentCapture(float* dest, int numFrames) const
{
    if (dest == nullptr || numFrames <= 0)
        return 0;

    float* channels[] = { dest };
    return recorder.copyRecent(channels, 1, numFrames);
}

void ErnieAudioProcessor::addChangeListener(juce::ChangeListener* listener)
{
    changeBroadcaster.addChangeListener(listener);
}

void ErnieAudioProcessor::removeChangeListener(juce::ChangeListener* listener)
{
    changeBroadcaster.removeChangeListener(listener);
}

void ErnieAudioProcessor::handleAsyncUpdate()
{
    if (finalizeListenRequested.exchange(false, std::memory_order_acq_rel))
    {
        finalizeCapture();
        notifyListeners();
    }
    changeBroadcaster.sendChangeMessage();
}

juce::Result ErnieAudioProcessor::handleApiCommand(const RecordingCommand& command)
{
    if (command.type == RecordingCommand::Type::stop)
    {
        // The API thread must not touch the processor after the instance is gone.
        juce::MessageManager::callSync([this] { stopRecording(); });
        return juce::Result::ok();
    }

    if (command.type == RecordingCommand::Type::setSourceFile)
    {
        setSourceAudioFile(command.sourceAudioFile);
        return juce::Result::ok();
    }

    if (command.type == RecordingCommand::Type::updateSettings || command.type == RecordingCommand::Type::start
        || command.type == RecordingCommand::Type::listen)
    {
        if (command.hasSampleRate || command.hasDuration || command.matchHostSampleRate)
        {
            auto settings = recorder.getSettings();
            settings.matchHostSampleRate = command.matchHostSampleRate;
            if (command.hasSampleRate)
                settings.captureSampleRate = command.captureSampleRate;
            if (command.hasDuration)
                settings.bufferDurationSeconds = command.bufferDurationSeconds;

            if (auto result = setRecorderSettings(settings); result.failed())
                return result;
        }
    }

    if (command.type == RecordingCommand::Type::start)
    {
        juce::Result result = juce::Result::ok();
        juce::MessageManager::callSync([this, &result]
        {
            if (recorder.isRecording())
            {
                result = juce::Result::fail("A capture is already active on this plugin instance");
                return;
            }
            transportListenCapture.store(false, std::memory_order_release);
            result = startRecording();
        });
        return result;
    }

    if (command.type == RecordingCommand::Type::listen)
    {
        juce::Result result = juce::Result::ok();
        juce::MessageManager::callSync([this, &result]
        {
            if (recorder.isRecording())
            {
                result = juce::Result::fail("A capture is already active on this plugin instance");
                return;
            }
            transportListenStarted.store(false, std::memory_order_release);
            transportListenCapture.store(true, std::memory_order_release);
            transportListenArmed.store(true, std::memory_order_release);
            result = startRecording();
            if (result.wasOk())
            {
                {
                    const juce::ScopedLock lock(stateLock);
                    lastCaptureFile = {};
                }
            }
            else
            {
                transportListenArmed.store(false, std::memory_order_release);
                transportListenCapture.store(false, std::memory_order_release);
            }
        });
        return result;
    }

    return juce::Result::ok();
}

RecordingStatus ErnieAudioProcessor::makeStatus() const
{
    const auto settings = recorder.getSettings();
    RecordingStatus status;
    status.recording = recorder.isRecording()
                       && (!transportListenArmed.load(std::memory_order_acquire)
                           || transportListenStarted.load(std::memory_order_acquire));
    status.waitingForPlayback = transportListenArmed.load(std::memory_order_acquire)
                                && !transportListenStarted.load(std::memory_order_acquire);
    status.matchHostSampleRate = settings.matchHostSampleRate;
    status.captureSampleRate = settings.captureSampleRate;
    status.bufferDurationSeconds = settings.bufferDurationSeconds;
    status.hostSampleRate = hostSampleRate.load();
    status.samplesWritten = recorder.getSamplesWritten();
    status.ramBytes = recorder.ramBytes();
    status.apiRunning = api.isRunning();
    status.port = api.getPort();
    status.handshakeReady = api.getToken().isNotEmpty();
    {
        const juce::ScopedLock lock(stateLock);
        status.sessionId = activeSessionId;
        status.lastError = lastError;
        status.captureReady = lastCaptureFile.existsAsFile();
    }
    return status;
}

void ErnieAudioProcessor::publishSnapshot(const juce::AudioPlayHead::PositionInfo* position)
{
    SessionSnapshot snapshot;
    snapshot.capturedAtMs = juce::Time::currentTimeMillis();
    snapshot.recording = recorder.isRecording()
                         && (!transportListenArmed.load(std::memory_order_acquire)
                             || transportListenStarted.load(std::memory_order_acquire));
    snapshot.hostSampleRate = hostSampleRate.load();
    snapshot.hostBlockSize = hostBlockSize.load();
    snapshot.hostDescription = juce::PluginHostType().getHostDescription();
    snapshot.pluginFormat = wrapperName(wrapperType);
    snapshot.sessionId = recorder.getSessionId();

    {
        const juce::ScopedLock lock(stateLock);
        snapshot.recordingStartedAtMs = recordingStartedAtMs;
        snapshot.hasTrackName = hasTrackName;
        snapshot.trackName = trackName;
        snapshot.hasTrackColour = hasTrackColour;
        snapshot.trackColourArgb = trackColourArgb;
    }

    applyTransport(snapshot, position);
    applyTempoAndKey(snapshot);

    const auto settings = recorder.getSettings();
    snapshot.captureSampleRate = recorder.isRecording() ? recorder.getActiveCaptureRate() : settings.captureSampleRate;
    snapshot.bufferDurationSeconds = settings.bufferDurationSeconds;
    snapshot.numChannels = recorder.getActiveChannels();
    snapshot.samplesWritten = recorder.getSamplesWritten();
    snapshot.bufferCapacitySamples = recorder.getCapacity();
    snapshot.bufferWrapped = recorder.hasWrapped();
    snapshot.ramBytes = recorder.ramBytes();

    snapshots.publish(snapshot);
}

void ErnieAudioProcessor::applyTransport(SessionSnapshot& snapshot, const juce::AudioPlayHead::PositionInfo* position)
{
    if (position == nullptr)
    {
        snapshot.playHeadAvailable = false;
        snapshot.playHeadMoving = false;
        snapshot.playheadNote = "host has not provided a playhead";
        clearPlayheadLocation(snapshot);
        havePlayheadSample = false;
        havePlayheadPpq = false;
        return;
    }

    fillPlayHead(snapshot, *position);

    const auto sampleMoved = snapshot.hasTimeInSamples && havePlayheadSample
                             && snapshot.timeInSamples != lastPlayheadSample;
    const auto ppqMoved = snapshot.hasPpq && havePlayheadPpq
                          && std::abs(snapshot.ppqPosition - lastPlayheadPpq) > 1.0e-4;
    const auto firstSight = (snapshot.hasTimeInSamples && !havePlayheadSample)
                            || (snapshot.hasPpq && !havePlayheadPpq);

    snapshot.playHeadMoving = snapshot.transportPlaying && (sampleMoved || ppqMoved || firstSight);

    if (snapshot.hasTimeInSamples)
    {
        lastPlayheadSample = snapshot.timeInSamples;
        havePlayheadSample = true;
    }

    if (snapshot.hasPpq)
    {
        lastPlayheadPpq = snapshot.ppqPosition;
        havePlayheadPpq = true;
    }

    if (!snapshot.playHeadMoving)
    {
        clearPlayheadLocation(snapshot);
        snapshot.playheadNote = snapshot.transportPlaying
                                    ? "transport is marked playing, but the playhead has not moved"
                                    : "playhead is not moving";
        return;
    }

    if (snapshot.hasTimeInSeconds)
        snapshot.timeInSeconds = std::round(snapshot.timeInSeconds * 10.0) / 10.0;

    if (snapshot.hasMeasureBeat)
        snapshot.beatInMeasure = std::round(snapshot.beatInMeasure * 4.0) / 4.0;

    snapshot.playheadNote = "moving";
}

void ErnieAudioProcessor::setSourceAudioFile(const juce::String& path)
{
    fileMetadata.setPath(path);
    const juce::ScopedLock lock(stateLock);
    cachedFileTags = fileMetadata.read();
}

void ErnieAudioProcessor::applyTempoAndKey(SessionSnapshot& snapshot) const
{
    FileTags tags;
    {
        const juce::ScopedLock lock(stateLock);
        tags = cachedFileTags;
    }

    if (snapshot.hasBpm && snapshot.bpm > 0.0)
    {
        snapshot.bpmSource = "daw";
        snapshot.bpmNote = "from the host transport";
    }
    else if (tags.hasBpm)
    {
        snapshot.hasBpm = true;
        snapshot.bpm = tags.bpm;
        snapshot.bpmSource = "file";
        snapshot.bpmNote = "from source audio file tags: " + tags.path;
    }
    else
    {
        snapshot.hasBpm = false;
        snapshot.bpmSource.clear();
        snapshot.bpmNote = tags.path.isEmpty() ? "host did not report BPM, and no source audio file is configured"
                                               : tags.note;
    }

    if (snapshot.keyNote >= 0)
    {
        snapshot.hasKey = true;
        snapshot.key = noteName(snapshot.keyNote);
        snapshot.keySource = "daw";
        snapshot.keyNoteText = "from the VST3 process-context chord";
        return;
    }

    int parsed = -1;
    if (tags.hasKey)
    {
        snapshot.hasKey = true;
        snapshot.key = tags.key;
        snapshot.keySource = "file";
        snapshot.keyNoteText = "from source audio file tags: " + tags.path;
        if (parseKeyNote(tags.key, parsed))
            snapshot.keyNote = parsed;
        return;
    }

    snapshot.hasKey = false;
    snapshot.keySource.clear();
    snapshot.keyNoteText = wrapperType == wrapperType_VST3
                               ? "host did not send a VST3 chord, and the source audio file has no key tag"
                               : "this plugin format does not expose a key, and the source audio file has no key tag";
    if (tags.note.isNotEmpty() && tags.path.isNotEmpty())
        snapshot.keyNoteText = tags.note;
}

void ErnieAudioProcessor::notifyListeners()
{
    if (juce::MessageManager::getInstance()->isThisTheMessageThread())
        changeBroadcaster.sendChangeMessage();
    else
        triggerAsyncUpdate();
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new ErnieAudioProcessor();
}
