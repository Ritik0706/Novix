const { app, BrowserWindow, screen, ipcMain } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const path = require("path");

app.setName('Novix');

let djangoProcess = null;

function getResourceRoot() {
  if (app.isPackaged) {
    return process.resourcesPath;
  }

  return path.join(__dirname, "novix");
}

function getLlamaRoot() {
  const candidates = [
    path.join(getResourceRoot(), "llama"),
    path.join(getResourceRoot(), "resources", "llama"),
    path.join(getResourceRoot(), "backend", "_internal", "resources", "llama"),
  ];

  const existing = candidates.find((candidate) => fs.existsSync(candidate));
  return existing || candidates[0];
}

function getRuntimeDirectory(runtime) {
  return path.join(
    getLlamaRoot(),
    runtime
  );
}

function getBackendEnv() {
  return {
    ...process.env,
    NOVIX_DATA_DIR: app.getPath('userData'),
  };
}

function createErrorWindow(error) {
  const window = new BrowserWindow({
    width: 560,
    height: 360,
    resizable: false,
    webPreferences: { contextIsolation: true },
  });
  const message = String(error?.message || error).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));
  window.loadURL(`data:text/html,<body style="font-family:sans-serif;padding:32px"><h2>Novix setup failed</h2><p>${message}</p><p>Make sure Ollama is installed and running, then try again.</p></body>`);
}

function ensureDatabaseAvailable() {
  const dataDir = app.getPath('userData');
  const databasePath = path.join(dataDir, "db.sqlite3");

  if (fs.existsSync(databasePath)) {
    console.log("Using existing database:", databasePath);
    return;
  }

  const packagedDatabasePath = app.isPackaged
    ? path.join(process.resourcesPath, "database", "db.sqlite3")
    : path.join(__dirname, "..", ".local_data", "db.sqlite3");

  if (!fs.existsSync(packagedDatabasePath)) {
    throw new Error(`Packaged database was not found: ${packagedDatabasePath}`);
  }

  fs.mkdirSync(dataDir, { recursive: true });
  fs.copyFileSync(packagedDatabasePath, databasePath);
  console.log("Copied packaged database to:", databasePath);
}

function waitForDjango(timeoutMs = 120000) {
  return new Promise((resolve, reject) => {
    const startedAt = Date.now();

    const check = () => {
      if (djangoProcess && djangoProcess.exitCode !== null && djangoProcess.exitCode !== undefined) {
        reject(new Error(`Django backend exited before it became ready (exit code ${djangoProcess.exitCode}).\n${djangoStartupLog.trim() || "No backend logs were captured."}`));
        return;
      }

      const request = http.get("http://127.0.0.1:8000", { timeout: 4000 }, (response) => {
        response.resume();
        if (response.statusCode >= 200 && response.statusCode < 400) {
          resolve();
          return;
        }

        if (Date.now() - startedAt >= timeoutMs) {
          reject(new Error(`Django returned HTTP ${response.statusCode}`));
          return;
        }

        setTimeout(check, 500);
      });

      request.on("timeout", () => {
        request.destroy(new Error("HTTP request timed out waiting for backend"));
      });

      request.on("error", () => {
        if (Date.now() - startedAt >= timeoutMs) {
          reject(new Error(`Django backend did not start in time after ${timeoutMs}ms.\n${djangoStartupLog.trim() || "No backend logs were captured."}`));
          return;
        }

        setTimeout(check, 500);
      });
    };

    check();
  });
}

async function startDjango() {
  const projectRoot = path.join(__dirname, "..");

  const developmentBackendDir = path.join(
    projectRoot,
    "dist",
    "novix-backend"
  );

  const developmentBackendPath = path.join(
    developmentBackendDir,
    "novix-backend.exe"
  );

  const packagedBackendDir = path.join(
    process.resourcesPath,
    "backend"
  );

  const packagedBackendPath = path.join(
    packagedBackendDir,
    "novix-backend.exe"
  );

  let backendPath;
  let backendCwd;
  let backendArgs = [];

  // Packaged Electron application
  if (app.isPackaged && fs.existsSync(packagedBackendPath)) {
    backendPath = packagedBackendPath;
    backendCwd = packagedBackendDir;
  }

  // Development Electron application
  else if (fs.existsSync(developmentBackendPath)) {
    backendPath = developmentBackendPath;
    backendCwd = developmentBackendDir;
  }

  // Development fallback
  else {
    const pythonCommand =
      process.platform === "win32" ? "python" : "python3";

    backendPath = pythonCommand;
    backendArgs = ["run_backend.py"];
    backendCwd = projectRoot;
  }

  console.log("Starting backend...");
  console.log("Backend path:", backendPath);
  console.log("Backend cwd:", backendCwd);

  djangoStartupLog = "";
  djangoProcess = spawn(
    backendPath,
    backendArgs,
    {
      cwd: backendCwd,
      env: getBackendEnv(),
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    }
  );

  djangoProcess.stdout.on("data", (data) => {
    const text = data.toString();
    djangoStartupLog += text;
    console.log(`[Django] ${text}`);
  });

  djangoProcess.stderr.on("data", (data) => {
    const text = data.toString();
    djangoStartupLog += text;
    console.error(`[Django] ${text}`);
  });

  djangoProcess.on("error", (error) => {
    console.error("Failed to start backend:", error);
    djangoStartupLog += `Failed to start backend: ${error.stack || error.message}\n`;
    djangoProcess = null;
  });

  djangoProcess.on("close", (code) => {
    console.log(`Django stopped with code ${code}`);
    djangoProcess = null;
  });
}

function createWindow() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;
  const windowWidth = 420;
  const windowHeight = 640;

  const window = new BrowserWindow({
    width: windowWidth,
    height: windowHeight,
    minWidth: 320,
    minHeight: 420,
    x: Math.max(20, width - windowWidth - 20),
    y: Math.max(20, height - windowHeight - 20),
    frame: false,
    transparent: true,
    resizable: true,
    alwaysOnTop: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
    },
  });

  const sendMaximizeState = () => {
    window.webContents.send("window-maximize-state", window.isMaximized());
  };
  window.on("maximize", sendMaximizeState);
  window.on("unmaximize", sendMaximizeState);
  window.webContents.on("did-finish-load", sendMaximizeState);

  window.loadFile(path.join(__dirname, "renderer", "chat.html"));
}

app.whenReady().then(async () => {
  try {
    ensureDatabaseAvailable();
    await startDjango();
    await waitForDjango();
  } catch (error) {
    console.error("Backend startup failed:", error);
    if (djangoProcess) {
      djangoProcess.kill();
      djangoProcess = null;
    }
    createErrorWindow(error);
    return;
  }

  createWindow();
});

ipcMain.on("close-window", (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (win) {
    win.close();
  }
});

ipcMain.on("toggle-maximize-window", (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (!win) return;

  if (win.isMaximized()) {
    win.unmaximize();
  } else {
    win.maximize();
  }
});

app.on("window-all-closed", () => {
  if (djangoProcess) {
    djangoProcess.kill();
    djangoProcess = null;
  }

  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  if (djangoProcess) {
    djangoProcess.kill();
    djangoProcess = null;
  }
});