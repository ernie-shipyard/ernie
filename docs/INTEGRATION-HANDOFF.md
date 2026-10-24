# ERNIE / FOLDBACK — Technical Integration Handoff

**Project:** ERNIE — AI Music Production Collaborator  
**Date:** October 24, 2026  
**Purpose:** Document two independently developed components so an AI coding assistant can merge them into one working application, resolve compatibility issues, preserve functionality, and implement missing integration points.

**Important:** This document is based on development conversations, screenshots, and descriptions of the repositories. It is not a complete source-code audit. The receiving assistant must inspect both codebases before modifying them.

---

# 1. Project Overview

ERNIE is a conversational AI music-production assistant designed to work alongside a Digital Audio Workstation (DAW), initially Ableton Live.

Its goal is to combine:

1. A desktop application with an expressive animated character.
2. Real-time voice conversations.
3. A distinctive Gen-Z music-producer personality.
4. Access to DAW session information.
5. Music-production feedback and mix analysis.
6. MIDI and creative-idea generation.
7. Discovery of installed samples and plugins.

The application should feel like a producer friend who can discuss music, provide actionable suggestions, and respond to the user's current DAW session.

**Target experience:**

The user opens ERNIE as a desktop application, starts a conversation, and asks:

"Ernie, which track needs work?"

ERNIE requests actual track information or mixing analysis from the backend, then responds using its configured personality and voice.

The UI visually reacts while ERNIE speaks.

---

# 2. Component A — React / Electron / ElevenLabs

## 2.1 Ownership and responsibility

Component A is responsible for:

- ERNIE's visual identity.
- React-based user interface.
- Animated character.
- ElevenLabs voice conversations.
- Displaying connection and conversation state.
- Electron desktop application shell.
- Future integration of backend data and commands.

This component was originally developed in a standalone folder called `ernie-personality`.

## 2.2 Technology stack

| Technology | Purpose |
|---|---|
| React | UI components and state |
| Vite | Frontend development/build |
| JavaScript/JSX | Application behavior |
| CSS | Interface styling |
| ElevenLabs React SDK | Real-time conversational agent |
| Electron | Desktop window |
| Node.js/npm | Package management and development |
| Python | Earlier standalone voice/text prototypes |

Package versions observed in a development screenshot include React 19.3.1, Vite 8.3.4, ElevenLabs React SDK 1.19.1, and Electron 45.0.0.

These versions must be verified against the current `package.json` and lockfile.

## 2.3 Observed project structure

```text
ernie-personality/
├── .env
├── .venv-arm/
├── main.py
├── personality.py
├── voice_test.py
└── ernie-ui/
    ├── .env.local
    ├── package.json
    ├── vite.config.js
    ├── index.html
    ├── public/
    │   └── images/
    │       ├── ernie.png
    │       └── ernie_talk.png
    ├── electron/
    │   └── main.cjs
    └── src/
        ├── App.jsx
        ├── App.css
        ├── index.css
        └── main.jsx
```

The image paths reflect the intended configuration and should be verified on disk.

Do not include virtual environments, API keys, or `.env` files in version control.

## 2.4 Implemented or configured features

### React interface

The frontend contains:

- ERNIE branding.
- A character/avatar.
- A greeting and status message.
- A Talk to Ernie button.
- A Stop Conversation button.
- Connection-status display.
- Error display.
- Dark UI styling.
- Orange color theme.

### Custom character assets

Two images are used:

- `ernie.png`: normal, mouth-closed expression.
- `ernie_talk.png`: mouth-open expression.

The desired behavior is:

- When ERNIE is silent, display `ernie.png`.
- When ERNIE speaks, alternate between both images.
- When speech ends, return to `ernie.png`.

The animation is a simple timer-based approximation, not audio waveform-driven lip synchronization.

Example implementation:

