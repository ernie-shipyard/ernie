const path = require("path");
const { spawn } = require("child_process");
const { app, BrowserWindow, session, systemPreferences } = require("electron");

// Dev: Vite on a fixed port (strictPort in vite.config.js). Prod (packaged, or ERNIE_PROD=1): the built UI served
// by FastAPI at /app, same origin as the API.
const BACKEND = process.env.ERNIE_BACKEND || "http://127.0.0.1:8000";
const PROD = app.isPackaged || process.env.ERNIE_PROD === "1";
const UI_URL = PROD ? `${BACKEND}/app/` : "http://localhost:5173";
const REPO = path.resolve(__dirname, "..", "..");

let backend = null;

async function backendUp() {
  try {
    return (await fetch(`${BACKEND}/health`)).ok;
  } catch {
    return false;
  }
}

// Prod only: start uvicorn from the repo's .venv if nothing is answering yet. In dev, `npm run dev:electron` runs the
// backend as its own process, so a spawn here would outlive Ctrl-C and squat on :8000.
async function ensureBackend() {
  if (!PROD || (await backendUp())) return;
  const port = new URL(BACKEND).port || "8000";
  backend = spawn(path.join(REPO, ".venv", "bin", "uvicorn"), ["app:app", "--port", port], {
    cwd: REPO,
    stdio: "inherit",
  });
  backend.on("error", (error) => console.error("Could not start the backend:", error.message));
  for (let i = 0; i < 60 && !(await backendUp()); i++) await new Promise((r) => setTimeout(r, 500));
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1180,
    height: 780,
    minWidth: 420,
    backgroundColor: "#17120e",
    title: "Ernie",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });
  win.loadURL(UI_URL);
}

app.whenReady().then(async () => {
  // Ernie needs the microphone for the conversation and for hum-to-MIDI; nothing else.
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback, details) => {
    callback(permission === "media" && (details.mediaTypes || []).every((t) => t === "audio"));
  });
  if (process.platform === "darwin") {
    await systemPreferences.askForMediaAccess("microphone");
  }

  await ensureBackend();
  await createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("will-quit", () => {
  backend?.kill();
});
