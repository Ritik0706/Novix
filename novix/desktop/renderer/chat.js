const messages = document.getElementById("messages"); // all messages
const text = document.getElementById("msg"); // input message
const imageInput = document.getElementById("image-input");
document.getElementById("attach-file").addEventListener("click", () => {
    imageInput.click();
});

const API_URL = "http://127.0.0.1:8000/chat/";
const CONVERSATION_URL = "http://127.0.0.1:8000/conversations/";
const MODEL_SETTINGS_URL = "http://127.0.0.1:8000/models/settings/";
const INSTALL_MODEL_URL = "http://127.0.0.1:8000/models/settings/install/";
const INSTALL_PROGRESS_URL = "http://127.0.0.1:8000/models/settings/install/progress/";
const INSTALL_CONTROL_URL = "http://127.0.0.1:8000/models/settings/install/control/";
const INSTALLED_MODELS_URL = "http://127.0.0.1:8000/models/installed/";
const CURRENT_MODEL_URL = "http://127.0.0.1:8000/models/current/";
let currentConversation = null;
let modelSettings = null;
let settingsOperationInProgress = false;
let pendingSettingsAction = null;
let downloadProgressPoll = null;
let downloadProgressGeneration = 0;
let downloadStatus = "idle";

function formatBytes(bytes) {
    if (typeof bytes !== "number" || Number.isNaN(bytes)) return "-";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = bytes;
    let unitIndex = 0;

    while (value >= 1024 && unitIndex < units.length - 1) {
        value /= 1024;
        unitIndex++;
    }

    const decimals = unitIndex === 0 || value >= 10 ? 0 : 1;
    return `${value.toFixed(decimals)} ${units[unitIndex]}`;
}

function formatContextLength(contextLength) {
    const tokens = Number.parseInt(contextLength, 10);
    if (!Number.isFinite(tokens) || tokens <= 0) return "";
    return `${tokens >= 1000 ? `${Math.floor(tokens / 1000)}K` : tokens} context`;
}

function makeSettingsTag(label, active = false) {
    const tag = document.createElement("span");
    tag.className = `settings-tag${active ? " active" : ""}`;
    tag.textContent = label;
    return tag;
}

function getActiveModel() {
    const installed = modelSettings?.installed_models.find((model) => model.is_active);
    return installed
        ? { id: installed.catalog_id, display_name: installed.display_name }
        : null;
}

function renderDeviceSettings(data) {
    const { hardware, storage } = data;
    document.getElementById("settings-cpu").textContent = hardware.cpu_cores ?? "—";
    document.getElementById("settings-ram").textContent = `${hardware.ram_gb ?? "—"} GB`;
    document.getElementById("settings-vram").textContent = hardware.vram_gb
        ? `${Number(hardware.vram_gb).toFixed(1)} GB`
        : "None";
    document.getElementById("settings-gpu-name").textContent = hardware.gpu_name || "No dedicated GPU detected";
    document.getElementById("settings-free-storage").textContent = formatBytes(storage.free_bytes);
    document.getElementById("settings-total-storage").textContent = `of ${formatBytes(storage.total_bytes)}`;

    const usedRatio = storage.total_bytes > 0
        ? (storage.total_bytes - storage.free_bytes) / storage.total_bytes
        : 0;
    document.getElementById("settings-storage-meter").style.width =
        `${Math.max(0, Math.min(100, usedRatio * 100))}%`;

    const fitModes = new Set(data.models.map((model) => model.fit_mode));
    const fitDescription = fitModes.has("hybrid")
        ? "This device can run models with CPU and GPU memory together."
        : fitModes.has("gpu")
            ? "Recommended models can fit within available GPU memory."
            : hardware.vram_gb
                ? "Recommended models use CPU memory; available GPU memory is limited."
                : "Recommended models are selected to run with CPU memory on this device.";
    document.getElementById("settings-device-mode").textContent =
        `${data.device_class}. ${fitDescription}`;
}

