// `npm run dev`: starts the local backend and the viewer together. They use ports 8000 and 5173 unless
// something else already does, in which case the next free ports are used. VIEWER_API_PORT and
// VIEWER_WEB_PORT change where the search starts.
import { spawn } from "node:child_process";
import { freePort } from "./ports.mjs";

const api = await freePort(Number(process.env.VIEWER_API_PORT ?? 8000));
const web = await freePort(Number(process.env.VIEWER_WEB_PORT ?? 5173));
console.log(`\nViewer: http://127.0.0.1:${web}  (local backend on port ${api})\n`);

const child = spawn(
  "concurrently",
  [
    "-k", "-n", "api,web", "-c", "blue,green",
    `uv run --project ../backend uvicorn app.main:app --app-dir ../backend --host 127.0.0.1 --port ${api}`,
    "vite", // reads VIEWER_WEB_PORT, and VIEWER_API_PORT for its /api proxy
  ],
  { stdio: "inherit", env: { ...process.env, VIEWER_API_PORT: String(api), VIEWER_WEB_PORT: String(web) } },
);
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
