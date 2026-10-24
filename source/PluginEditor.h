#pragma once

#include "PluginProcessor.h"

class ErnieAudioProcessorEditor final : public juce::AudioProcessorEditor
{
public:
    explicit ErnieAudioProcessorEditor(ErnieAudioProcessor& processorToUse);
    ~ErnieAudioProcessorEditor() override;

    void paint(juce::Graphics&) override;
    void resized() override;

private:
    ErnieAudioProcessor& processorRef;
    juce::Slider gainSlider;
    std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> gainAttachment;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(ErnieAudioProcessorEditor)
};