function renderSettingsModels(models, currentModel, installedModels) {
    const list = document.getElementById("settings-model-list");
    list.replaceChildren();
    const options = models.slice();
    const catalogIds = new Set(models.map((model) => model.id));
    installedModels.forEach((installed) => {
        if (installed.catalog_id && catalogIds.has(installed.catalog_id)) return;
        options.push({
            id: installed.catalog_id || `installed-${installed.id}`,
            display_name: installed.display_name,
            description: "Already downloaded on this device. Switching to it replaces the current model.",
            size_bytes: installed.size_bytes,
            quantization: installed.quantization,
            context_length: installed.context_length,
            fit_mode: "downloaded",
            recommended: false,
            installable: false,
            installed_only: true,
            installed_record_id: installed.id,
        });
    });

    if (!options.length) {
        const empty = document.createElement("li");
        empty.className = "settings-empty";
        empty.textContent = "No models currently fit this device.";
        list.appendChild(empty);
        return;
    }

    options.forEach((model) => {
        const installedModel = installedModels.find((installed) =>
            model.installed_only
                ? installed.id === model.installed_record_id
                : installed.catalog_id === model.id
        );
        const isActive = Boolean(installedModel && installedModel.is_active) ||
            Boolean(currentModel && currentModel.id === model.id);
        const item = document.createElement("li");
        item.className = `settings-model${model.recommended ? " recommended" : ""}`;

        const info = document.createElement("div");
        info.className = "settings-model-info";
        const heading = document.createElement("h4");
        heading.textContent = model.display_name;
        if (model.recommended) {
            const recommended = document.createElement("span");
            recommended.className = "settings-recommended";
            recommended.textContent = " (Recommended)";
            heading.appendChild(recommended);
        }
        info.appendChild(heading);

        const description = document.createElement("p");
        description.textContent = model.description;
        info.appendChild(description);

        const tags = document.createElement("div");
        tags.className = "settings-tags";
        tags.appendChild(makeSettingsTag(formatBytes(model.size_bytes)));
        if (model.quantization) tags.appendChild(makeSettingsTag(model.quantization));
        const contextLength = formatContextLength(model.context_length);
        if (contextLength) tags.appendChild(makeSettingsTag(contextLength));
        if (model.speed) tags.appendChild(makeSettingsTag(model.speed));
        if (model.fit_mode === "gpu") tags.appendChild(makeSettingsTag("GPU"));
        else if (model.fit_mode === "hybrid") tags.appendChild(makeSettingsTag("CPU + GPU"));
        else if (model.fit_mode === "cpu") tags.appendChild(makeSettingsTag("CPU"));
        else if (model.fit_mode === "downloaded") tags.appendChild(makeSettingsTag("Downloaded"));
        if (model.vision_support) tags.appendChild(makeSettingsTag("Vision"));
        if (isActive) {
            tags.appendChild(makeSettingsTag("Active", true));
        }
        info.appendChild(tags);
        item.appendChild(info);

        const actions = document.createElement("div");
        actions.className = "settings-model-actions";
        if (installedModel) {
            if (!isActive) {
                const button = document.createElement("button");
                button.className = "settings-action primary";
                button.type = "button";
                button.dataset.installedId = installedModel.id;
                button.dataset.installedAction = "activate";
                button.textContent = "Switch";
                button.disabled = settingsOperationInProgress;
                actions.appendChild(button);
            }

            const removeButton = document.createElement("button");
            removeButton.className = "settings-action danger";
            removeButton.type = "button";
            removeButton.disabled = settingsOperationInProgress;
            removeButton.dataset.installedId = installedModel.id;
            removeButton.dataset.installedAction = isActive ? "remove" : "delete";
            removeButton.textContent = isActive ? "Remove" : "Delete";
            actions.appendChild(removeButton);
        } else {
            const button = document.createElement("button");
            button.className = `settings-action${model.recommended ? " primary" : ""}`;
            button.type = "button";
            button.dataset.modelId = model.id;
            button.textContent = model.installable
                ? installedModels.length ? "Download and replace" : "Download"
                : "Not enough storage";
            button.disabled = settingsOperationInProgress || !model.installable;
            actions.appendChild(button);
        }
        item.appendChild(actions);
        list.appendChild(item);
    });
}

