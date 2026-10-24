// One client for the FastAPI backend. URLs are relative: Vite proxies them in dev, FastAPI serves the build.

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const text = await response.text();
  let body;
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { detail: text };
  }
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    throw new Error(detail || `${path} failed (${response.status})`);
  }
  return body;
}

const json = (path, data) =>
  request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data ?? {}) });

const form = (path, fields) => {
  const fd = new FormData();
  for (const [k, v] of Object.entries(fields)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) fd.append(k, v[0], v[1]);   // [blob, filename]
    else fd.append(k, v);
  }
  return request(path, { method: "POST", body: fd });
};

export const api = {
  status: () => request("/agent/status"),
  signedUrl: () => request("/agent/signed-url"),
  session: () => request("/tools/session_context"),

  listen: (track) => json("/agent/listen", { track: track || "master" }),
  listenStatus: (captureId) => request(`/agent/listen/${captureId}`),
  cancelListen: (captureId) => json(`/agent/listen/${captureId}/cancel`),

  oneshot: (prompt) => form("/generate/oneshot", { prompt, duration_s: 1.5 }),
  loop: (prompt) => form("/compose", { prompt, length_ms: 8000 }),
  pickSamples: ({ tag, key, bpm }) =>
    request(`/samples/pick?${new URLSearchParams({ tag, key: key || "", ...(bpm ? { bpm } : {}) })}`),
  sampleUrl: (path) => `/sample/file?${new URLSearchParams({ path })}`,

  humToMidi: ({ blob, bpm, bars, countIn, trackName }) =>
    form("/midi/hum", {
      audio: [blob, "hum.webm"], bpm, bars, count_in_s: countIn.toFixed(3), track_name: trackName,
    }),
};
