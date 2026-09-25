const messages = document.getElementById("messages"); // all messages
const text = document.getElementById("msg"); // input message
const imageInput = document.getElementById("image-input");

const API_URL = "http://127.0.0.1:8000/chat/";
const CONVERSATION_URL = "http://127.0.0.1:8000/conversations/";
const RECOMMENDATION_URL = "http://127.0.0.1:8000/models/recommendation/";
const INSTALLED_MODELS_URL = "http://127.0.0.1:8000/models/installed/";
const MODEL_STORAGE_URL = "http://127.0.0.1:8000/models/storage/";
let currentConversation = null;

async function loadRecommendation() {
    const model = document.getElementById("recommendation-model");
    const reason = document.getElementById("recommendation-reason");
    const installButton = document.getElementById("install-recommendation");
    const status = document.getElementById("recommendation-status");

    try {
        const response = await fetch(RECOMMENDATION_URL);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Recommendation unavailable");

        const recommendation = data.recommendation;
        model.textContent = recommendation.model.model;
        reason.textContent = recommendation.reason;
        installButton.disabled = !recommendation.installable;
        status.textContent = recommendation.installable
            ? "Ready to install"
            : "Download details are not configured yet";
    } catch (error) {
        model.textContent = "Recommendation unavailable";
        reason.textContent = error.message;
        status.textContent = "";
    }
}

async function installRecommendation() {
    const button = document.getElementById("install-recommendation");
    const status = document.getElementById("recommendation-status");
    button.disabled = true;
    status.textContent = "Installing...";

    try {
        const response = await fetch(RECOMMENDATION_URL, { method: "POST" });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Installation failed");
        status.textContent = `Active: ${data.recommendation.model.model}`;
        await refreshModelsAndStorage();
    } catch (error) {
        status.textContent = error.message;
        button.disabled = false;
    }
}

// =====================================================
// MODEL MANAGEMENT
// =====================================================

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

async function loadStorage() {
    const storageElement = document.getElementById("model-storage");
    if (!storageElement) return;

    try {
        const response = await fetch(MODEL_STORAGE_URL);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Storage info unavailable");

        storageElement.textContent = `${formatBytes(data.installed_bytes)} used of ${formatBytes(data.total_bytes)} - ${formatBytes(data.free_bytes)} free`;
    } catch (error) {
        storageElement.textContent = error.message || "Storage info unavailable";
    }
}

async function loadModels() {
    const list = document.getElementById("model-list");
    if (!list) return;

    list.innerHTML = `<p class="recommendation-status">Loading models...</p>`;

    try {
        const response = await fetch(INSTALLED_MODELS_URL);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Failed to load models");

        renderModels(data.models || []);
    } catch (error) {
        list.innerHTML = `<p class="recommendation-status">${error.message || "Failed to load models"}</p>`;
    }
}

async function refreshModelsAndStorage() {
    await Promise.all([loadModels(), loadStorage()]);
}

function renderModels(models) {
    const list = document.getElementById("model-list");
    if (!list) return;
    list.innerHTML = "";

    if (!models.length) {
        list.innerHTML = `<p class="recommendation-status">No models installed yet.</p>`;
        return;
    }

    models.forEach((model) => {
        const item = document.createElement("div");
        item.className = `model-item${model.is_active ? " active" : ""}`;

        const info = document.createElement("div");
        info.className = "model-item-info";

        const name = document.createElement("span");
        name.className = "model-item-name";
        name.textContent = model.display_name;
        info.appendChild(name);

        const metaParts = [];
        if (model.quantization) metaParts.push(model.quantization);
        if (model.context_length) metaParts.push(`${model.context_length} ctx`);
        if (model.capabilities && model.capabilities.vision) metaParts.push("vision");

        if (metaParts.length) {
            const meta = document.createElement("span");
            meta.className = "model-item-meta";
            meta.textContent = metaParts.join(" - ");
            info.appendChild(meta);
        }

        const actions = document.createElement("div");
        actions.className = "model-item-actions";

        const status = document.createElement("p");
        status.className = "model-item-status";

        if (model.is_active) {
            const badge = document.createElement("span");
            badge.className = "model-active-badge";
            badge.textContent = "Active";
            actions.appendChild(badge);
        } else {
            const activateButton = document.createElement("button");
            activateButton.className = "model-activate-btn";
            activateButton.type = "button";
            activateButton.textContent = "Activate";

            const deleteButton = document.createElement("button");
            deleteButton.className = "model-delete-btn";
            deleteButton.type = "button";
            deleteButton.textContent = "Delete";

            activateButton.onclick = () => activateModel(model.id, activateButton, deleteButton, status);
            deleteButton.onclick = () => deleteModel(model.id, activateButton, deleteButton, status);

            actions.appendChild(activateButton);
            actions.appendChild(deleteButton);
        }

        item.appendChild(info);
        item.appendChild(actions);
        item.appendChild(status);
        list.appendChild(item);
    });
}

async function activateModel(id, activateButton, deleteButton, status) {
    activateButton.disabled = true;
    deleteButton.disabled = true;
    status.className = "model-item-status";
    status.textContent = "Activating...";

    try {
        const response = await fetch(INSTALLED_MODELS_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "Failed to activate model");

        status.classList.add("model-item-status-success");
        status.textContent = "Model activated";
        await refreshModelsAndStorage();
    } catch (error) {
        status.classList.add("model-item-status-error");
        status.textContent = error.message || "Failed to activate model";
        activateButton.disabled = false;
        deleteButton.disabled = false;
    }
}

async function deleteModel(id, activateButton, deleteButton, status) {
    activateButton.disabled = true;
    deleteButton.disabled = true;
    status.className = "model-item-status";
    status.textContent = "Deleting...";

    try {
        const response = await fetch(`${INSTALLED_MODELS_URL}${id}/`, {
            method: "DELETE",
        });

        if (response.status === 409) throw new Error("Active model cannot be deleted");
        if (response.status === 404) throw new Error("Model not found");
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(data.error || "Failed to delete model");
        }

        status.classList.add("model-item-status-success");
        status.textContent = "Model deleted";
        await refreshModelsAndStorage();
    } catch (error) {
        status.classList.add("model-item-status-error");
        status.textContent = error.message || "Failed to delete model";
        activateButton.disabled = false;
        deleteButton.disabled = false;
    }
}

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
document.getElementById("install-recommendation").onclick = installRecommendation;
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

initConversations();
loadRecommendation();
refreshModelsAndStorage();
