// The packaged app runs the backend from this repo (.venv, .env, ernie-ui/dist), so record where it is.
const fs = require("fs");
const path = require("path");

const repo = path.resolve(__dirname, "..", "..");
fs.writeFileSync(path.join(__dirname, "repo.json"), JSON.stringify({ repo }, null, 2) + "\n");
console.log(`Ernie.app will run the backend from ${repo}`);
