import { Router, type IRouter, type Request, type Response } from "express";
import { SendMessageBody } from "@workspace/api-zod";

const router: IRouter = Router();

function getOllamaBaseUrl(): string {
  return (process.env.OLLAMA_URL ?? "http://localhost:11434").replace(/\/$/, "") + "/v1";
}

router.post("/chat", async (req: Request, res: Response) => {
  const parseResult = SendMessageBody.safeParse(req.body);
  if (!parseResult.success) {
    res.status(400).json({
      error: "invalid_request",
      message: parseResult.error.message,
    });
    return;
  }

  const { messages, model = "deepseek-v3.2" } = parseResult.data;
  const ollamaBaseUrl = getOllamaBaseUrl();

  try {
    const response = await fetch(`${ollamaBaseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer ollama",
      },
      body: JSON.stringify({
        model,
        messages,
        stream: false,
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`Ollama API error ${response.status}:`, errorText);
      let errorData: { error?: { message?: string } } = {};
      try {
        errorData = JSON.parse(errorText);
      } catch {
        errorData = {};
      }
      res.status(response.status).json({
        error: "ollama_api_error",
        message: errorData?.error?.message || errorText || `Ollama returned status ${response.status}`,
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
    console.error("Ollama API error:", err);

    const isConnectionRefused =
      err?.cause?.code === "ECONNREFUSED" || err?.message?.includes("fetch failed");

    if (isConnectionRefused) {
      const ollamaUrl = process.env.OLLAMA_URL ?? "http://localhost:11434";
      res.status(503).json({
        error: "ollama_not_reachable",
        message: `Não foi possível conectar ao Ollama em ${ollamaUrl}. Para usar o app: (1) Instale o Ollama em ollama.com, (2) Exponha-o via ngrok ou similar, (3) Configure a variável OLLAMA_URL com o endereço público. Exemplo: https://abc123.ngrok-free.app`,
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
  const ollamaUrl = process.env.OLLAMA_URL ?? "http://localhost:11434";
  res.json({
    ollamaUrl,
    models: ["deepseek-v3.2", "gemini-3-flash-preview", "kimi-k2.5"],
  });
});

export default router;
