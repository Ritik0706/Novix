const { app, BrowserWindow, screen, ipcMain } = require("electron");
const { spawn } = require("child_process");
const { execFileSync } = require("child_process");
const https = require("https");
const fs = require("fs");
const http = require("http");
const path = require("path");

app.setName('Novix');

let djangoProcess = null;
let djangoStartupLog = "";
const MODEL_URL = process.env.NOVIX_MODEL_URL ||
  "https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_0.gguf";

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

function getFreeDiskSpace(directory) {
  const driveLetter = path.parse(directory).root[0];
  return Number(execFileSync("powershell.exe", [
    "-NoProfile",
    "-Command",
    `(Get-PSDrive -Name '${driveLetter}').Free`,
  ], { encoding: "utf8" }).trim());
}

function getModelName(url) {
  return decodeURIComponent(new URL(url).pathname.split("/").pop() || "AI model");
}

function requestBackendJson(pathname, method = "GET") {
  return new Promise((resolve, reject) => {
    const request = http.request(
      `http://127.0.0.1:8000${pathname}`,
      { method, headers: { "Content-Type": "application/json" } },
      (response) => {
        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => { body += chunk; });
        response.on("end", () => {
          let data;
          try {
            data = JSON.parse(body || "{}");
          } catch (error) {
            reject(new Error(`Backend returned invalid JSON: ${error.message}`));
            return;
          }
          if (response.statusCode < 200 || response.statusCode >= 300) {
            reject(new Error(data.error || `Backend returned HTTP ${response.statusCode}`));
            return;
          }
          resolve(data);
        });
      }
    );
    request.on("error", reject);
    request.end();
  });
}

function createSetupWindow(modelName, destination) {
  const window = new BrowserWindow({
    width: 560,
    height: 330,
    resizable: false,
    webPreferences: { contextIsolation: true },
  });
  const escapedModel = JSON.stringify(modelName);
  const escapedDestination = JSON.stringify(destination);
  window.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`
    <body style="font-family:Segoe UI,sans-serif;padding:28px;color:#202124">
      <h2>Preparing Novix</h2>
      <p id="status">Checking model...</p>
      <p><strong>Model:</strong> <span id="model">${modelName}</span></p>
      <p style="word-break:break-all"><strong>Download path:</strong><br><span id="path">${destination}</span></p>
      <progress id="progress" value="0" max="100" style="width:100%;height:18px"></progress>
      <p id="percent">Waiting...</p>
    </body>
  `)}`);
  window.webContents.on("did-finish-load", () => {
    window.webContents.executeJavaScript(`
      document.getElementById("model").textContent = ${escapedModel};
      document.getElementById("path").textContent = ${escapedDestination};
    `);
  });
  return window;
}

function updateSetupWindow(window, status, downloaded, total) {
  if (!window || window.isDestroyed()) return;
  const percent = total ? Math.floor((downloaded / total) * 100) : 0;
  window.webContents.executeJavaScript(`
    document.getElementById("status").textContent = ${JSON.stringify(status)};
    document.getElementById("progress").value = ${percent};
    document.getElementById("percent").textContent = ${JSON.stringify(total ? `${percent}% downloaded` : "Downloading...")};
  `).catch(() => {});
}

function downloadModel(url, destination, progress, attempt = 1) {
  const partPath = `${destination}.part`;

  return new Promise((resolve, reject) => {
    const request = (requestUrl, redirects = 0) => {
      if (redirects > 5) {
        reject(new Error("Too many redirects while downloading the AI model."));
        return;
      }

      const existingBytes = fs.existsSync(partPath) ? fs.statSync(partPath).size : 0;
      const requestOptions = new URL(requestUrl);
      if (existingBytes) {
        requestOptions.headers = { Range: `bytes=${existingBytes}-` };
      }

      https.get(requestOptions, (response) => {
        if ([301, 302, 303, 307, 308].includes(response.statusCode)) {
          response.resume();
          request(new URL(response.headers.location, requestUrl).toString(), redirects + 1);
          return;
        }

        if (response.statusCode !== (existingBytes ? 206 : 200)) {
          response.resume();
          reject(new Error(`Model download failed with HTTP ${response.statusCode}.`));
          return;
        }

        const responseLength = Number(response.headers["content-length"] || 0);
        const totalBytes = existingBytes + responseLength;
        const output = fs.createWriteStream(partPath, { flags: existingBytes ? "a" : "w" });
        let downloaded = existingBytes;

        response.on("data", (chunk) => {
          downloaded += chunk.length;
          progress(downloaded, totalBytes);
        });
        response.on("error", (error) => {
          output.destroy();
          reject(error);
        });
        output.on("error", reject);
        output.on("finish", () => {
          if (!fs.existsSync(partPath) || fs.statSync(partPath).size === 0) {
            reject(new Error("The model download produced an empty file."));
            return;
          }
          fs.renameSync(partPath, destination);
          resolve();
        });
        response.pipe(output);
      }).on("error", reject);
    };

    request(url);
  }).catch((error) => {
    if (attempt >= 3) {
      throw new Error(`Model download failed after ${attempt} attempts: ${error.message}`);
    }
    console.warn(`Model download attempt ${attempt} failed: ${error.message}`);
    return new Promise((resolve) => setTimeout(resolve, 1000 * attempt))
      .then(() => downloadModel(url, destination, progress, attempt + 1));
  });
}

async function ensureModelAvailable(setupWindow) {
  const dataModelDir = path.join(app.getPath('userData'), "models");
  const activeModelPath = path.join(dataModelDir, "active_model.gguf");

  if (fs.existsSync(activeModelPath)) {
    console.log("Using existing model:", activeModelPath);
    updateSetupWindow(setupWindow, "Existing model found. Using it.", 1, 1);
    return;
  }

  fs.mkdirSync(dataModelDir, { recursive: true });
  updateSetupWindow(setupWindow, "Selecting a model for this device...", 0, 0);
  const recommendation = await requestBackendJson("/models/recommendation/");
  const selectedModel = recommendation.recommendation.model;
  const selectedName = selectedModel.model || getModelName(selectedModel.download_url);
  updateSetupWindow(setupWindow, `Installing ${selectedName}...`, 0, 0);
  await requestBackendJson("/models/recommendation/", "POST");
  console.log("Installed recommended model:", selectedName);
  updateSetupWindow(setupWindow, "Model download complete. Starting Novix...", 1, 1);
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
  window.loadURL(`data:text/html,<body style="font-family:sans-serif;padding:32px"><h2>Novix setup failed</h2><p>${message}</p><p>Check the installation and try again.</p></body>`);
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
          reject(new Error(`Django returned HTTP ${response.statusCode} before the app was ready.\n${djangoStartupLog.trim() || "No backend logs were captured."}`));
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

async function startDjango(setupWindow) {
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
  const dataModelDir = path.join(app.getPath('userData'), "models");
  const activeModelPath = path.join(dataModelDir, "active_model.gguf");
  const setupWindow = createSetupWindow("Selecting model...", activeModelPath);
  try {
    ensureDatabaseAvailable();
    await startDjango(setupWindow);
    await waitForDjango();
    await ensureModelAvailable(setupWindow);
  } catch (error) {
    console.error("Backend startup failed:", error);
    if (djangoProcess) {
      djangoProcess.kill();
      djangoProcess = null;
    }
    createErrorWindow(error);
    return;
  }

  if (!setupWindow.isDestroyed()) setupWindow.close();
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