```jsx
const { mode } = useConversationMode();

const isSpeaking =
  status === "connected" &&
  mode === "speaking";

const [mouthOpen, setMouthOpen] = useState(false);

useEffect(() => {
  if (!isSpeaking) {
    setMouthOpen(false);
    return;
  }

  const interval = setInterval(() => {
    setMouthOpen(previous => !previous);
  }, 100);

  return () => clearInterval(interval);
}, [isSpeaking]);
```

The last observed animation interval was **100 milliseconds**, corresponding to approximately 10 image changes per second.

The image selected for display is determined by:

```jsx
const image =
  isSpeaking && mouthOpen
    ? "/images/ernie_talk.png"
    : "/images/ernie.png";
```

**Integration requirement:** Preserve this behavior. If the Electron or voice implementation changes, the avatar must still respond to the actual agent-speaking state.

### ElevenLabs agent

An ElevenLabs conversational agent named **Ernie** was created.

Known dashboard configuration at the time of development:

- Voice: Young Jamal.
- Language: English.
- LLM shown: DeepSeek Flash 4.1.
- First message: "Yo what's good".
- Personality prompt: custom music-production collaborator.
- Default agent personality: off.

These are historical configuration observations, not guarantees of the current settings.

The personality includes:

- Chill, laid-back tone.
- Gen-Z-coded language used naturally.
- Playful sarcasm.
- Supportive but honest criticism.
- Music-production expertise.
- Short conversational answers.
- Actionable recommendations.
- No fabricated track measurements or plugin information.

The actual system prompt is configured in ElevenLabs.

`personality.py` may contain a local copy but is not automatically loaded by the React application.

### Voice interface

The React integration uses the `@elevenlabs/react` package.

Relevant APIs used in the prototype:

```jsx
ConversationProvider
useConversationControls
useConversationStatus
useConversationMode
```

The UI starts a voice conversation using an Agent ID from:

```env
VITE_ELEVENLABS_AGENT_ID=...
```

The current prototype expects the agent to support the selected client-side connection method.

A public agent can use an Agent ID for direct connection. A private agent requires a secure backend-mediated authentication flow.

**Never expose an ElevenLabs API key in React/Vite frontend environment variables.**

### Python prototype

A separate `voice_test.py` uses the ElevenLabs Python SDK and PyAudio to perform a standalone microphone/speaker conversation.

This prototype was developed before the React interface.

Its purpose is testing voice functionality independently.

It is not inherently required for the final Electron frontend.

## 2.5 Electron implementation

An Electron folder and entry file were created:

```text
ernie-ui/electron/main.cjs
```

The Electron process creates a desktop window and loads a local Vite development server.

The application was successfully launched in an Electron window.

However, the window initially displayed the **default Vite starter interface** instead of the customized ERNIE interface.

The suspected cause was mismatched development-server ports and multiple simultaneously running Vite instances.

The final resolution was not independently confirmed.

Therefore:

- Electron window launch: observed working.
- Loading the correct ERNIE frontend: not fully verified.
- Electron microphone access: not fully verified.
- Packaged `.app` build: not yet verified.

---

# 3. Component B — Existing Team Foldback / DAW Application

## 3.1 Purpose

Component B is the team's existing music-production assistant backend and interface.

Its repository was shown on GitHub as `ernie`, with the application described as **Foldback**.

The GitHub README describes the application as a studio assistant that can read Ableton session information, provide mixing suggestions, respond to microphone input, and support idea-to-MIDI functionality.

These are repository-described capabilities. The receiving assistant must verify which are actually implemented and functional.

## 3.2 Observed repository structure

```text
ernie/
├── docs/
├── foldback/
├── patches/
├── plugin/
├── scripts/
├── source/
├── static/
├── tests/
├── .env.example
├── .gitignore
├── CMakeLists.txt
├── README.md
├── app.py
└── requirements.txt
```

Additional JSON files related to ERNIE sessions were visible.

The exact current contents must be inspected before integration.

## 3.3 Repository-described capabilities

### Ableton session access

The backend is intended to access:

- Track names.
- BPM/tempo.
- Devices and plugins.
- Clips.
- Sample-file references.
- Other supported session metadata.

The repository documentation references AbletonOSC for Ableton Live connectivity.

The backend must distinguish information directly available from Ableton from information requiring user input or additional analysis.

### Mix analysis

The repository describes rule-based production analysis, including possible:

- Low-frequency masking.
- Kick/bass conflicts.
- Stereo or mono issues.
- Excessive compression.
- Missing upper-frequency content.

Recommendations must distinguish measured evidence from subjective production advice.

### Hum-to-MIDI

The team describes the ability to:

- Record or accept a hummed melody.
- Analyze pitch.
- Quantize notes.
- Generate MIDI.
- Potentially insert the resulting MIDI into Ableton.

The README also references a more ambitious generated-track workflow.

Implementation status must be verified.

### Plugin integration

The repository includes a `plugin/` directory, CMake configuration, and C++ source files.

The screenshots also describe a JUCE-based audio plugin for obtaining track or audio information.

Do not assume the plugin can automatically read arbitrary DAW plugin parameters or inspect an entire Ableton project.

### Existing interface

The repository includes:

```text
app.py
foldback/
static/
```

The existing app reportedly exposes a web interface on:

```text
http://localhost:8000
```

This is a described development configuration and must be checked.

It may already contain microphone or voice-related functionality.

The receiving assistant must determine whether to:

- Preserve the existing UI as a fallback.
- Replace the UI with Component A.
- Reuse the backend routes while retiring redundant frontend code.

---

# 4. Proposed Unified Architecture

The recommended direction is:

```text
                   USER
                     |
                     v
          Electron Desktop App
                     |
                     v
               React UI
           /         |         \
          v          v          v
      Avatar      Voice      DAW Panel
      Animation   Controls    and Tools
                     |
                     v
              ElevenLabs Agent
                     |
                 Tool Calls
                     |
                     v
              Backend API
                 Python
                     |
           +---------+---------+
           |         |         |
           v         v         v
       AbletonOSC  JUCE DSP  MIDI/Assets
           |         |         |
           +---------+---------+
                     |
                     v
                 Ableton Live
```

## Architectural decisions

**Decision 1: Keep React as the primary frontend.**

React owns:

- Character appearance.
- Microphone button.
- Conversation state.
- Error messages.
- Track and session displays.
- User interactions.
- Client-side tool handlers.

**Decision 2: Use Electron for desktop packaging.**

Electron owns:

- Application lifecycle.
- Desktop window.
- Secure OS integrations.
- Appropriate microphone permissions.
- Local backend lifecycle, if needed.

Electron should not independently implement audio DSP or duplicate the existing backend logic.

**Decision 3: Retain the team's Python backend for DAW functions.**

Python handles:

- Ableton communication.
- Session data.
- Mixing analysis.
- MIDI generation.
- Asset discovery.
- Any secure ElevenLabs authentication endpoint.

**Decision 4: Retain ElevenLabs as the conversational agent.**

ElevenLabs handles:

- Real-time speech interaction.
- Conversation reasoning.
- Ernie's personality.
- Voice output.
- Calling properly registered application/backend tools.

**Decision 5: Preserve one source of truth for data.**

Do not maintain separate, contradictory track registries or analysis results in React and Python.

React should consume canonical backend responses.

**Decision 6: Avoid duplicate voice pipelines.**

The existing Python voice prototype and React/ElevenLabs voice system have overlapping functionality.

The final architecture should use one primary user-facing conversation system unless there is a clear reason to retain both.

---

# 5. Known Integration Conflicts

## Conflict A: React and existing Python UI

Component A contains a React interface.

Component B contains an existing Python-backed web interface.

**Resolution:**

Prefer React as the main desktop interface.

Reuse backend features rather than rewriting their functionality.

Retain the original web UI during migration if doing so reduces integration risk.

## Conflict B: Multiple voice implementations