async function loadModelSettings() {
    const response = await fetch(MODEL_SETTINGS_URL);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Unable to load model settings");
    if (!data.hardware || !data.storage || !Array.isArray(data.models) ||
        !Array.isArray(data.installed_models)) {
        throw new Error("Invalid model settings response");
    }

    modelSettings = data;
    text.placeholder = data.installed_models.length
        ? "Ask anything..."
        : "Please download a model in Model settings to start chatting...";
    renderDeviceSettings(data);
    renderSettingsModels(data.models, getActiveModel(), data.installed_models);
    return data;
}

function setSettingsStatus(message, type = "") {
    const status = document.getElementById("settings-status");
    status.className = `settings-status${type ? ` ${type}` : ""}`;
    status.textContent = message;
}

function updateDownloadProgress(progress) {
    const modal = document.getElementById("download-progress-scrim");
    const statusMessage = document.getElementById("download-progress-message");
    const errorMessage = document.getElementById("download-progress-error");
    const progressBar = document.getElementById("download-progress-bar");
    const progressTrack = progressBar.parentElement;
    const downloaded = Number(progress.downloaded) || 0;
    const total = Number(progress.total) || 0;
    downloadStatus = progress.status;
    const finished = ["completed", "error", "cancelled"].includes(progress.status);
    const cancelling = progress.status === "cancelling";
    const percentage = total > 0 ? Math.min(100, Math.floor(downloaded / total * 100)) : 0;

    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    progressBar.style.width = `${percentage}%`;
    progressTrack.setAttribute("aria-valuenow", String(percentage));
    document.getElementById("download-progress-percent").textContent =
        total > 0 ? `${percentage}%` : "—";
    document.getElementById("download-progress-remaining").textContent =
        total > 0
            ? `${formatBytes(Math.max(0, total - downloaded))} remaining`
            : `${formatBytes(downloaded)} downloaded`;
    document.getElementById("download-spinner").hidden = finished || progress.status === "paused";
    document.getElementById("download-progress-close").hidden = !finished;
    const pauseButton = document.getElementById("download-progress-pause");
    const cancelButton = document.getElementById("download-progress-cancel");
    pauseButton.hidden = finished || cancelling;
    cancelButton.hidden = finished;
    pauseButton.disabled = cancelling;
    cancelButton.disabled = cancelling;
    pauseButton.textContent = progress.status === "paused" ? "Resume" : "Pause";
    cancelButton.textContent = cancelling ? "Cancelling…" : "Cancel";
    errorMessage.textContent = progress.status === "error"
        ? progress.error || "The model download failed."
        : "";

    const messages = {
        preparing: "Preparing download…",
        downloading: "Downloading model…",
        paused: "Download paused.",
        cancelling: "Cancelling download…",
        cancelled: "Download cancelled.",
        finishing: "Validating and installing model…",
        completed: "Download complete. The model is ready to use.",
        error: "The model download could not be completed.",
    };
    statusMessage.textContent = messages[progress.status] || "Waiting for download progress…";
    modal.classList.toggle("error", progress.status === "error");
    modal.classList.toggle("completed", progress.status === "completed");
    modal.classList.toggle("cancelled", progress.status === "cancelled");
}

function closeDownloadProgress() {
    if (!document.getElementById("download-progress-close").hidden) {
        const modal = document.getElementById("download-progress-scrim");
        modal.classList.remove("open", "error", "completed", "cancelled");
        modal.setAttribute("aria-hidden", "true");
    }
}

async function controlModelDownload(action) {
    const pauseButton = document.getElementById("download-progress-pause");
    const cancelButton = document.getElementById("download-progress-cancel");
    pauseButton.disabled = true;
    cancelButton.disabled = true;
    try {
        const response = await fetch(INSTALL_CONTROL_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action }),
        });
        const progress = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(progress.error || `Unable to ${action} download`);
        updateDownloadProgress(progress);
    } catch (error) {
        document.getElementById("download-progress-error").textContent =
            error.message || `Unable to ${action} download`;
        pauseButton.disabled = false;
        cancelButton.disabled = false;
    }
}

