#pragma once

#include <juce_audio_formats/juce_audio_formats.h>

// Reads tempo and key tags from a user-supplied audio file. This is only a
// fallback: an insert plugin is not given the source clip path by the host.
struct FileTags
{
    bool hasBpm = false;
    double bpm = 0.0;
    bool hasKey = false;
    juce::String key;
    juce::String path;
    juce::String note;
};

class FileMetadata
{
public:
    FileMetadata();

    void setPath(const juce::String& path);
    juce::String getPath() const;
    FileTags read() const;

private:
    mutable juce::AudioFormatManager formats;
    mutable juce::CriticalSection pathLock;
    juce::String filePath;
};