Component A contains:

- ElevenLabs React SDK integration.
- Standalone Python ElevenLabs voice prototype.

Component B may contain:

- Existing ElevenLabs or other TTS functionality.
- Microphone/audio processing routes.

**Resolution:**

Audit all existing voice paths.

Select one primary conversation flow.

Avoid two microphones capturing simultaneously or duplicate TTS responses.

Preserve independently useful audio-processing functions.

## Conflict C: Frontend voice connection errors

Component A previously displayed:

```text
Status: disconnected
Status: error
```

The exact cause was not conclusively established.

Potential causes include:

- Incorrect Agent ID.
- Missing `.env.local` values.
- Agent authentication requirements.
- Microphone permissions.
- Incompatible SDK usage.
- WebSocket/network failures.

**Resolution:**

Inspect the installed ElevenLabs React SDK API.

Log the actual error.

Verify configuration and credentials without exposing secrets.

Test browser voice first, followed by Electron voice.

## Conflict D: Vite development ports

Multiple local Vite servers were observed:

```text
5173
5174
5175
5176
5177
```

Electron initially loaded the wrong frontend.

**Resolution:**

Configure one fixed development port:

```js
server: {
  port: 5173,
  strictPort: true
}
```

Ensure Electron targets that exact port.

Do not assume an old server instance is running the current source code.

## Conflict E: Electron development versus packaging

The current Electron prototype loads:

```text
http://localhost:5173
```

A packaged application cannot depend on a manually running Vite dev server.

**Resolution:**

Support two modes:

Development:
- Launch Vite.
- Electron loads the development URL.

Production:
- Build React.
- Electron loads the compiled frontend.
- Package required static assets.
- Supply the backend through an explicit production strategy.

## Conflict F: Frontend secrets

The React application uses an Agent ID.

The Python scripts use a secret ElevenLabs API key.

**Resolution:**

Never embed secret credentials in the React bundle.

Store secrets in a secure server-side or desktop-main-process mechanism.

For private ElevenLabs agents, use an authenticated endpoint that issues short-lived conversation credentials.

## Conflict G: Asset paths

Component A uses:

```text
/images/ernie.png
/images/ernie_talk.png
```

These work in Vite development when the files are correctly located under `public/images`.

Electron packaging may use different file-loading paths.

**Resolution:**

Verify asset loading in both development and production builds.

Use a consistent Vite base configuration and correct resource resolution.

## Conflict H: Python environments

Development involved:

- A Conda base environment.
- An Intel x86_64 Python installation.
- Apple Silicon arm64 Homebrew PortAudio.
- A replacement arm64 virtual environment.

This initially caused PyAudio import errors.

**Resolution:**

Do not bundle personal Python virtual environments.

Document supported Python versions and architectures.

Use reproducible dependency installation.

Prefer eliminating PyAudio from the final React/Electron frontend if the browser/Electron audio stack handles voice input directly.

---

# 6. Required User-Facing Features

## 6.1 Character animation

Default asset:

```text
ernie.png
```

Talking asset:

```text
ernie_talk.png
```

Required behavior:

- Idle: mouth closed.
- User speaking: mouth closed unless a separate listening animation is later introduced.
- Ernie speaking: alternate images.
- Ernie finished: mouth closed.
- Disconnected/error: mouth closed.

Retain an adjustable animation interval.

## 6.2 Appearance

Current design direction:

- Dark background.
- Orange accent color.
- Custom character assets.
- Minimal interface.
- Casual producer-friend branding.

Orange styling was introduced to replace an earlier purple theme.

Preserve the latest chosen theme.

## 6.3 Voice controls

Required:

- Start Conversation.
- Stop Conversation.
- Connection status.
- Visible connection errors.
- Microphone permission handling.
- Agent speaking-state detection.
- Avoid overlapping conversations.

## 6.4 Music production tools

Planned integration features:

- Read Ableton session metadata.
- Display track names.
- Access actual mixing analysis.
- Ask Ernie questions about specific tracks.
- Generate or expose MIDI outputs.
- Search installed samples and plugins.

Do not display hardcoded example measurements as if they were live.

---

# 7. Backend Contracts to Verify

The initial project plan proposed the following routes:

| Route | Purpose |
|---|---|
| `GET /health` | Backend health |
| `GET /tracks` | Track information |
| `POST /mix/analyze` | Mixing recommendations |
| `POST /idea/transcribe` | Audio-to-MIDI conversion |
| `POST /assets/scan` | Approved directory scan |
| `GET /assets/search` | Plugin/sample search |

**These are proposed contracts, not confirmed routes in the current Foldback repository.**

Before writing integration code:

1. Inspect actual FastAPI routes.
2. Document request/response schemas.
3. Identify authentication requirements.
4. Reuse existing endpoints where possible.
5. Adapt the React API client to the real backend.
6. Add only genuinely missing endpoints.

A centralized React API client is preferred over hardcoding backend URLs throughout components.

---

# 8. Feature Status Matrix

| Feature | Current evidence | Integration status |
|---|---|---|
| Ernie persona prompt | Created in ElevenLabs | Configured |
| ElevenLabs voice selection | Young Jamal selected | Configured |
| Standalone Python voice | Prototype developed | Requires final verification |
| React frontend | Rendered successfully | Working UI |
| Orange visual theme | CSS developed | Needs latest-code verification |
| Custom character images | Implementation supplied | Verify actual assets |
| Talking-mouth animation | React code implemented | Needs connected-agent test |
| React ElevenLabs voice | Connection errors observed | Unresolved |
| Electron desktop window | Successfully launched | Working shell |
| Electron displaying latest UI | Initially displayed Vite starter | Unresolved |
| Production `.app` packaging | No completed build observed | Not implemented |
| DAW metadata backend | Existing repository describes support | Verify |
| JUCE audio capture | Source/repository structure exists | Verify |
| Mix analysis | Repository-described feature | Verify |
| Hum-to-MIDI | Repository-described feature | Verify |
| Agent-to-DAW tool calling | Planned architecture | Not verified |
| Automated mood changes | Proposed | Not implemented |

Do not mark a feature complete solely because code exists. Verify its actual behavior.

---

# 9. Suggested Repository Structure After Integration

The receiving assistant should inspect the current repository and adapt this layout rather than move files unnecessarily.

```text
ernie/
├── frontend/
│   ├── electron/
│   │   └── main.cjs
│   ├── public/
│   │   └── images/
│   │       ├── ernie.png
│   │       └── ernie_talk.png
│   ├── src/
│   │   ├── App.jsx
│   │   ├── App.css
│   │   ├── components/
│   │   ├── hooks/
│   │   └── services/
│   ├── package.json
│   └── vite.config.js
├── foldback/
├── plugin/
├── source/
├── scripts/
├── tests/
├── docs/
│   └── integration-handoff.md
├── app.py
├── requirements.txt
└── README.md
```

This is a proposed structure, not the current verified filesystem layout.

The React project may also remain under `ernie-ui/` if that minimizes changes.

---

# 10. Integration Sequence

## Phase 1 — Repository audit

- Read both READMEs.
- Inspect package files.
- Inspect Python routes.
- Inspect React components.
- Inspect Electron configuration.
- Inspect ElevenLabs integration.
- Identify duplicate responsibilities.
- Confirm which features actually work.

Deliverable: architecture map and conflict list.

## Phase 2 — Stabilize frontend

- Ensure the intended React app loads.
- Fix Electron's incorrect Vite target.
- Verify character images.
- Verify orange theme.
- Verify mouth animation independently using mock speaking state.

Deliverable: reliable desktop UI.

## Phase 3 — Fix voice

- Validate Agent ID and access policy.
- Test the conversation in the browser.
- Resolve React SDK errors.
- Confirm microphone and audio output.
- Confirm speaking-state detection.
- Repeat in Electron.

