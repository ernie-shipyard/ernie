#pragma once

#include <juce_core/juce_core.h>
#include <juce_events/juce_events.h>

#include <functional>

// Localhost-only control plane. A client must complete a handshake before it
// can arm or disarm recording. The token is process-local and never written
// into the session JSON.
struct RecordingCommand
{
    enum class Type
    {
        start,
        listen,
        stop,
        updateSettings,
        setSourceFile
    };

    Type type = Type::stop;
    bool matchHostSampleRate = false;
    double captureSampleRate = 48000.0;
    double bufferDurationSeconds = 8.0;
    bool hasSampleRate = false;
    bool hasDuration = false;
    juce::String sourceAudioFile;
};

struct RecordingStatus
{
    bool recording = false;
    bool matchHostSampleRate = false;
    double captureSampleRate = 48000.0;
    double bufferDurationSeconds = 8.0;
    double hostSampleRate = 0.0;
    juce::int64 samplesWritten = 0;
    juce::int64 ramBytes = 0;
    bool apiRunning = false;
    int port = 0;
    bool handshakeReady = false;
    juce::String sessionId;
    juce::String lastError;
    bool waitingForPlayback = false;
    bool captureReady = false;
};

class RecordingApi : private juce::Thread
{
public:
    using CommandHandler = std::function<juce::Result(const RecordingCommand&)>;
    using StatusProvider = std::function<RecordingStatus()>;
    using CaptureFileProvider = std::function<juce::File()>;

    RecordingApi(CommandHandler onCommand, StatusProvider onStatus, CaptureFileProvider onCaptureFile = {});
    ~RecordingApi() override;

    juce::Result start(int preferredPort = 17831);
    void shutdown();

    bool isRunning() const noexcept { return running.load(); }
    int getPort() const noexcept { return boundPort.load(); }
    juce::String getToken() const;

private:
    void run() override;
    void serveClient(std::unique_ptr<juce::StreamingSocket> client);
    juce::String handleRequest(const juce::String& method, const juce::String& target, const juce::String& body, const juce::String& tokenHeader);
    bool tokenMatches(const juce::String& presented) const;

    CommandHandler commandHandler;
    StatusProvider statusProvider;
    CaptureFileProvider captureFileProvider;

    std::unique_ptr<juce::StreamingSocket> listener;
    std::atomic<bool> running { false };
    std::atomic<int> boundPort { 0 };

    mutable juce::CriticalSection tokenLock;
    juce::String token;
};
