import { Router, type IRouter, type Request, type Response } from "express";

const router: IRouter = Router();
const BROWSER_AGENT_URL = process.env.BROWSER_AGENT_URL || "http://localhost:8000";

async function proxyJson(req: Request, res: Response, path: string, method = "GET", body?: unknown) {
  try {
    const opts: RequestInit = {
      method,
      headers: { "Content-Type": "application/json" },
    };
    if (body) opts.body = JSON.stringify(body);
    const upstream = await fetch(`${BROWSER_AGENT_URL}${path}`, opts);
    const data = await upstream.json();
    res.status(upstream.status).json(data);
  } catch (err) {
    res.status(503).json({
      error: "browser_agent_unavailable",
      message: "Browser agent server is not running. Please wait a moment and retry.",
    });
  }
}

router.get("/browser/health", async (req, res) => {
  await proxyJson(req, res, "/health");
});

router.get("/browser/tasks", async (req, res) => {
  await proxyJson(req, res, "/tasks");
});

router.get("/browser/tasks/:taskId", async (req, res) => {
  await proxyJson(req, res, `/tasks/${req.params.taskId}`);
});

router.post("/browser/run", async (req, res) => {
  await proxyJson(req, res, "/run", "POST", req.body);
});

router.post("/browser/tasks/:taskId/respond", async (req, res) => {
  await proxyJson(req, res, `/tasks/${req.params.taskId}/respond`, "POST", req.body);
});

router.post("/browser/tasks/:taskId/inject", async (req, res) => {
  await proxyJson(req, res, `/tasks/${req.params.taskId}/inject`, "POST", req.body);
});

router.get("/browser/screenshot/:taskId", async (req, res) => {
  await proxyJson(req, res, `/screenshot/${req.params.taskId}`);
});

router.post("/browser/tasks/clear-stuck", async (req, res) => {
  await proxyJson(req, res, "/tasks/clear-stuck", "POST");
});

router.get("/browser/stream/:taskId", async (req: Request, res: Response) => {
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("X-Accel-Buffering", "no");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders();

  try {
    const upstream = await fetch(`${BROWSER_AGENT_URL}/stream/${req.params.taskId}`);
    if (!upstream.body) {
      res.write(`data: ${JSON.stringify({ type: "error", error: "No stream body" })}\n\n`);
      res.end();
      return;
    }
    const reader = upstream.body.getReader();
    req.on("close", () => reader.cancel());
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      res.write(new TextDecoder().decode(value));
    }
  } catch (err) {
    res.write(`data: ${JSON.stringify({ type: "error", error: "Browser agent unavailable" })}\n\n`);
  } finally {
    res.end();
  }
});

export default router;
