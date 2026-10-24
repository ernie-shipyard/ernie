#pragma once

#include "SessionMetadata.h"

#include <juce_events/juce_events.h>

// Periodically writes the latest host snapshot to ~/Documents/ernie.
// File IO never runs on the audio thread.
class MetadataWriter : private juce::Timer
{
public:
    explicit MetadataWriter(SessionSnapshotStore& storeToUse);
    ~MetadataWriter() override;

    void startWriting(const juce::String& sessionId);
    void stopWriting();
    void writeNow();

    juce::File getSessionFile() const;
    juce::File getLatestFile() const;

    static juce::File getDocumentsFolder();
    static juce::String snapshotToJson(const SessionSnapshot& snapshot);

private:
    void timerCallback() override;
    void writeSnapshot(const SessionSnapshot& snapshot) const;

    SessionSnapshotStore& store;
    juce::String sessionId;
    bool active = false;
};
