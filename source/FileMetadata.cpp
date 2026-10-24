#include "FileMetadata.h"

namespace
{
juce::String findValue(const juce::StringPairArray& values, std::initializer_list<const char*> keys)
{
    for (auto key : keys)
    {
        auto value = values.getValue(key, {});
        if (value.isNotEmpty())
            return value;

        value = values.getValue(juce::String(key).toLowerCase(), {});
        if (value.isNotEmpty())
            return value;
    }

    const auto& allKeys = values.getAllKeys();
    const auto& allValues = values.getAllValues();

    for (auto wanted : keys)
    {
        const auto needle = juce::String(wanted).toLowerCase();
        for (int i = 0; i < allKeys.size(); ++i)
        {
            if (allKeys[i].toLowerCase().contains(needle) && allValues[i].isNotEmpty())
                return allValues[i];
        }
    }

    return {};
}

bool parseBpm(const juce::String& text, double& bpm)
{
    auto trimmed = text.trim();
    if (trimmed.isEmpty())
        return false;

    bpm = trimmed.getDoubleValue();
    return bpm >= 20.0 && bpm <= 400.0;
}
}

FileMetadata::FileMetadata()
{
    formats.registerBasicFormats();
}

void FileMetadata::setPath(const juce::String& path)
{
    const juce::ScopedLock lock(pathLock);
    filePath = path.trim();
}

juce::String FileMetadata::getPath() const
{
    const juce::ScopedLock lock(pathLock);
    return filePath;
}

FileTags FileMetadata::read() const
{
    FileTags tags;
    juce::String path;
    {
        const juce::ScopedLock lock(pathLock);
        path = filePath;
    }
    tags.path = path;

    if (path.isEmpty())
    {
        tags.note = "no source audio file configured";
        return tags;
    }

    const juce::File file(path);
    if (!file.existsAsFile())
    {
        tags.note = "source audio file does not exist";
        return tags;
    }

    std::unique_ptr<juce::AudioFormatReader> reader(formats.createReaderFor(file));
    if (reader == nullptr)
    {
        tags.note = "could not read source audio file metadata";
        return tags;
    }

    const auto& values = reader->metadataValues;
    double bpm = 0.0;
    if (parseBpm(findValue(values, { "Tempo", "TBPM", "BPM", "bpm", "id3 BPM" }), bpm))
    {
        tags.hasBpm = true;
        tags.bpm = bpm;
    }

    auto key = findValue(values, { "Key", "initialkey", "TKEY", "key", "InitialKey" });
    if (key.isNotEmpty())
    {
        tags.hasKey = true;
        tags.key = key.trim();
    }

    tags.note = tags.hasBpm || tags.hasKey ? "read from source audio file tags"
                                           : "source audio file has no tempo or key tag";
    return tags;
}