async function pollDownloadProgress(generation) {
    try {
        const response = await fetch(INSTALL_PROGRESS_URL, { cache: "no-store" });
        const progress = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(progress.error || "Unable to retrieve download progress");
        if (generation !== downloadProgressGeneration) return;
        updateDownloadProgress(progress);
    } catch (error) {
        if (generation !== downloadProgressGeneration) return;
        document.getElementById("download-progress-message").textContent =
            error.message || "Unable to retrieve download progress";
    }
}

function openSettingsPanel() {
    document.getElementById("card").classList.remove("open");
    const scrim = document.getElementById("settings-scrim");
    scrim.classList.add("open");
    scrim.setAttribute("aria-hidden", "false");
    document.getElementById("close-settings").focus();
}

function closeSettingsPanel() {
    closeSettingsConfirmation();
    document.getElementById("card").classList.add("open");
    const scrim = document.getElementById("settings-scrim");
    scrim.classList.remove("open");
    scrim.setAttribute("aria-hidden", "true");
    document.getElementById("open-settings").focus();
}

function showSettingsConfirmation(title, message, confirmText, action, danger = false) {
    document.getElementById("settings-confirm-title").textContent = title;
    document.getElementById("settings-confirm-message").textContent = message;
    const confirmButton = document.getElementById("settings-confirm-yes");
    confirmButton.textContent = confirmText;
    confirmButton.classList.toggle("danger", danger);
    pendingSettingsAction = action;
    const dialog = document.getElementById("settings-confirm");
    dialog.classList.add("open");
    dialog.setAttribute("aria-hidden", "false");
    confirmButton.focus();
}

function closeSettingsConfirmation() {
    const dialog = document.getElementById("settings-confirm");
    dialog.classList.remove("open");
    dialog.setAttribute("aria-hidden", "true");
    pendingSettingsAction = null;
}

async function installSettingsModel(model) {
    settingsOperationInProgress = true;
    setSettingsStatus(`Downloading and installing ${model.display_name}…`);
    document.getElementById("download-progress-model").textContent = model.display_name;
    document.getElementById("download-progress-error").textContent = "";
    document.getElementById("download-progress-close").hidden = true;
    document.getElementById("download-progress-scrim").classList.remove("cancelled");
    updateDownloadProgress({
        status: "preparing",
        downloaded: 0,
        total: model.size_bytes,
    });
    const progressGeneration = ++downloadProgressGeneration;
    downloadProgressPoll = window.setInterval(
        () => pollDownloadProgress(progressGeneration),
        500,
    );
    renderSettingsModels(modelSettings.models, getActiveModel(), modelSettings.installed_models);
    try {
        const response = await fetch(INSTALL_MODEL_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: model.id }),
        });
        const data = await response.json().catch(() => ({}));
        if (data.status === "cancelled") {
            updateDownloadProgress({ ...data, downloaded: 0, total: model.size_bytes });
            setSettingsStatus(`${model.display_name} download was cancelled.`, "error");
            return;
        }
        if (!response.ok) throw new Error(data.error || "Model installation failed");
        await loadModelSettings();
        updateDownloadProgress({
            status: "completed",
            downloaded: model.size_bytes,
            total: model.size_bytes,
        });
        setSettingsStatus(`${model.display_name} is now active.`, "success");
    } catch (error) {
        updateDownloadProgress({
            status: "error",
            error: error.message || "Model installation failed",
        });
        setSettingsStatus(error.message || "Model installation failed", "error");
    } finally {
        window.clearInterval(downloadProgressPoll);
        downloadProgressPoll = null;
        downloadProgressGeneration++;
        settingsOperationInProgress = false;
        if (modelSettings) {
            renderSettingsModels(
                modelSettings.models,
                getActiveModel(),
                modelSettings.installed_models,
            );
        }
    }
}

function confirmRemoveModel(model) {
    showSettingsConfirmation(
        `Remove ${model.display_name}?`,
        "The model file will be deleted. You will need to install a model before chatting again.",
        "Remove model",
        removeCurrentModel,
        true,
    );
}

