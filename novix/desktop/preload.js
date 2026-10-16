window.addEventListener('DOMContentLoaded', () => { });


// clse btn related 
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronAPI", {
    closeWindow: () => ipcRenderer.send("close-window"),
    toggleMaximize: () => ipcRenderer.send("toggle-maximize-window"),
    onWindowMaximizeState: (callback) => ipcRenderer.on(
        "window-maximize-state",
        (_event, isMaximized) => callback(isMaximized)
    )
});