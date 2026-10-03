const { app, BrowserWindow, screen, ipcMain } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const path = require("path");

app.setName('Novix');

let djangoProcess = null;

function getBackendEnv() {
  return {
    ...process.env,
    NOVIX_DATA_DIR: app.getPath('userData'),
    NOVIX_OLLAMA_EXECUTABLE: process.env.NOVIX_OLLAMA_EXECUTABLE,
    NOVIX_OLLAMA_HOME: process.env.NOVIX_OLLAMA_HOME,
    OLLAMA_MODELS: process.env.OLLAMA_MODELS,
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

function waitForDjango(timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const startedAt = Date.now();

    const check = () => {
      const request = http.get("http://127.0.0.1:8000", (response) => {
        response.resume();
        // Any HTTP response means the backend is up and answering requests.
        resolve();
      });

      request.on("error", () => {
        if (Date.now() - startedAt >= timeoutMs) {
          reject(new Error("Django backend did not start in time"));
          return;
        }

        setTimeout(check, 500);
      });
    };

    check();
  });
}

async function startDjango() {
  ensureDatabaseAvailable();

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

  const fallbackScript = path.join(
    projectRoot,
    "run_backend.py"
  );

  let backendPath;
  let backendCwd;
  let backendArgs = [];
  let useExecutable = false;

  // Packaged Electron application
  if (app.isPackaged && fs.existsSync(packagedBackendPath)) {
    backendPath = packagedBackendPath;
    backendCwd = packagedBackendDir;
    useExecutable = true;
  }

  // Development Electron application
  else if (fs.existsSync(developmentBackendPath)) {
    backendPath = developmentBackendPath;
    backendCwd = developmentBackendDir;
    useExecutable = true;
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
    console.log(`[Django] ${data.toString()}`);
  });

  djangoProcess.stderr.on("data", (data) => {
    console.error(`[Django] ${data.toString()}`);
  });

  djangoProcess.on("error", (error) => {
    console.error("Failed to start backend:", error);
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

  window.loadFile(path.join(__dirname, "renderer", "chat.html"));
}

app.whenReady().then(async () => {
  try {
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