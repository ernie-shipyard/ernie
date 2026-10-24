#pragma once

#include "PluginProcessor.h"
#include "WaveformDisplay.h"

class ErnieAudioProcessorEditor final : public juce::AudioProcessorEditor,
                                        private juce::ChangeListener,
                                        private juce::Timer
{
public:
    explicit ErnieAudioProcessorEditor(ErnieAudioProcessor& processorToUse);
    ~ErnieAudioProcessorEditor() override;

    void paint(juce::Graphics&) override;
    void resized() override;

private:
    void changeListenerCallback(juce::ChangeBroadcaster* source) override;
    void timerCallback() override;
    void refresh();
    void applySettingsFromUi();

    ErnieAudioProcessor& processorRef;

    juce::TextButton recordButton { "Record" };
    juce::ComboBox sampleRateBox;
    juce::Slider durationSlider;
    juce::Label durationLabel;
    juce::Label statusLabel;
    juce::Label apiLabel;
    WaveformDisplay waveform;

    bool updatingUi = false;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(ErnieAudioProcessorEditor)
};
