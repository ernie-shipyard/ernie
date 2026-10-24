#include "RecordingApi.h"

namespace
{
juce::String httpResponse(int status, const juce::String& reason, const juce::String& body)
{
    return "HTTP/1.1 " + juce::String(status) + " " + reason + "\r\n"
           "Content-Type: application/json\r\n"
           "Content-Length: " + juce::String(body.getNumBytesAsUTF8()) + "\r\n"
           "Connection: close\r\n"
           "\r\n"
           + body;
}

juce::String jsonError(const juce::String& message)
{
    auto object = std::make_unique<juce::DynamicObject>();
    object->setProperty("ok", false);
    object->setProperty("error", message);
    return juce::JSON::toString(juce::var(object.release()), false);
}

juce::String jsonOk(juce::DynamicObject* object)
{
    object->setProperty("ok", true);
    return juce::JSON::toString(juce::var(object), false);
}

bool isLoopbackPeer(const juce::StreamingSocket& socket)
{
    return socket.isLocal();
}
}

RecordingApi::RecordingApi(CommandHandler onCommand, StatusProvider onStatus)
    : juce::Thread("Ernie Recording API"),
      commandHandler(std::move(onCommand)),
      statusProvider(std::move(onStatus))
{
}

RecordingApi::~RecordingApi()
{
    shutdown();
}

juce::Result RecordingApi::start(int preferredPort)
{
    if (isThreadRunning())
        return juce::Result::ok();

    listener = std::make_unique<juce::StreamingSocket>();

    // JUCE rejects port 0, so fall back by scanning a loopback range.
    auto port = preferredPort > 0 ? preferredPort : 17831;
    auto bound = listener->createListener(port, "127.0.0.1");

    for (auto candidate = 17831; !bound && candidate < 17831 + 32; ++candidate)
    {
        if (candidate == preferredPort)
            continue;

        listener = std::make_unique<juce::StreamingSocket>();
        port = candidate;
        bound = listener->createListener(port, "127.0.0.1");
    }

    if (!bound)
        return juce::Result::fail("Could not bind the recording API to 127.0.0.1");

    boundPort.store(listener->getBoundPort() > 0 ? listener->getBoundPort() : port);
    running.store(true);
    startThread();
    return juce::Result::ok();
}

void RecordingApi::shutdown()
{
    running.store(false);

    if (listener != nullptr)
        listener->close();

    stopThread(2000);
    listener.reset();
    boundPort.store(0);

    const juce::ScopedLock lock(tokenLock);
    token.clear();
}

juce::String RecordingApi::getToken() const
{
    const juce::ScopedLock lock(tokenLock);
    return token;
}

bool RecordingApi::tokenMatches(const juce::String& presented) const
{
    const juce::ScopedLock lock(tokenLock);
    return token.isNotEmpty() && presented == token;
}

void RecordingApi::run()
{
    while (!threadShouldExit() && running.load())
    {
        if (listener == nullptr)
            break;

        auto ready = listener->waitUntilReady(true, 200);
        if (ready <= 0)
            continue;

        std::unique_ptr<juce::StreamingSocket> client(listener->waitForNextConnection());
        if (client != nullptr)
            serveClient(std::move(client));
    }
}

void RecordingApi::serveClient(std::unique_ptr<juce::StreamingSocket> client)
{
    if (!isLoopbackPeer(*client))
    {
        const auto body = jsonError("recording API accepts loopback clients only");
        const auto response = httpResponse(403, "Forbidden", body);
        client->write(response.toRawUTF8(), static_cast<int>(response.getNumBytesAsUTF8()));
        client->close();
        return;
    }

    juce::MemoryBlock raw;
    raw.ensureSize(8192, false);
    int received = 0;

    while (received < static_cast<int>(raw.getSize()) - 1)
    {
        if (client->waitUntilReady(true, 500) <= 0)
            break;

        const auto n = client->read(static_cast<char*>(raw.getData()) + received,
                                    static_cast<int>(raw.getSize()) - 1 - received,
                                    false);
        if (n <= 0)
            break;

        received += n;
        if (juce::String::fromUTF8(static_cast<const char*>(raw.getData()), received).contains("\r\n\r\n"))
            break;
    }

    const auto request = juce::String::fromUTF8(static_cast<const char*>(raw.getData()), received);
    const auto headerEnd = request.indexOf("\r\n\r\n");
    const auto header = headerEnd >= 0 ? request.substring(0, headerEnd) : request;
    const auto body = headerEnd >= 0 ? request.substring(headerEnd + 4) : juce::String();

    const auto requestLine = header.upToFirstOccurrenceOf("\r\n", false, false);
    const auto method = requestLine.upToFirstOccurrenceOf(" ", false, false);
    const auto rest = requestLine.fromFirstOccurrenceOf(" ", false, false);
    const auto target = rest.upToFirstOccurrenceOf(" ", false, false);

    juce::String tokenHeader;
    for (auto line : juce::StringArray::fromLines(header))
    {
        if (line.startsWithIgnoreCase("X-Ernie-Token:"))
            tokenHeader = line.fromFirstOccurrenceOf(":", false, false).trim();
    }

    const auto responseBody = handleRequest(method, target, body, tokenHeader);
    auto parsed = juce::JSON::parse(responseBody);
    const auto failed = parsed.getDynamicObject() != nullptr
                        && parsed.getDynamicObject()->hasProperty("ok")
                        && !static_cast<bool>(parsed.getDynamicObject()->getProperty("ok"));
    const auto reason = responseBody.contains("handshake required") ? "Unauthorized" : "Bad Request";
    const auto code = responseBody.contains("handshake required") ? 401 : 400;
    const auto status = failed ? httpResponse(code, reason, responseBody)
                               : httpResponse(200, "OK", responseBody);

    client->write(status.toRawUTF8(), static_cast<int>(status.getNumBytesAsUTF8()));
    client->close();
}

