# Novix

**A Windows-focused desktop chat app for running local language models.**

Novix combines an Electron desktop interface with a Django backend and a locally hosted `llama.cpp` inference server. It stores conversations in SQLite and helps users select a downloadable GGUF model based on the computer's available hardware and storage.

> **Current scope:** The checked-in desktop app is a frameless chat window. The repository includes blob mascot artwork, but does not currently implement the planned floating mascot/chat-bubble window flow described in older project notes.

## What Novix does

Many AI chat applications rely on a remote service. Novix is designed to run inference on the user's own computer after a model has been downloaded. Its intended users include people who want a desktop chat interface for local models, as well as developers exploring desktop application packaging, hardware-aware model selection, and a Python/JavaScript application architecture.

Model downloads require an internet connection. Once a model and its runtime are available, prompts are sent from the desktop app to the local backend and local inference server.

## Features

- **Desktop chat:** A resizable Electron window with a custom title bar and minimize, maximize/restore, and close controls.
- **Conversation history:** Start conversations, reopen saved chats, rename them, and delete them. The backend persists the history in SQLite.
- **Local model setup:** Browse model choices and a hardware-based recommendation, see model size and fit information, and download a model from the app.
- **Download controls:** View progress and pause, resume, or cancel a model download.
- **Hardware and storage information:** The settings screen reports detected CPU, memory, GPU/VRAM, and available storage to help explain model recommendations.
- **CPU and GPU runtime selection:** The backend can select a CPU, CUDA, or Vulkan `llama.cpp` runtime when its runtime pack is available, falling back to another available runtime where possible.
- **Image attachments:** Attach an image to a chat when the active model advertises vision support. Image input is rejected when the active model does not support it.
- **Long conversation context:** The backend keeps recent messages in context and creates/updates a summary after a threshold of unsummarized messages.

## Screenshots and demo

There is no UI screenshot, hosted demo, or demo account included in the repository. The image below is the available mascot artwork; it is **not** a screenshot of the running application.

![Novix mascot artwork (not an application screenshot)](novix/desktop/assets/blob.png)

> **Screenshot placeholder:** Add a screenshot of the running chat window and model settings here when one is available.

## Technology

| Area | Technologies |
| --- | --- |
| Desktop application | Electron 43, Node.js, HTML, CSS, JavaScript |
| Backend and local API | Python 3.12+, Django 6, Waitress |
| Persistence | SQLite, Django ORM |
| Local inference | `llama.cpp` `llama-server`, GGUF model files |
| Hardware and model handling | `psutil`, GGUF metadata inspection, model catalog and download/checksum logic |
| Windows packaging | PyInstaller and electron-builder (NSIS installer target) |

The backend exposes Django JSON views. Although Django REST Framework is listed in `requirements.txt`, the application endpoints inspected here are implemented with Django views and `JsonResponse`.

## Prerequisites

The repository is configured for a **Windows x64 desktop build**. Other operating systems are not documented or configured as installer targets.

- Windows x64 (the exact minimum Windows version has not been declared)
- Python 3.12 or later and `pip`
- Node.js and npm compatible with the Electron version in `novix/desktop/package.json`
- Internet access to download a model and, when preparing runtime bundles, upstream runtime archives
- Disk space for the model you choose; the installer checks for roughly twice the model file size in free space while installing
- A supported llama.cpp runtime. CPU inference is supported; CUDA/Vulkan acceleration depends on the matching runtime, graphics hardware, and drivers

The model catalog contains multiple model sizes. The required memory and disk space depend on the selected model; the settings screen displays the device-specific fit and storage information.

## Set up for development

Commands below use PowerShell and assume you are in the repository root (the folder containing this README).

### 1. Create a Python environment and install dependencies

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 2. Configure Django

The Django settings loader reads `novix/novix/config.env`. This file is intentionally ignored by Git. A safe template is provided as [`.env.example`](.env.example); copy it to the path the application reads, then replace the placeholder secret with a generated local key:

```powershell
Copy-Item .env.example novix\novix\config.env
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Edit `novix\novix\config.env` and set `SECRET_KEY` to the generated value. Do not commit the copied `config.env` or a real secret. The optional `NOVIX_RUNTIME` setting can be `auto`, `cpu`, `cuda`, or `vulkan`; leave it at `auto` unless you need to select a runtime explicitly.

Electron sets `NOVIX_DATA_DIR` for its child backend process to Electron's per-user application data folder. When running Django directly, the default data folder is `novix\.local_data`. Set `NOVIX_DATA_DIR` in the process environment before launching Django if you need a different location.

### 3. Initialize the database and run the backend tests

```powershell
Set-Location novix
python manage.py migrate
python manage.py test synola
```

The project uses Django's built-in test runner. The tests cover chat context and inference behavior, model settings and installation APIs, hardware/runtime selection, and related backend logic.

### 4. Install and start the desktop app

From the repository root:

```powershell
Set-Location novix\desktop
npm ci
npm start
```

Electron starts the backend on `127.0.0.1:8000` and waits for it to respond before opening the chat window. In development, it starts `run_backend.py` with Python when a built backend executable is not present. Keep the Python virtual environment active when launching this fallback so it can find the installed Python dependencies.

On first run, open **Model settings**, review the hardware-based recommendations, and install a model. Model downloads come from the Hugging Face URLs in the model catalog. After installation, create a chat and send a message.

## Build a Windows installer

The Electron builder is configured to create an NSIS installer in `novix\desktop\release`. The build expects a PyInstaller backend, a migrated seed database, and llama.cpp runtime files at the configured paths.

1. From the repository root, activate the Python environment and initialize the database:

   ```powershell
   Set-Location novix
   . ..\.venv\Scripts\Activate.ps1
   python manage.py migrate
   ```

2. Prepare runtime packs in the paths expected by `synola/services/runtime_manager.py`. The helper below downloads and verifies the CUDA and Vulkan Windows archives; it does **not** install the CPU runtime:

   ```powershell
   .\tools\install_llama_runtimes.ps1
   ```

   Place a compatible CPU `llama-server.exe` pack under `resources\llama\cpu\llama-b10818-bin-win-cpu-x64`. Check all three runtime packs with:

   ```powershell
   python .\tools\check_runtime_packs.py
   ```

3. Build the backend from the `novix` directory:

   ```powershell
   pyinstaller novix-backend.spec
   ```

4. Build the Electron installer:

   ```powershell
   Set-Location desktop
   npm ci
   npm run dist
   ```

The builder copies the backend from `novix\dist\novix-backend`, runtime files from `novix\resources\llama`, and the database from `novix\.local_data\db.sqlite3`. These generated files and runtime binaries are not all stored in Git, so prepare them before packaging. The available `dist` script targets Windows; no macOS/Linux installer configuration is present.

## First-time use

1. Launch Novix. The desktop process starts the local Django service and applies database migrations.
2. Select **Model settings** in the sidebar. Review the detected hardware, available storage, and model list.
3. Install a model marked installable. Downloading requires internet access; the progress dialog offers pause, resume, and cancel controls.
4. Select **New Chat**, enter a prompt, and press Enter or the send button. Use Shift+Enter for a line break.
5. To continue a previous conversation, select it in the sidebar. Use its menu to rename or delete it.
6. To attach an image, use the attachment button. Image prompts work only with a model that supports vision.
7. Model changes replace the model currently stored by this app; the settings screen asks for confirmation before replacing it.

## Configuration

| Name | Purpose | Default / notes |
| --- | --- | --- |
| `SECRET_KEY` | Django cryptographic key | The settings file contains an insecure development fallback. Set a private value in `novix/novix/config.env`. |
| `NOVIX_RUNTIME` | Select the inference runtime | `auto` by default; optional values are `cpu`, `cuda`, or `vulkan`. |
| `NOVIX_DATA_DIR` | Override the backend's database, model, and log directory | Electron sets this for its backend process. For standalone backend runs, set it in the shell before starting Django; it is resolved before `config.env` is loaded. |

The checked-in [`.env.example`](.env.example) is a template for `config.env`; it is not itself loaded by Django.

## Backend API

The desktop renderer calls the local backend at `http://127.0.0.1:8000`. Requests and responses use JSON except for the image, which the UI sends as a data URL inside the chat JSON body.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Backend readiness response |
| `GET`, `POST` | `/conversations/` | List or create conversations |
| `GET`, `DELETE` | `/conversations/<id>/` | Read or delete a conversation and its messages |
| `POST` | `/conversations/<id>/rename/` | Rename a conversation (`{"name": "..."}`) |
| `POST` | `/chat/` | Send a message (`conversation_id`, `message`, optional `image`); returns `reply` |
| `GET` | `/models/settings/` | Hardware, storage, recommendation, available and installed models |
| `POST` | `/models/settings/install/` | Install a catalog model (`{"id": "..."}`) |
| `GET` | `/models/settings/install/progress/` | Read model download status |
| `POST` | `/models/settings/install/control/` | Pause, resume, or cancel; provide `action` as `pause`, `resume`, or `cancel` |
| `GET`, `POST` | `/models/installed/` | List installed models or activate one (`{"id": <model-id>}`) |
| `DELETE` | `/models/installed/<id>/` | Delete an installed inactive model |
| `DELETE` | `/models/current/` | Remove the active model |
| `GET` | `/models/recommendation/` | Get a hardware-based model recommendation |
| `POST` | `/models/recommendation/` | Install the recommended model |
| `GET` | `/runtime/diagnostics/` | Report detected hardware and runtime availability |
| `GET` | `/models/storage/` | Report installed and available storage |

