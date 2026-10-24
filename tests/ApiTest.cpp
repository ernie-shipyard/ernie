#include "RecordingApi.h"

#include <juce_core/juce_core.h>
#include <juce_events/juce_events.h>

#include <iostream>

namespace
{
int failures = 0;

void expect(bool condition, const char* message)
{
    if (!condition)
    {
        std::cerr << "FAIL: " << message << '\n';
        ++failures;
    }
}

juce::String request(int port, const juce::String& method, const juce::String& path, const juce::String& token, const juce::String& body)
{
    juce::StreamingSocket socket;
    if (!socket.connect("127.0.0.1", port, 1000))
        return {};

    auto payload = method + " " + path + " HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n";
    if (token.isNotEmpty())
        payload += "X-Ernie-Token: " + token + "\r\n";
    if (body.isNotEmpty())
        payload += "Content-Length: " + juce::String(body.getNumBytesAsUTF8()) + "\r\n";
    payload += "\r\n" + body;

    socket.write(payload.toRawUTF8(), static_cast<int>(payload.getNumBytesAsUTF8()));

    juce::MemoryBlock raw(4096, true);
    int received = 0;
    while (received < static_cast<int>(raw.getSize()) - 1)
    {
        if (socket.waitUntilReady(true, 1000) <= 0)
            break;
        const auto n = socket.read(static_cast<char*>(raw.getData()) + received,
                                    static_cast<int>(raw.getSize()) - received - 1,
                                    false);
        if (n <= 0)
            break;
        received += n;
    }

    socket.close();
    return juce::String::fromUTF8(static_cast<const char*>(raw.getData()), received);
}
}

int main()
{
    juce::ScopedJuceInitialiser_GUI juceInit;

    std::atomic<bool> recording { false };
    std::atomic<int> starts { 0 };
    std::atomic<int> stops { 0 };

    RecordingApi api(
        [&](const RecordingCommand& command)
        {
            if (command.type == RecordingCommand::Type::start)
            {
                recording.store(true);
                starts.fetch_add(1);
            }
            else if (command.type == RecordingCommand::Type::stop)
            {
                recording.store(false);
                stops.fetch_add(1);
            }
            else if (recording.load())
            {
                return juce::Result::fail("Cannot change sample rate or buffer duration while recording");
            }
            return juce::Result::ok();
        },
        [&]()
        {
            RecordingStatus status;
            status.recording = recording.load();
            return status;
        });

    expect(api.start(17841).wasOk(), "api should bind to loopback");
    const auto port = api.getPort();
    expect(port > 0, "bound port should be published");

    const auto denied = request(port, "POST", "/v1/recording/start", {}, "{}");
    expect(denied.startsWith("HTTP/1.1 401"), "start without a handshake must be rejected");
    expect(starts.load() == 0, "rejected start must not arm recording");

    const auto handshake = request(port, "POST", "/v1/handshake", {}, {});
    expect(handshake.startsWith("HTTP/1.1 200"), "handshake should succeed");
    const auto body = handshake.fromFirstOccurrenceOf("\r\n\r\n", false, false);
    const auto token = juce::JSON::parse(body).getProperty("token", {}).toString();
    expect(token.isNotEmpty(), "handshake should return a token");

    const auto started = request(port, "POST", "/v1/recording/start", token,
                                 "{\"capture_sample_rate\":48000,\"buffer_duration_seconds\":4}");
    expect(started.startsWith("HTTP/1.1 200"), "authorised start should succeed");
    expect(starts.load() == 1, "start should reach the command handler");

    const auto status = request(port, "GET", "/v1/status", {}, {});
    expect(status.contains("\"recording\": true") || status.contains("\"recording\":true"), "status should report recording");

    const auto stopped = request(port, "POST", "/v1/recording/stop", token, {});
    expect(stopped.startsWith("HTTP/1.1 200"), "authorised stop should succeed");
    expect(stops.load() == 1, "stop should reach the command handler");

    api.shutdown();
    std::cout << (failures == 0 ? "api tests passed\n" : "api tests failed\n");
    return failures == 0 ? 0 : 1;
}
