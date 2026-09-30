// KLIPANI desktop shell (Electron).
// Dev:      frontend + dev backend run separately (`npm run dev`, uvicorn);
//            `npx electron electron/main.js` attaches to them.
// Packaged:  spawns the bundled backend sidecar + Next.js standalone server.

const { app, BrowserWindow } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const BACKEND_PORT = 8000;
const FRONTEND_PORT = 3000;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}/api/health`;

const children = [];

function isPackaged() {
  return app.isPackaged;
}

function resourcesDir() {
  return isPackaged() ? process.resourcesPath : path.join(__dirname, "..");
}

function backendBinary() {
  const base = isPackaged()
    ? path.join(resourcesDir(), "klipani-backend")
    : path.join(__dirname, "..", "build", "dist", "klipani-backend");
  const exe = path.join(base, process.platform === "win32" ? "klipani-backend.exe" : "klipani-backend");
  return fs.existsSync(exe) ? exe : null;
}

function dataDir() {
  const dir = path.join(app.getPath("userData"), "data");
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function waitForBackend(timeoutMs = 60000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      fetch(BACKEND_URL)
        .then((r) => (r.ok ? resolve(true) : retry()))
        .catch(retry);
    };
    const retry = () => {
      if (Date.now() - started > timeoutMs) reject(new Error("backend timeout"));
      else setTimeout(tick, 800);
    };
    tick();
  });
}

function spawnBackend() {
  const exe = backendBinary();
  if (!exe) {
    console.log("[klipani] no bundled backend found, expecting dev backend on :8000");
    return null;
  }
  const child = spawn(exe, [], {
    env: { ...process.env, KLIPANI_DATA: dataDir() },
    stdio: "inherit",
  });
  children.push(child);
  return child;
}

function frontendDir() {
  if (isPackaged()) return path.join(resourcesDir(), "frontend", ".next", "standalone");
  return path.join(__dirname, "..", "frontend", ".next", "standalone");
}

function spawnFrontend() {
  if (!isPackaged()) return null; // `npm run dev` serves it
  const dir = frontendDir();
  const server = path.join(dir, "server.js");
  if (!fs.existsSync(server)) {
    console.log("[klipani] standalone frontend not found:", server);
    return null;
  }
  const child = spawn(process.execPath, [server], {
    cwd: dir,
    env: { ...process.env, PORT: String(FRONTEND_PORT), HOSTNAME: "127.0.0.1" },
    stdio: "inherit",
  });
  children.push(child);
  return child;
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 900,
    title: "KLIPANI",
    backgroundColor: "#0B0B10",
    webPreferences: { contextIsolation: true },
  });
  const url = isPackaged() || process.env.KLIPANI_FRONT_URL
    ? `http://127.0.0.1:${FRONTEND_PORT}`
    : "http://localhost:3000";
  try {
    await waitForBackend();
  } catch (error) {
    console.error("[klipani]", error.message);
  }
  win.loadURL(url);
}

app.whenReady().then(() => {
  if (isPackaged()) {
    spawnBackend();
    spawnFrontend();
  } else if (process.env.KLIPANI_SPAWN_BACKEND === "1") {
    spawnBackend();
  }
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("quit", () => {
  for (const child of children) {
    try {
      child.kill();
    } catch {
      /* already dead */
    }
  }
});