function confirmSwitchInstalledModel(model) {
    showSettingsConfirmation(
        `Switch to ${model.display_name}?`,
        "This model is already on your device and will replace the currently stored model.",
        "Switch model",
        () => activateDownloadedModel(model),
    );
}

async function activateDownloadedModel(model) {
    settingsOperationInProgress = true;
    setSettingsStatus(`Switching to ${model.display_name}…`);
    try {
        const response = await fetch(INSTALLED_MODELS_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: model.id }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "Unable to switch models");
        await loadModelSettings();
        setSettingsStatus(`${model.display_name} is now active.`, "success");
    } catch (error) {
        setSettingsStatus(error.message || "Unable to switch models", "error");
    } finally {
        settingsOperationInProgress = false;
        if (modelSettings) {
            renderSettingsModels(
                modelSettings.models,
                getActiveModel(),
                modelSettings.installed_models,
            );
        }
    }
}

function confirmDeleteDownloadedModel(model) {
    showSettingsConfirmation(
        `Delete ${model.display_name}?`,
        "The downloaded model file will be deleted from this device.",
        "Delete model",
        () => deleteDownloadedModel(model),
        true,
    );
}

async function deleteDownloadedModel(model) {
    settingsOperationInProgress = true;
    setSettingsStatus(`Deleting ${model.display_name}…`);
    try {
        const response = await fetch(`${INSTALLED_MODELS_URL}${model.id}/`, {
            method: "DELETE",
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "Unable to delete downloaded model");
        await loadModelSettings();
        setSettingsStatus(`${model.display_name} was deleted.`, "success");
    } catch (error) {
        setSettingsStatus(error.message || "Unable to delete downloaded model", "error");
    } finally {
        settingsOperationInProgress = false;
        if (modelSettings) {
            renderSettingsModels(
                modelSettings.models,
                getActiveModel(),
                modelSettings.installed_models,
            );
        }
    }
}

async function removeCurrentModel() {
    settingsOperationInProgress = true;
    setSettingsStatus("Removing the active model…");
    try {
        const response = await fetch(CURRENT_MODEL_URL, { method: "DELETE" });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "Model removal failed");
        await loadModelSettings();
        setSettingsStatus("Active model removed.", "success");
    } catch (error) {
        setSettingsStatus(error.message || "Model removal failed", "error");
    } finally {
        settingsOperationInProgress = false;
        if (modelSettings) {
            renderSettingsModels(
                modelSettings.models,
                getActiveModel(),
                modelSettings.installed_models,
            );
        }
    }
}

document.getElementById("open-settings").addEventListener("click", async () => {
    openSettingsPanel();
    if (!modelSettings) {
        setSettingsStatus("Loading settings…");
        try {
            await loadModelSettings();
            setSettingsStatus("");
        } catch (error) {
            setSettingsStatus(error.message || "Unable to load model settings", "error");
        }
    }
});
document.getElementById("close-settings").addEventListener("click", closeSettingsPanel);
document.getElementById("settings-scrim").addEventListener("click", (event) => {
    if (event.target.id === "settings-scrim") closeSettingsPanel();
});
document.getElementById("settings-confirm-cancel").addEventListener("click", closeSettingsConfirmation);
document.getElementById("settings-confirm-yes").addEventListener("click", async () => {
    const action = pendingSettingsAction;
    closeSettingsConfirmation();
    if (action) await action();
});
document.getElementById("settings-model-list").addEventListener("click", (event) => {
    const installedButton = event.target.closest("[data-installed-action]");
    if (installedButton && modelSettings && !settingsOperationInProgress) {
        const installedModel = modelSettings.installed_models.find(
            (model) => String(model.id) === installedButton.dataset.installedId,
        );
        if (!installedModel) return;
        if (installedButton.dataset.installedAction === "activate") {
            confirmSwitchInstalledModel(installedModel);
        } else if (installedButton.dataset.installedAction === "delete") {
            confirmDeleteDownloadedModel(installedModel);
        } else if (installedButton.dataset.installedAction === "remove") {
            confirmRemoveModel(installedModel);
        }
        return;
    }

    const button = event.target.closest("[data-model-id]");
    if (!button || !modelSettings || settingsOperationInProgress) return;
    const model = modelSettings.models.find((item) => item.id === button.dataset.modelId);
    if (!model || !model.installable) return;

    const installedModels = modelSettings.installed_models;
    if (installedModels.length) {
        const currentModel = getActiveModel() || installedModels[0];
        showSettingsConfirmation(
            "Replace downloaded model?",
            `${model.display_name} will replace ${currentModel.display_name}. Only one model can be downloaded at a time.`,
            "Install and replace",
            () => installSettingsModel(model),
        );
    } else {
        installSettingsModel(model);
    }
});
document.getElementById("download-progress-close").addEventListener("click", closeDownloadProgress);
document.getElementById("download-progress-pause").addEventListener("click", () => {
    controlModelDownload(downloadStatus === "paused" ? "resume" : "pause");
});
document.getElementById("download-progress-cancel").addEventListener("click", () => {
    controlModelDownload("cancel");
});
document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (document.getElementById("settings-confirm").classList.contains("open")) {
        closeSettingsConfirmation();
    } else if (document.getElementById("settings-scrim").classList.contains("open")) {
        closeSettingsPanel();
    }
});