The backend also includes `POST /models/swap/` for direct model replacement. Chat inference is served locally on `127.0.0.1:8090` by the selected `llama.cpp` runtime.

## Project structure

```text
.
├── .env.example                 # Safe local configuration template
├── README.md
├── requirements.txt             # Python dependencies
└── novix/
    ├── manage.py                # Django management commands
    ├── run_backend.py           # Waitress startup, migrations, inference lifecycle
    ├── novix/                   # Django settings and URL/WSGI configuration
    ├── synola/                  # App models, API views, migrations, and tests
    │   └── services/            # Inference, hardware, runtime, model, and context services
    ├── templates/               # Backend status page
    ├── tools/                   # Runtime setup and validation scripts
    ├── resources/llama/         # Local llama.cpp runtime packs (prepared separately)
    └── desktop/
        ├── main.js              # Electron window and backend process lifecycle
        ├── preload.js           # Narrow renderer-to-main IPC bridge
        └── renderer/            # Chat interface, styles, and browser-side logic
```

The `novix\.local_data` directory is created locally for the database, downloaded models, and logs; it is ignored by Git.

## Data, deployment, and licensing

- **Data storage:** Django uses SQLite. Electron stores application data under its per-user application data directory; standalone Django defaults to `novix\.local_data`. Model and backend logs are stored with that application data.
- **Deployment:** The project currently supports a Windows NSIS installer build through electron-builder. A public download URL or hosted demo is not configured in the repository.
- **License:** No project license file is present. Do not assume the application or its model catalog is open source under a particular license. Model licenses vary; check each model's own terms before downloading or redistributing it.
- **Contributions:** No contribution guide is present. Before opening a contribution, check with the repository maintainer for the preferred workflow and licensing expectations.
- **Acknowledgments:** Local inference uses llama.cpp, and the model catalog references GGUF artifacts hosted on Hugging Face. Runtime and model licenses remain those of their respective upstream projects.

## Project highlights

- **Desktop/backend process lifecycle:** Electron launches a loopback-only Django/Waitress service, waits for readiness, and manages shutdown of the backend process.
- **Constrained renderer integration:** A context-isolated preload script exposes a small IPC API for window controls rather than exposing Electron directly to the renderer.
- **Local inference orchestration:** The backend validates available CPU/CUDA/Vulkan runtime packs, selects a compatible runtime, configures model execution, and monitors server health.
- **Safer model replacement:** Model downloads are staged, checked against catalog metadata, inspected as GGUF files, and activated with rollback handling if startup fails.
- **Hardware-aware setup:** Model recommendations combine system memory, GPU/VRAM, and free storage; the settings UI exposes installation progress and controls.
- **Conversation lifecycle and context management:** Django models persist messages and conversation summaries, while tests cover model and runtime decision paths as well as API behavior.
- **Packaged application workflow:** PyInstaller builds the Python backend, and electron-builder packages it with runtime assets into a Windows installer.

## GitHub repository metadata

**Suggested About description (165 characters):**

> A Windows desktop AI chat app that runs GGUF language models locally, with a Django backend, Electron interface, hardware-aware model recommendations, and persistent conversations.

**Suggested topics:** `electron`, `django`, `python`, `javascript`, `desktop-app`, `local-llm`, `llama-cpp`, `gguf`, `sqlite`, `offline-ai`

## Details to add when available

- A real screenshot of the chat window and model settings (the current image is mascot art only).
- The public repository URL and any live demo or release download link.
- Confirmed supported Windows versions, minimum system requirements, and supported GPU/driver combinations.
- A project license and contribution policy.
- A documented source and setup procedure for the CPU runtime pack; the current helper installs only CUDA and Vulkan packs.
- Any intended privacy/data-retention statement that has been verified against the complete release and model-download flow.
