#include "MetadataWriter.h"

MetadataWriter::MetadataWriter(SessionSnapshotStore& storeToUse)
    : store(storeToUse)
{
}

MetadataWriter::~MetadataWriter()
{
    stopTimer();
}

juce::File MetadataWriter::getDocumentsFolder()
{
    auto folder = juce::File::getSpecialLocation(juce::File::userDocumentsDirectory).getChildFile("ernie");
    folder.createDirectory();
    return folder;
}

void MetadataWriter::startWriting(const juce::String& newSessionId)
{
    sessionId = newSessionId;
    active = true;
    writeNow();
    startTimer(250);
}

void MetadataWriter::stopWriting()
{
    stopTimer();
    if (active)
        writeNow();
    active = false;
}

void MetadataWriter::writeNow()
{
    writeSnapshot(store.read());
}

juce::File MetadataWriter::getSessionFile() const
{
    return getDocumentsFolder().getChildFile("ernie-" + sessionId + ".json");
}

juce::File MetadataWriter::getLatestFile() const
{
    return getDocumentsFolder().getChildFile("ernie-latest.json");
}

void MetadataWriter::timerCallback()
{
    if (active)
        writeNow();
}

void MetadataWriter::writeSnapshot(const SessionSnapshot& snapshot) const
{
    const auto json = snapshotToJson(snapshot);
    const auto folder = getDocumentsFolder();
    folder.getChildFile("ernie-latest.json").replaceWithText(json);

    if (sessionId.isNotEmpty())
        folder.getChildFile("ernie-" + sessionId + ".json").replaceWithText(json);
}

