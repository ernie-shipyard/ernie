import { api } from "./api.js";

// Sample library folders: the native multi-select picker in the Ernie app, typed paths in a plain browser.
async function pickFolders() {
  if (window.ernie?.chooseFolders) return window.ernie.chooseFolders();
  const typed = window.prompt("Sample library folders, separated by ':'");
  return typed ? typed.split(":").map((d) => d.trim()).filter(Boolean) : [];
}

// Picks, saves and rescans. null when the producer cancels; else {dirs, count, tags}.
export async function chooseLibrary() {
  const dirs = await pickFolders();
  return dirs.length ? api.setSampleDirs(dirs) : null;
}
