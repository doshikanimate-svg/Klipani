// KLIPANI desktop shell (Electron).
// Dev:      frontend + dev backend run separately (`npm run dev`, uvicorn);
//            `npx electron electron/main.js` attaches to them.
// Packaged:  spawns the bundled backend sidecar + Next.js standalone server.

const { app, BrowserWindow, dialog } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

let autoUpdater = null;
try {
  ({ autoUpdater } = require("electron-updater"));
} catch {
  /* dev checkout without the dep: updates simply stay off */
}

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
  return waitForPort(BACKEND_URL, timeoutMs);
}

function waitForPort(url, timeoutMs = 60000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      fetch(url)
        .then((r) => (r.ok ? resolve(true) : retry()))
        .catch(retry);
    };
    const retry = () => {
      if (Date.now() - started > timeoutMs) reject(new Error(`timeout waiting for ${url}`));
      else setTimeout(tick, 800);
    };
    tick();
  });
}

function logFile(name) {
  try {
    const dir = path.join(dataDir(), "logs");
    fs.mkdirSync(dir, { recursive: true });
    return path.join(dir, name);
  } catch {
    return null;
  }
}

function pipeToFile(child, file, tag) {
  const lines = [];
  const flush = () => {
    if (!file) return;
    try {
      fs.appendFileSync(file, lines.join("") + `\n===== ${tag} ended =====\n`);
    } catch {
      /* best effort */
    }
    lines.length = 0;
  };
  const note = (text) => {
    const line = `[main] ${text}\n`;
    lines.push(line);
    console.log(`[klipani] ${text}`);
  };
  if (file) {
    try {
      fs.appendFileSync(file, `\n===== ${new Date().toISOString()} ${tag} =====\n`);
    } catch {
      /* best effort */
    }
  }
  if (child.stdout) child.stdout.on("data", (d) => lines.push(d.toString()));
  if (child.stderr) child.stderr.on("data", (d) => lines.push(d.toString()));
  child.on("error", (error) => note(`process error: ${error.message}`));
  child.on("exit", (code, signal) => {
    note(`process exit: code=${code} signal=${signal}`);
    flush();
  });
  child.on("spawn", () => note("process spawned"));
  return { note, flush };
}

function spawnBackend() {
  const exe = backendBinary();
  if (!exe) {
    console.log("[klipani] no bundled backend found, expecting dev backend on :8000");
    return null;
  }
  // stdio:pipe (not inherit) so the app never flashes a console window —
  // output goes to backend.log instead.
  const child = spawn(exe, [], {
    env: { ...process.env, KLIPANI_DATA: dataDir() },
    stdio: "pipe",
    windowsHide: true,
  });
  pipeToFile(child, logFile("backend.log"), "backend");
  children.push(child);
  return child;
}

function frontendCandidates() {
  if (!isPackaged()) return [path.join(__dirname, "..", "frontend", ".next", "standalone")];
  const asarBase = app.getAppPath(); // .../resources/app.asar when packaged
  return [
    // Real files (builder.json asarUnpack) — preferred, no asar quirks.
    path.join(path.dirname(asarBase), "app.asar.unpacked", "frontend", ".next", "standalone"),
    // Inside the archive — utilityProcess reads asar fine.
    path.join(asarBase, "frontend", ".next", "standalone"),
  ];
}

function spawnFrontend() {
  if (!isPackaged()) return null; // `npm run dev` serves it
  const log = logFile("frontend.log");
  const say = (text) => {
    try {
      if (log) fs.appendFileSync(log, `[main] ${text}\n`);
    } catch {
      /* best effort */
    }
    console.log(`[klipani] ${text}`);
  };
  let dir = null;
  for (const candidate of frontendCandidates()) {
    const found = fs.existsSync(path.join(candidate, "server.js"));
    say(`candidate ${candidate} -> server.js ${found ? "FOUND" : "missing"}`);
    if (found && !dir) dir = candidate;
  }
  if (!dir) {
    say("server.js not found anywhere, aborting frontend start");
    return null;
  }
  const server = path.join(dir, "server.js");
  say(`spawning ${server} (cwd=${dir})`);
  // NOTE: never spawn process.execPath (Electron) with server.js — Electron
  // would load it as an app instead of running Node. utilityProcess runs it
  // as plain Node with asar support, on every platform.
  const { utilityProcess } = require("electron");
  let child;
  try {
    child = utilityProcess.fork(server, [], {
      cwd: dir,
      env: { ...process.env, PORT: String(FRONTEND_PORT), HOSTNAME: "127.0.0.1", HOST: "127.0.0.1" },
      stdio: "pipe",
    });
  } catch (error) {
    say(`fork threw: ${error.message}`);
    return null;
  }
  pipeToFile(child, log, "frontend");
  children.push(child);
  return child;
}