async function createConversation() {
    const response = await fetch(CONVERSATION_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
    });

    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || "Failed to create conversation");
    }

    return await response.json();
}

async function getConversations() {
    const response = await fetch(CONVERSATION_URL);
    return await response.json();
}

async function loadConversation(id) {
    const response = await fetch(`${CONVERSATION_URL}${id}/`);
    if (!response.ok) {
        throw new Error("Failed to load conversation");
    }
    return await response.json();
}

async function loadConversations() {
    const data = await getConversations();
    renderConversations(data.conversations);
}

async function initConversations() {
    let data = await getConversations();

    if (!data.conversations.length) {
        const conversation = await createConversation();
        currentConversation = conversation.id;
        data = await getConversations();
    } else {
        currentConversation = data.conversations[0].id;
    }

    renderConversations(data.conversations);
    await loadMessages(currentConversation);
}

function add(txt, c) {
    // will take input string and username
    const d = document.createElement("div");
    d.className = `bubble ${c}`;
    d.textContent = txt;
    messages.appendChild(d);
    messages.scrollTop = messages.scrollHeight;
}

// loading animation
function showTyping() {
    const div = document.createElement("div");
    div.className = "bubble bot";
    div.id = "typing";

    div.innerHTML = `
      <div class="typing">
        <span></span>
        <span></span>
        <span></span>
      </div>
    `;

    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
}

function hideTyping() {
    const typing = document.getElementById("typing");
    if (typing) typing.remove();
}

function readImageAsDataUrl(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("Unable to read the image"));
        reader.readAsDataURL(file);
    });
}

async function sendToBackend(message, image) {
    if (!currentConversation) {
        throw new Error("No conversation selected");
    }

    const response = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: currentConversation, message, image }),
    });

    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || "Request failed");
    }

    const data = await response.json();
    return data.reply;
}

async function submitMessage() {
    const v = text.value.trim(); // trim the input string
    if (!v) return;

    let image;
    try {
        if (imageInput.files[0]) {
            image = await readImageAsDataUrl(imageInput.files[0]);
        }
    } catch (error) {
        add(error.message, "bot");
        return;
    }

    add(v, "user");
    text.value = "";

    imageInput.value = "";

    // Show loading
    showTyping();
    try {
        const reply = await sendToBackend(v, image);

        // Remove loading
        hideTyping();
        add(reply, "bot");
        await loadMessages(currentConversation);
    } catch (err) {
        // Remove loading
        hideTyping();
        add(err.message || "Something went wrong", "bot");
    }
}

document.getElementById("send").onclick = submitMessage;
imageInput.onchange = () => {
    if (imageInput.files[0]) {
        imageInput.title = imageInput.files[0].name;
    }
};

text.onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        document.getElementById("send").click();
    }
};

// sidebar btn
const menuBtn = document.getElementById("menuBtn");
const card = document.getElementById("card");

