#pragma once

#include "FileMetadata.h"
#include "MetadataWriter.h"
#include "RecordingApi.h"
#include "SessionMetadata.h"
#include "TrackRecorder.h"

#include <juce_audio_processors/juce_audio_processors.h>

class ErnieAudioProcessor final : public juce::AudioProcessor,
                                  private juce::AsyncUpdater
{
public:
    ErnieAudioProcessor();
    ~ErnieAudioProcessor() override;

    void prepareToPlay(double sampleRate, int samplesPerBlock) override;
    void releaseResources() override;

    bool isBusesLayoutSupported(const BusesLayout& layouts) const override;

    void processBlock(juce::AudioBuffer<float>&, juce::MidiBuffer&) override;
    using AudioProcessor::processBlock;

    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override;

    const juce::String getName() const override;

    bool acceptsMidi() const override;
    bool producesMidi() const override;
    bool isMidiEffect() const override;
    double getTailLengthSeconds() const override;

    int getNumPrograms() override;
    int getCurrentProgram() override;
    void setCurrentProgram(int index) override;
    const juce::String getProgramName(int index) override;
    void changeProgramName(int index, const juce::String& newName) override;

    void getStateInformation(juce::MemoryBlock& destData) override;
    void setStateInformation(const void* data, int sizeInBytes) override;

    void updateTrackProperties(const TrackProperties& properties) override;

    juce::AudioProcessorValueTreeState parameters;

    bool isRecording() const noexcept;
    bool isWaitingForPlayback() const noexcept;
    RecorderSettings getRecorderSettings() const { return recorder.getSettings(); }
    juce::Result setRecorderSettings(const RecorderSettings& settings);
    juce::Result startRecording();
    void stopRecording();

    RecordingStatus getRecordingStatus() const;
    int getApiPort() const noexcept { return api.getPort(); }
    juce::String getApiToken() const { return api.getToken(); }
    juce::String getLastError() const;

    int copyRecentCapture(float* const* dest, int destChannels, int numFrames) const;
    juce::File getLastCaptureFile() const;
    void addChangeListener(juce::ChangeListener* listener);
    void removeChangeListener(juce::ChangeListener* listener);

private:
    void handleAsyncUpdate() override;
    void finalizeCapture();
    void writeCaptureWav();
    juce::Result handleApiCommand(const RecordingCommand& command);
    RecordingStatus makeStatus() const;
    void publishSnapshot(const juce::AudioPlayHead::PositionInfo* position);
    void applyTransport(SessionSnapshot& snapshot, const juce::AudioPlayHead::PositionInfo* position);
    void applyTempoAndKey(SessionSnapshot& snapshot) const;
    void setSourceAudioFile(const juce::String& path);
    void notifyListeners();

    static juce::AudioProcessorValueTreeState::ParameterLayout createParameterLayout();

    std::atomic<float>* gainParameter = nullptr;

    TrackRecorder recorder;
    SessionSnapshotStore snapshots;
    MetadataWriter metadataWriter;
    RecordingApi api;
    juce::ChangeBroadcaster changeBroadcaster;

    std::atomic<double> hostSampleRate { 0.0 };
    std::atomic<int> hostBlockSize { 0 };
    std::atomic<bool> transportListenArmed { false };
    std::atomic<bool> transportListenStarted { false };
    std::atomic<bool> transportListenCapture { false };
    std::atomic<bool> finalizeListenRequested { false };

    mutable juce::CriticalSection stateLock;
    juce::String trackName;
    bool hasTrackName = false;
    juce::uint32 trackColourArgb = 0;
    bool hasTrackColour = false;
    juce::String lastError;
    juce::String activeSessionId;
    juce::File lastCaptureFile;
    juce::int64 recordingStartedAtMs = 0;

    bool havePlayheadSample = false;
    juce::int64 lastPlayheadSample = 0;
    bool havePlayheadPpq = false;
    double lastPlayheadPpq = 0.0;

    FileMetadata fileMetadata;
    FileTags cachedFileTags;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(ErnieAudioProcessor)
};
