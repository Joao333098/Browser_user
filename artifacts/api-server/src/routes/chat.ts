import { Router, type IRouter, type Request, type Response } from "express";
import { SendMessageBody } from "@workspace/api-zod";

const router: IRouter = Router();

function getLLMConfig(): { baseUrl: string; apiKey: string; defaultModel: string; provider: string } {
  if (process.env.GROQ_API_KEY) {
    return {
      baseUrl: "https://api.groq.com/openai/v1",
      apiKey: process.env.GROQ_API_KEY,
      defaultModel: "llama-3.3-70b-versatile",
      provider: "Groq",
    };
  }
  return {
    baseUrl: (process.env.OLLAMA_URL ?? "http://localhost:11434").replace(/\/$/, "") + "/v1",
    apiKey: "ollama",
    defaultModel: "deepseek-v3.2",
    provider: "Ollama",
  };
}

router.post("/chat/stream", async (req: Request, res: Response) => {
  const parseResult = SendMessageBody.safeParse(req.body);
  if (!parseResult.success) {
    res.status(400).json({ error: "invalid_request", message: parseResult.error.message });
    return;
  }

  const { baseUrl, apiKey, defaultModel } = getLLMConfig();
  const { messages, model } = parseResult.data;
  const chosenModel = model || defaultModel;

  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("X-Accel-Buffering", "no");
  res.setHeader("Connection", "keep-alive");

  try {
    const upstream = await fetch(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model: chosenModel,
        messages,
        stream: true,
      }),
    });

    if (!upstream.ok) {
      const errorText = await upstream.text();
      let errorData: { error?: { message?: string } } = {};
      try { errorData = JSON.parse(errorText); } catch { /* noop */ }
      res.write(`data: ${JSON.stringify({ error: errorData?.error?.message || errorText })}\n\n`);
      res.end();
      return;
    }

    const reader = upstream.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed === "data: [DONE]") {
          if (trimmed === "data: [DONE]") {
            res.write("data: [DONE]\n\n");
          }
          continue;
        }
        if (trimmed.startsWith("data: ")) {
          res.write(trimmed + "\n\n");
        }
      }
    }

    res.end();
  } catch (err: any) {
    console.error("Stream error:", err);
    res.write(`data: ${JSON.stringify({ error: err.message || "Server error" })}\n\n`);
    res.end();
  }
});

router.post("/chat", async (req: Request, res: Response) => {
  const parseResult = SendMessageBody.safeParse(req.body);
  if (!parseResult.success) {
    res.status(400).json({
      error: "invalid_request",
      message: parseResult.error.message,
    });
    return;
  }

  const { baseUrl, apiKey, defaultModel } = getLLMConfig();
  const { messages, model } = parseResult.data;
  const chosenModel = model || defaultModel;

  try {
    const response = await fetch(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model: chosenModel,
        messages,
        stream: false,
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`LLM API error ${response.status}:`, errorText);
      let errorData: { error?: { message?: string } } = {};
      try {
        errorData = JSON.parse(errorText);
      } catch {
        errorData = {};
      }
      res.status(response.status).json({
        error: "llm_api_error",
        message: errorData?.error?.message || errorText || `LLM API returned status ${response.status}`,
      });
      return;
    }

    const data = await response.json() as {
      id: string;
      model: string;
      choices: Array<{
        message: { role: string; content: string };
        finish_reason: string;
      }>;
      usage: {
        prompt_tokens: number;
        completion_tokens: number;
        total_tokens: number;
      };
    };

    const choice = data.choices[0];
    res.json({
      id: data.id,
      content: choice.message.content,
      role: choice.message.role,
      model: data.model,
      usage: data.usage,
    });
  } catch (err: any) {
    console.error("LLM API error:", err);

    const isConnectionRefused =
      err?.cause?.code === "ECONNREFUSED" || err?.message?.includes("fetch failed");

    if (isConnectionRefused) {
      res.status(503).json({
        error: "llm_not_reachable",
        message: `Não foi possível conectar ao provedor de IA. Verifique se a variável GROQ_API_KEY está configurada corretamente.`,
      });
      return;
    }

    res.status(500).json({
      error: "server_error",
      message: err instanceof Error ? err.message : "Unknown error occurred",
    });
  }
});

router.get("/chat/config", (_req: Request, res: Response) => {
  const { baseUrl, defaultModel, provider } = getLLMConfig();
  res.json({
    provider,
    baseUrl,
    models: [defaultModel, "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
  });
});

export default router;