menuBtn.addEventListener("click", () => {
    card.classList.toggle("open");
});

const newChatBtn = document.getElementById("new-chat");
newChatBtn?.addEventListener("click", async () => {
    const conversation = await createConversation();
    currentConversation = conversation.id;
    await loadConversations();
    await loadMessages(currentConversation);
});

function renderConversations(conversations) {
    const list = document.getElementById("conversation-list");
    list.innerHTML = "";

    conversations.forEach((c) => {

        // Conversation row
        const div = document.createElement("div");
        div.className = "conversation";

        // Title
        const title = document.createElement("span");
        title.className = "conversation-title";
        title.innerText = c.title;

        // Three-dot button
        const menuBtn = document.createElement("button");
        menuBtn.className = "conversation-menu-btn";
        menuBtn.innerText = "⋮";
        menuBtn.title = "More";

        // Dropdown menu
        const menu = document.createElement("div");
        menu.className = "conversation-menu";

        // =========================
        // Rename button
        // =========================
        const renameBtn = document.createElement("button");
        renameBtn.innerText = "✏️ Rename";

        renameBtn.onclick = (e) => {
            e.stopPropagation();

            // Close dropdown
            menu.classList.remove("show");

            // Open custom rename modal
            openRenameModal(c, conversations);
        };

        // =========================
        // Delete button
        // =========================
        const deleteBtn = document.createElement("button");
        deleteBtn.innerText = "🗑️ Delete";

        deleteBtn.onclick = (e) => {
            e.stopPropagation();

            // Close dropdown
            menu.classList.remove("show");

            // Open custom delete modal
            openDeleteModal(c, conversations);
        };

        // Add buttons to menu
        menu.appendChild(renameBtn);
        menu.appendChild(deleteBtn);

        // Toggle menu
        menuBtn.onclick = (e) => {
            e.stopPropagation();

            // Close other open menus
            document.querySelectorAll(".conversation-menu").forEach((m) => {
                if (m !== menu) {
                    m.classList.remove("show");
                }
            });

            menu.classList.toggle("show");
        };

        // Prevent conversation selection when clicking menu
        menu.onclick = (e) => {
            e.stopPropagation();
        };

        // Select conversation
        div.onclick = () => {
            selectConversation(c.id);
        };

        // Active conversation
        if (c.id === currentConversation) {
            div.classList.add("active");
        }

        // Build row
        div.appendChild(title);
        div.appendChild(menuBtn);
        div.appendChild(menu);

        list.appendChild(div);
    });
}


// =====================================================
// RENAME MODAL
// =====================================================

function openRenameModal(conversation, conversations) {

    const modal = document.getElementById("rename-modal");
    const input = document.getElementById("rename-input");
    const cancelBtn = document.getElementById("rename-cancel-btn");
    const confirmBtn = document.getElementById("rename-confirm-btn");

    // Set current conversation name
    input.value = conversation.title;

    // Show modal
    modal.classList.add("show");

    // Focus input
    setTimeout(() => {
        input.focus();
        input.select();
    }, 100);

    // Remove previous event handlers
    const newCancelBtn = cancelBtn.cloneNode(true);
    const newConfirmBtn = confirmBtn.cloneNode(true);

    cancelBtn.replaceWith(newCancelBtn);
    confirmBtn.replaceWith(newConfirmBtn);

    // Cancel
    newCancelBtn.onclick = () => {
        closeRenameModal();
    };

    // Rename
    newConfirmBtn.onclick = async () => {
        const newName = input.value.trim();

        if (!newName) {
            input.focus();
            input.classList.add("input-error");

            setTimeout(() => {
                input.classList.remove("input-error");
            }, 500);

            return;
        }

        try {
            await renameConversation(conversation.id, newName);
            conversation.title = newName;
            closeRenameModal();
            renderConversations(conversations);
        } catch (err) {
            add(err.message || "Failed to rename conversation", "bot");
        }
    };

    // Rename using Enter key
    input.onkeydown = (e) => {
        if (e.key === "Enter") {
            newConfirmBtn.click();
        }

        if (e.key === "Escape") {
            closeRenameModal();
        }
    };
}


