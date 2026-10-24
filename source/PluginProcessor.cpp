#include "PluginProcessor.h"
#include "PluginEditor.h"

ErnieAudioProcessor::ErnieAudioProcessor()
    : AudioProcessor(BusesProperties()
#if !JucePlugin_IsMidiEffect
#if !JucePlugin_IsSynth
                         .withInput("Input", juce::AudioChannelSet::stereo(), true)
#endif
                         .withOutput("Output", juce::AudioChannelSet::stereo(), true)
#endif
                         ),
      parameters(*this, nullptr, "PARAMETERS", createParameterLayout())
{
    gainParameter = parameters.getRawParameterValue("gain");
}

ErnieAudioProcessor::~ErnieAudioProcessor() = default;

juce::AudioProcessorValueTreeState::ParameterLayout ErnieAudioProcessor::createParameterLayout()
{
    juce::AudioProcessorValueTreeState::ParameterLayout layout;

    layout.add(std::make_unique<juce::AudioParameterFloat>(
        juce::ParameterID { "gain", 1 },
        "Gain",
        juce::NormalisableRange<float> { 0.0f, 1.0f, 0.001f },
        0.8f));

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
    juce::ignoreUnused(sampleRate, samplesPerBlock);
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
    const auto totalNumInputChannels = getTotalNumInputChannels();
    const auto totalNumOutputChannels = getTotalNumOutputChannels();

    for (auto i = totalNumInputChannels; i < totalNumOutputChannels; ++i)
        buffer.clear(i, 0, buffer.getNumSamples());

    const auto gain = gainParameter != nullptr ? gainParameter->load() : 1.0f;

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
        if (auto xml = state.createXml())
            copyXmlToBinary(*xml, destData);
    }
}

void ErnieAudioProcessor::setStateInformation(const void* data, int sizeInBytes)
{
    if (auto xml = getXmlFromBinary(data, sizeInBytes))
        if (xml->hasTagName(parameters.state.getType()))
            parameters.replaceState(juce::ValueTree::fromXml(*xml));
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new ErnieAudioProcessor();
}
