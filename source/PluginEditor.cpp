#include "PluginEditor.h"

ErnieAudioProcessorEditor::ErnieAudioProcessorEditor(ErnieAudioProcessor& processorToUse)
    : AudioProcessorEditor(&processorToUse),
      processorRef(processorToUse)
{
    recordButton.setClickingTogglesState(true);
    recordButton.onClick = [this]
    {
        if (updatingUi)
            return;

        if (recordButton.getToggleState())
        {
            applySettingsFromUi();
            if (processorRef.startRecording().failed())
                recordButton.setToggleState(false, juce::dontSendNotification);
        }
        else
        {
            processorRef.stopRecording();
        }

        refresh();
    };
    addAndMakeVisible(recordButton);

    sampleRateBox.addItem("Match host", 1);
    sampleRateBox.addItem("22.05 kHz", 2);
    sampleRateBox.addItem("44.1 kHz", 3);
    sampleRateBox.addItem("48 kHz", 4);
    sampleRateBox.addItem("88.2 kHz", 5);
    sampleRateBox.addItem("96 kHz", 6);
    sampleRateBox.addItem("192 kHz", 7);
    sampleRateBox.onChange = [this]
    {
        if (!updatingUi)
            applySettingsFromUi();
    };
    addAndMakeVisible(sampleRateBox);

    durationSlider.setSliderStyle(juce::Slider::LinearHorizontal);
    durationSlider.setTextBoxStyle(juce::Slider::TextBoxRight, false, 64, 20);
    durationSlider.setRange(TrackRecorder::minDurationSeconds, TrackRecorder::maxDurationSeconds, 0.25);
    durationSlider.setTextValueSuffix(" s");
    durationSlider.onValueChange = [this]
    {
        if (!updatingUi)
            applySettingsFromUi();
    };
    addAndMakeVisible(durationSlider);

    durationLabel.setText("Buffer", juce::dontSendNotification);
    durationLabel.setJustificationType(juce::Justification::centredRight);
    addAndMakeVisible(durationLabel);

    statusLabel.setJustificationType(juce::Justification::centredLeft);
    addAndMakeVisible(statusLabel);

    apiLabel.setJustificationType(juce::Justification::centredLeft);
    apiLabel.setFont(juce::FontOptions(12.0f));
    addAndMakeVisible(apiLabel);

    waveform.setSampleSource([this](float* const* dest, int destChannels, int numFrames)
    {
        return processorRef.copyRecentCapture(dest, destChannels, numFrames);
    });
    addAndMakeVisible(waveform);

    processorRef.addChangeListener(this);
    refresh();
    startTimerHz(8);
    setSize(520, 300);
}

ErnieAudioProcessorEditor::~ErnieAudioProcessorEditor()
{
    stopTimer();
    processorRef.removeChangeListener(this);
}

void ErnieAudioProcessorEditor::paint(juce::Graphics& g)
{
    g.fillAll(juce::Colour(0xff1b1e18));

    g.setColour(juce::Colours::white);
    g.setFont(juce::FontOptions(18.0f));
    g.drawFittedText("ernie stereo", 16, 8, 180, 24, juce::Justification::centredLeft, 1);
}

void ErnieAudioProcessorEditor::resized()
{
    auto bounds = getLocalBounds().reduced(16);
    bounds.removeFromTop(28);

    auto controls = bounds.removeFromTop(28);
    recordButton.setBounds(controls.removeFromLeft(88));
    controls.removeFromLeft(12);
    sampleRateBox.setBounds(controls.removeFromLeft(140));
    controls.removeFromLeft(12);
    durationLabel.setBounds(controls.removeFromLeft(52));
    durationSlider.setBounds(controls);

    bounds.removeFromTop(12);
    waveform.setBounds(bounds.removeFromTop(140));
    bounds.removeFromTop(10);
    statusLabel.setBounds(bounds.removeFromTop(22));
    apiLabel.setBounds(bounds.removeFromTop(36));
}

void ErnieAudioProcessorEditor::changeListenerCallback(juce::ChangeBroadcaster*)
{
    refresh();
}

void ErnieAudioProcessorEditor::timerCallback()
{
    refresh();
}

void ErnieAudioProcessorEditor::applySettingsFromUi()
{
    RecorderSettings settings;
    settings.matchHostSampleRate = sampleRateBox.getSelectedId() == 1;

    switch (sampleRateBox.getSelectedId())
    {
        case 2: settings.captureSampleRate = 22050.0; break;
        case 3: settings.captureSampleRate = 44100.0; break;
        case 4: settings.captureSampleRate = 48000.0; break;
        case 5: settings.captureSampleRate = 88200.0; break;
        case 6: settings.captureSampleRate = 96000.0; break;
        case 7: settings.captureSampleRate = 192000.0; break;
        default: settings.captureSampleRate = processorRef.getRecorderSettings().captureSampleRate; break;
    }

    settings.bufferDurationSeconds = durationSlider.getValue();
    processorRef.setRecorderSettings(settings);
}

void ErnieAudioProcessorEditor::refresh()
{
    const auto settings = processorRef.getRecorderSettings();
    const auto recording = processorRef.isRecording();
    const auto waitingForPlayback = processorRef.isWaitingForPlayback();
    const auto captureActive = recording || waitingForPlayback;
    const auto status = processorRef.getRecordingStatus();

    updatingUi = true;
    recordButton.setToggleState(captureActive, juce::dontSendNotification);
    recordButton.setButtonText(waitingForPlayback ? "Waiting for playback" : recording ? "Stop" : "Record");
    sampleRateBox.setEnabled(!captureActive);
    durationSlider.setEnabled(!captureActive);

    const auto selected = settings.matchHostSampleRate
                              ? 1
                              : (std::abs(settings.captureSampleRate - 22050.0) < 1.0 ? 2
                                 : std::abs(settings.captureSampleRate - 44100.0) < 1.0 ? 3
                                 : std::abs(settings.captureSampleRate - 48000.0) < 1.0 ? 4
                                 : std::abs(settings.captureSampleRate - 88200.0) < 1.0 ? 5
                                 : std::abs(settings.captureSampleRate - 96000.0) < 1.0 ? 6
                                 : std::abs(settings.captureSampleRate - 192000.0) < 1.0 ? 7
                                                                                         : 4);
    sampleRateBox.setSelectedId(selected, juce::dontSendNotification);

    if (!durationSlider.isMouseButtonDown())
        durationSlider.setValue(settings.bufferDurationSeconds, juce::dontSendNotification);
    updatingUi = false;

    waveform.setActive(recording);

    const auto ramMb = static_cast<double>(status.ramBytes) / (1024.0 * 1024.0);
    auto statusText = waitingForPlayback
                          ? juce::String("Agent listen armed — waiting for DAW playback")
                          : recording
                          ? "Recording  " + juce::String(status.samplesWritten) + " frames   "
                                + juce::String(ramMb, 1) + " MB RAM"
                          : "Passthrough";

    if (status.lastError.isNotEmpty())
        statusText += "   " + status.lastError;

    statusLabel.setText(statusText, juce::dontSendNotification);

    auto apiText = "API  127.0.0.1:" + juce::String(processorRef.getApiPort())
                   + "    handshake POST /v1/handshake";
    if (const auto token = processorRef.getApiToken(); token.isNotEmpty())
        apiText += "\nToken  " + token;

    apiLabel.setText(apiText, juce::dontSendNotification);
}
