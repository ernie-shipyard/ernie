// The one native thing the UI needs: a folder picker (multi-select) for the sample library.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("ernie", {
  chooseFolders: () => ipcRenderer.invoke("choose-folders"),
});