Deliverable: fully functioning voice conversation in the desktop app.

## Phase 4 — Connect backend

- Connect existing backend health check.
- Display real session/track information.
- Request backend mixing analysis.
- Render results in React.
- Pass relevant data to Ernie using supported agent tools.

Deliverable: voice assistant with grounded DAW context.

## Phase 5 — Integrate advanced features

- MIDI transcription/export.
- Sample/plugin discovery.
- Track-specific recommendations.
- Appropriate DAW actions.

Deliverable: end-to-end music-production workflows.

## Phase 6 — Package and test

- Build React production assets.
- Configure Electron production loading.
- Handle microphone permissions.
- Configure backend startup.
- Test on a clean environment.
- Package the desktop application.

Deliverable: runnable distributable application.

---

# 11. Acceptance Tests

The integration is complete only when the following tests pass.

### UI

- The correct Ernie app opens in Electron.
- The orange theme displays properly.
- Both PNG assets load.
- Character remains idle when silent.
- Character animates when Ernie speaks.
- Animation stops when speech ends.

### Voice

- User can start a session.
- User can speak through the microphone.
- Ernie responds using the configured voice.
- Custom personality is preserved.
- Errors are surfaced clearly.
- Stop Conversation ends the session.

### Backend

- Backend health check succeeds.
- Real Ableton track information can be retrieved.
- The UI displays real track values.
- Ernie can obtain actual backend analysis.
- Ernie does not invent measurements when data is unavailable.

### Application

- Electron loads the correct UI.
- Development and production paths work.
- No secret credentials are exposed.
- The application can be launched reproducibly.
- Existing backend tests continue passing.

---

# 12. Instructions for the Receiving AI Coding Assistant

You are being asked to merge two independently developed parts of an AI music-production application.

**Component A:** React/Electron frontend, animated Ernie character, ElevenLabs conversational voice, and standalone Python prototypes.

**Component B:** Existing Foldback backend, DAW integration, mix-analysis features, MIDI tools, and existing web interface.

Your responsibilities are to:

1. Inspect both codebases completely before changing their architecture.
2. Compare claimed features with actual implemented functionality.
3. Preserve functionality that already works.
4. Identify duplicated implementations and select the better-supported solution.
5. Resolve conflicting environment variables, ports, dependencies, and interfaces.
6. Connect the React frontend to existing backend routes.
7. Connect ElevenLabs tool calls to verified backend functions.
8. Fix frontend ElevenLabs authentication and microphone handling.
9. Make Electron load the correct frontend in development and production.
10. Preserve custom PNG character animation and orange styling.
11. Write tests for important integration points.
12. Implement missing functionality only when it is required and feasible.
13. Update documentation with the actual final architecture.

## Critical constraints

- Do not overwrite functioning DAW logic simply to simplify the frontend.
- Do not expose API keys in browser bundles.
- Do not fabricate DAW telemetry or analysis.
- Do not assume proposed API routes already exist.
- Do not assume the Electron wrapper alone provides DAW integration.
- Do not create separate competing voice pipelines unnecessarily.
- Do not remove features without documenting why.
- Do not silently replace working dependencies or configurations.
- Do not claim successful integration without executing tests.

## Expected output from your work

First produce:

1. An inventory of both repositories.
2. A list of conflicts.
3. A list of missing integrations.
4. A proposed merged architecture.
5. An implementation order prioritized by impact and risk.

Then implement changes incrementally.

After implementation, provide:

1. Exact files modified.
2. Features preserved.
3. Features added.
4. Conflicts resolved.
5. Tests executed and results.
6. Remaining limitations.
7. Commands to launch and build the final application.

**Success criterion:** One coherent Ernie desktop application with a working animated React interface, ElevenLabs voice conversation, and reliable access to the team's actual music-production backend.

The result must be demonstrated through working software, not merely an architectural proposal.