juce::String MetadataWriter::snapshotToJson(const SessionSnapshot& snapshot)
{
    auto optionalNumber = [](auto& object, const char* key, bool present, double value)
    {
        if (present)
            object->setProperty(key, value);
        else
            object->setProperty(key, juce::var());
    };

    auto optionalInt = [](auto& object, const char* key, bool present, juce::int64 value)
    {
        if (present)
            object->setProperty(key, value);
        else
            object->setProperty(key, juce::var());
    };

    auto root = std::make_unique<juce::DynamicObject>();
    root->setProperty("schema", "ernie.session.v1");
    root->setProperty("session_id", snapshot.sessionId);
    root->setProperty("recording", snapshot.recording);
    root->setProperty("captured_at", juce::Time(snapshot.capturedAtMs).toISO8601(true));
    root->setProperty("recording_started_at",
                      snapshot.recordingStartedAtMs > 0
                          ? juce::var(juce::Time(snapshot.recordingStartedAtMs).toISO8601(true))
                          : juce::var());

    auto host = std::make_unique<juce::DynamicObject>();
    host->setProperty("description", snapshot.hostDescription);
    host->setProperty("plugin_format", snapshot.pluginFormat);
    host->setProperty("sample_rate", snapshot.hostSampleRate);
    host->setProperty("block_size", snapshot.hostBlockSize);
    root->setProperty("host", juce::var(host.release()));

    auto playhead = std::make_unique<juce::DynamicObject>();
    playhead->setProperty("available", snapshot.playHeadAvailable);
    playhead->setProperty("playing", snapshot.transportPlaying);
    playhead->setProperty("moving", snapshot.playHeadMoving);
    playhead->setProperty("note", snapshot.playheadNote);
    playhead->setProperty("host_recording", snapshot.transportRecording);
    playhead->setProperty("looping", snapshot.transportLooping);
    optionalInt(playhead, "time_in_samples", snapshot.hasTimeInSamples, snapshot.timeInSamples);
    optionalNumber(playhead, "time_in_seconds", snapshot.hasTimeInSeconds, snapshot.timeInSeconds);
    optionalNumber(playhead, "bpm", snapshot.hasBpm, snapshot.bpm);
    playhead->setProperty("bpm_source", snapshot.hasBpm ? juce::var(snapshot.bpmSource) : juce::var());
    playhead->setProperty("bpm_note", snapshot.bpmNote);
    optionalNumber(playhead, "ppq", snapshot.hasPpq, snapshot.ppqPosition);
    optionalNumber(playhead, "ppq_of_last_bar_start", snapshot.hasLastBarPpq, snapshot.ppqOfLastBarStart);
    optionalInt(playhead, "bar_count", snapshot.hasBarCount, snapshot.barCount);
    optionalNumber(playhead, "edit_origin_seconds", snapshot.hasEditOrigin, snapshot.editOriginSeconds);
    optionalNumber(playhead, "frame_rate", snapshot.hasFrameRate, snapshot.frameRate);

    if (snapshot.hasTimeSignature)
    {
        auto signature = std::make_unique<juce::DynamicObject>();
        signature->setProperty("numerator", snapshot.timeSigNumerator);
        signature->setProperty("denominator", snapshot.timeSigDenominator);
        playhead->setProperty("time_signature", juce::var(signature.release()));
    }
    else
    {
        playhead->setProperty("time_signature", juce::var());
    }

    if (snapshot.hasLoopPoints)
    {
        auto loop = std::make_unique<juce::DynamicObject>();
        loop->setProperty("start_ppq", snapshot.loopStartPpq);
        loop->setProperty("end_ppq", snapshot.loopEndPpq);
        playhead->setProperty("loop", juce::var(loop.release()));
    }
    else
    {
        playhead->setProperty("loop", juce::var());
    }

    if (snapshot.hasMeasureBeat)
    {
        auto measure = std::make_unique<juce::DynamicObject>();
        measure->setProperty("measure", snapshot.measure);
        measure->setProperty("beat", snapshot.beatInMeasure);
        playhead->setProperty("measure_beat", juce::var(measure.release()));
    }
    else
    {
        playhead->setProperty("measure_beat", juce::var());
    }

    root->setProperty("playhead", juce::var(playhead.release()));

    auto track = std::make_unique<juce::DynamicObject>();
    track->setProperty("name", snapshot.hasTrackName ? juce::var(snapshot.trackName) : juce::var());
    track->setProperty("colour_argb", snapshot.hasTrackColour ? juce::var(static_cast<juce::int64>(snapshot.trackColourArgb)) : juce::var());
    track->setProperty("name_note",
                       snapshot.hasTrackName
                           ? juce::var("provided by the host")
                           : juce::var("unavailable: the host has not sent track properties"));
    root->setProperty("track", juce::var(track.release()));

    if (snapshot.hasKey)
    {
        auto key = std::make_unique<juce::DynamicObject>();
        key->setProperty("name", snapshot.key);
        key->setProperty("pitch_class", snapshot.keyNote >= 0 ? juce::var(snapshot.keyNote) : juce::var());
        key->setProperty("source", snapshot.keySource);
        root->setProperty("key", juce::var(key.release()));
    }
    else
    {
        root->setProperty("key", juce::var());
    }
    root->setProperty("key_note", snapshot.keyNoteText);
    root->setProperty("sibling_plugins", juce::var());
    root->setProperty("sibling_plugins_note", snapshot.siblingPluginsNote);

    auto capture = std::make_unique<juce::DynamicObject>();
    capture->setProperty("sample_rate", snapshot.captureSampleRate);
    capture->setProperty("buffer_duration_seconds", snapshot.bufferDurationSeconds);
    capture->setProperty("channels", snapshot.numChannels);
    capture->setProperty("samples_written", snapshot.samplesWritten);
    capture->setProperty("buffer_capacity_samples", snapshot.bufferCapacitySamples);
    capture->setProperty("wrapped", snapshot.bufferWrapped);
    capture->setProperty("ram_bytes", snapshot.ramBytes);
    capture->setProperty("storage", "ram");
    root->setProperty("capture", juce::var(capture.release()));

    return juce::JSON::toString(juce::var(root.release()), true);
}