juce::String RecordingApi::handleRequest(const juce::String& method,
                                         const juce::String& target,
                                         const juce::String& body,
                                         const juce::String& tokenHeader)
{
    const auto path = target.upToFirstOccurrenceOf("?", false, false);

    if (method == "POST" && path == "/v1/handshake")
    {
        auto fresh = juce::Uuid().toDashedString();
        {
            const juce::ScopedLock lock(tokenLock);
            token = fresh;
        }

        auto object = std::make_unique<juce::DynamicObject>();
        object->setProperty("token", fresh);
        object->setProperty("port", boundPort.load());
        object->setProperty("bind", "127.0.0.1");
        return jsonOk(object.release());
    }

    if (method == "GET" && path == "/v1/status")
    {
        const auto status = statusProvider != nullptr ? statusProvider() : RecordingStatus {};
        auto object = std::make_unique<juce::DynamicObject>();
        object->setProperty("recording", status.recording);
        object->setProperty("match_host_sample_rate", status.matchHostSampleRate);
        object->setProperty("capture_sample_rate", status.captureSampleRate);
        object->setProperty("buffer_duration_seconds", status.bufferDurationSeconds);
        object->setProperty("host_sample_rate", status.hostSampleRate);
        object->setProperty("samples_written", status.samplesWritten);
        object->setProperty("ram_bytes", status.ramBytes);
        object->setProperty("api_running", running.load());
        object->setProperty("port", boundPort.load());
        object->setProperty("handshake_ready", getToken().isNotEmpty());
        object->setProperty("session_id", status.sessionId);
        object->setProperty("last_error", status.lastError);
        return jsonOk(object.release());
    }

    const auto control = (method == "POST")
                         && (path == "/v1/recording/start"
                             || path == "/v1/recording/stop"
                             || path == "/v1/config"
                             || path == "/v1/source-file");

    if (!control)
        return jsonError("unknown route");

    if (!tokenMatches(tokenHeader))
        return jsonError("handshake required");

    RecordingCommand command;

    if (path == "/v1/recording/stop")
    {
        command.type = RecordingCommand::Type::stop;
    }
    else
    {
        auto parsed = juce::JSON::parse(body);
        auto* object = parsed.getDynamicObject();

        if (path == "/v1/recording/start")
        {
            command.type = RecordingCommand::Type::start;
        }
        else if (path == "/v1/source-file")
        {
            command.type = RecordingCommand::Type::setSourceFile;
            if (object == nullptr || !object->hasProperty("path"))
                return jsonError("source-file body must include path");
            command.sourceAudioFile = object->getProperty("path").toString();
        }
        else
        {
            command.type = RecordingCommand::Type::updateSettings;
            if (object == nullptr)
                return jsonError("config body must be a JSON object");
        }

        if (object != nullptr)
        {
            if (object->hasProperty("match_host_sample_rate"))
                command.matchHostSampleRate = static_cast<bool>(object->getProperty("match_host_sample_rate"));

            if (object->hasProperty("capture_sample_rate"))
            {
                command.hasSampleRate = true;
                command.captureSampleRate = static_cast<double>(object->getProperty("capture_sample_rate"));
            }

            if (object->hasProperty("buffer_duration_seconds"))
            {
                command.hasDuration = true;
                command.bufferDurationSeconds = static_cast<double>(object->getProperty("buffer_duration_seconds"));
            }
        }
    }

    if (commandHandler == nullptr)
        return jsonError("command handler is not installed");

    const auto result = commandHandler(command);
    if (result.failed())
        return jsonError(result.getErrorMessage());

    auto object = std::make_unique<juce::DynamicObject>();
    object->setProperty("accepted", true);
    return jsonOk(object.release());
}