function closeRenameModal() {
    const modal = document.getElementById("rename-modal");
    modal.classList.remove("show");
}

async function renameConversation(id, newName) {
    const url = `${CONVERSATION_URL}${id}/rename/`;

    const response = await fetch(url, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, name: newName }),
    });

    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || "Failed to rename conversation");
    }

    return await response.json();
}

// =====================================================
// DELETE MODAL
// =====================================================

function openDeleteModal(conversation, conversations) {

    const modal = document.getElementById("delete-modal");
    const cancelBtn = document.getElementById("delete-cancel-btn");
    const confirmBtn = document.getElementById("delete-confirm-btn");

    // Show conversation name
    const nameElement = document.getElementById("delete-conversation-name");

    if (nameElement) {
        nameElement.innerText = conversation.title;
    }

    // Show modal
    modal.classList.add("show");

    // Remove previous event handlers
    const newCancelBtn = cancelBtn.cloneNode(true);
    const newConfirmBtn = confirmBtn.cloneNode(true);

    cancelBtn.replaceWith(newCancelBtn);
    confirmBtn.replaceWith(newConfirmBtn);

    // Cancel
    newCancelBtn.onclick = () => {
        closeDeleteModal();
    };

    // Delete
    newConfirmBtn.onclick = async () => {
        try {
            await deleteConversation(conversation.id);

            const index = conversations.findIndex(
                (item) => item.id === conversation.id
            );

            if (index !== -1) {
                conversations.splice(index, 1);
            }

            closeDeleteModal();
            renderConversations(conversations);
        } catch (err) {
            add(err.message || "Failed to delete conversation", "bot");
        }
    };

    // Escape key
    document.onkeydown = (e) => {
        if (e.key === "Escape") {
            closeDeleteModal();
        }
    };
}


function closeDeleteModal() {
    const modal = document.getElementById("delete-modal");
    modal.classList.remove("show");
}

async function deleteConversation(id) {
    const url = `${CONVERSATION_URL}${id}/`;

    const response = await fetch(url, {
        method: "DELETE",
    });

    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || "Failed to delete conversation");
    }
    window.location.reload();
    return await response.json();

}


// =====================================================
// CLOSE MODALS WHEN CLICKING BACKDROP
// =====================================================

document.addEventListener("click", (e) => {

    const renameModal = document.getElementById("rename-modal");
    const deleteModal = document.getElementById("delete-modal");

    if (e.target === renameModal) {
        closeRenameModal();
    }

    if (e.target === deleteModal) {
        closeDeleteModal();
    }
});

async function selectConversation(id) {
    if (currentConversation === id) return;
    currentConversation = id;
    await loadMessages(id);
    await loadConversations();
}

async function loadMessages(id) {
    const response = await fetch(`${CONVERSATION_URL}${id}/`);
    if (!response.ok) {
        messages.innerHTML = "";
        add("Unable to load conversation.", "bot");
        return;
    }

    const data = await response.json();
    messages.innerHTML = "";
    data.messages.forEach((m) => {
        add(m.content, m.role === "user" ? "user" : "bot");
    });
}

// close btn
document.getElementById("closeBtn").addEventListener("click", () => {
    window.electronAPI.closeWindow();
});

const maximizeButton = document.getElementById("maximizeBtn");
maximizeButton.addEventListener("click", () => {
    window.electronAPI.toggleMaximize();
});
window.electronAPI.onWindowMaximizeState((isMaximized) => {
    const action = isMaximized ? "Restore" : "Maximize";
    maximizeButton.setAttribute("aria-label", `${action} window`);
    maximizeButton.setAttribute("title", `${action} window`);
    maximizeButton.setAttribute("aria-pressed", String(isMaximized));
    document.getElementById("card").classList.toggle("maximized", isMaximized);
});

initConversations();
loadModelSettings()
    .then((data) => {
        if (!data.installed_models.some((model) => model.is_active)) openSettingsPanel();
    })
    .catch((error) => {
        openSettingsPanel();
        setSettingsStatus(error.message || "Unable to load model settings", "error");
    });
