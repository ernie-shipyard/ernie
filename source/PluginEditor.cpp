#include "PluginEditor.h"

ErnieAudioProcessorEditor::ErnieAudioProcessorEditor(ErnieAudioProcessor& processorToUse)
    : AudioProcessorEditor(&processorToUse),
      processorRef(processorToUse)
{
    gainSlider.setSliderStyle(juce::Slider::RotaryHorizontalVerticalDrag);
    gainSlider.setTextBoxStyle(juce::Slider::TextBoxBelow, false, 80, 20);
    addAndMakeVisible(gainSlider);

    gainAttachment = std::make_unique<juce::AudioProcessorValueTreeState::SliderAttachment>(
        processorRef.parameters, "gain", gainSlider);

    setSize(360, 240);
}

ErnieAudioProcessorEditor::~ErnieAudioProcessorEditor() = default;

void ErnieAudioProcessorEditor::paint(juce::Graphics& g)
{
    g.fillAll(getLookAndFeel().findColour(juce::ResizableWindow::backgroundColourId));

    g.setColour(juce::Colours::white);
    g.setFont(18.0f);
    g.drawFittedText("Ernie", getLocalBounds().removeFromTop(40), juce::Justification::centred, 1);
}

void ErnieAudioProcessorEditor::resized()
{
    auto bounds = getLocalBounds().reduced(20);
    bounds.removeFromTop(40);
    gainSlider.setBounds(bounds);
}