function errorPage(title, details) {
  const safe = String(details).replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));
  return `data:text/html,<html><body style="background:#0B0B10;color:#e4e4e7;font-family:sans-serif;padding:40px"><h2>${title}</h2><pre style="color:#a1a1aa">${safe}</pre></body></html>`;
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 900,
    title: "KLIPANI",
    backgroundColor: "#0B0B10",
    autoHideMenuBar: true,
    webPreferences: { contextIsolation: true },
  });
  const url = isPackaged() || process.env.KLIPANI_FRONT_URL
    ? `http://127.0.0.1:${FRONTEND_PORT}`
    : "http://localhost:3000";
  if (isPackaged()) {
    try {
      await waitForBackend();
    } catch (error) {
      console.error("[klipani]", error.message);
      win.loadURL(errorPage("Бэкенд не запустился", `${error.message}\n\nЛоги: ${logFile("backend.log") || "(нет)"}`));
      return;
    }
    try {
      await waitForPort(`http://127.0.0.1:${FRONTEND_PORT}`, 45000);
    } catch (error) {
      console.error("[klipani]", error.message);
      dialog.showErrorBox(
        "KLIPANI не запустился",
        `Интерфейс не отвечает. Бэкенд жив — проверьте http://127.0.0.1:8000/api/health в браузере.\n\nЛог интерфейса: ${logFile("frontend.log") || "(нет)"}`
      );
      win.loadURL(errorPage("Интерфейс не запустился", `${error.message}\n\nЛог: ${logFile("frontend.log") || "(нет)"}`));
      return;
    }
  } else {
    try {
      await waitForBackend();
    } catch (error) {
      console.error("[klipani]", error.message);
    }
  }
  win.loadURL(url);
}

app.whenReady().then(() => {
  if (isPackaged()) {
    spawnBackend();
    spawnFrontend();
    checkForUpdates();
  } else if (process.env.KLIPANI_SPAWN_BACKEND === "1") {
    spawnBackend();
  }
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

function checkForUpdates() {
  if (!autoUpdater) return;
  autoUpdater.autoDownload = false;
  autoUpdater.on("update-available", (info) => {
    const win = BrowserWindow.getAllWindows()[0];
    const answer = dialog.showMessageBoxSync(win || undefined, {
      type: "question",
      buttons: ["Скачать", "Позже"],
      defaultId: 0,
      title: "Доступно обновление",
      message: `Вышла версия ${info.version}. Скачать и установить при выходе?`,
    });
    if (answer === 0) autoUpdater.downloadUpdate();
  });
  autoUpdater.on("update-downloaded", () => {
    const answer = dialog.showMessageBoxSync(BrowserWindow.getAllWindows()[0], {
      type: "question",
      buttons: ["Перезапустить", "Позже"],
      defaultId: 0,
      title: "Обновление готово",
      message: "Новая версия скачана. Перезапустить KLIPANI для установки?",
    });
    if (answer === 0) {
      for (const child of children) {
        try {
          child.kill();
        } catch {
          /* already dead */
        }
      }
      autoUpdater.quitAndInstall();
    }
  });
  autoUpdater.on("error", (error) => {
    console.log("[klipani] updater:", error == null ? "unknown" : error.message || error);
  });
  autoUpdater.checkForUpdates().catch((error) => {
    console.log("[klipani] update check failed:", error == null ? "unknown" : error.message || error);
  });
}

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
