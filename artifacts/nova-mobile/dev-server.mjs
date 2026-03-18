/**
 * Dev proxy server for nova-mobile.
 *
 * Opens an HTTP proxy on PROXY_PORT (env PORT, default 23870) immediately so
 * Replit's health-check always sees a 200. Vite runs on an internal port
 * (PROXY_PORT+1) and real traffic is forwarded there once Vite is ready.
 */
import http from "http";
import { spawn } from "child_process";

const PROXY_PORT = Number(process.env.PORT ?? 23870);
const VITE_PORT  = PROXY_PORT + 1;          // e.g. 23871 or 23844
const BASE_PATH  = process.env.BASE_PATH ?? "/mobile/";

let viteReady = false;

// ── 1. Open proxy immediately so health-check sees port open ──────────────
const server = http.createServer((req, res) => {
  if (!viteReady) {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(`<!doctype html><html><body><p>Starting…</p>
      <script>setTimeout(()=>location.reload(),600)</script></body></html>`);
    return;
  }

  const opts = {
    hostname: "127.0.0.1",
    port: VITE_PORT,
    path: req.url,
    method: req.method,
    headers: { ...req.headers, host: `localhost:${VITE_PORT}` },
  };

  const proxy = http.request(opts, (pr) => {
    res.writeHead(pr.statusCode ?? 200, pr.headers);
    pr.pipe(res, { end: true });
  });

  proxy.on("error", (err) => { res.writeHead(502); res.end(err.message); });
  req.pipe(proxy, { end: true });
});

server.listen(PROXY_PORT, "0.0.0.0", () => {
  console.log(`\n  Proxy :${PROXY_PORT} → Vite :${VITE_PORT} (${BASE_PATH})\n`);
});

// ── 2. Spawn Vite on internal port ────────────────────────────────────────
const vite = spawn(
  "pnpm",
  ["exec", "vite", "--config", "vite.config.ts", "--host", "0.0.0.0",
   "--port", String(VITE_PORT), "--strictPort"],
  {
    env: { ...process.env, PORT: String(VITE_PORT), BASE_PATH },
    stdio: ["ignore", "pipe", "pipe"],
    shell: false,
    cwd: import.meta.dirname,
  }
);

vite.stdout.on("data", (chunk) => {
  process.stdout.write(chunk);
  if (!viteReady && chunk.toString().includes("ready in")) {
    viteReady = true;
    console.log(`\n  → Proxy now forwarding :${PROXY_PORT} → :${VITE_PORT}\n`);
  }
});

vite.stderr.on("data", (chunk) => process.stderr.write(chunk));

vite.on("exit", (code) => {
  server.close();
  process.exit(code ?? 1);
});

process.on("SIGTERM", () => { vite.kill("SIGTERM"); server.close(); process.exit(0); });
process.on("SIGINT",  () => { vite.kill("SIGTERM"); server.close(); process.exit(0); });
