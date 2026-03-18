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
  const { messages, model = defaultModel } = parseResult.data;

  try {
    const response = await fetch(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model,
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
      const ollamaUrl = process.env.OLLAMA_URL ?? "http://localhost:11434";
      res.status(503).json({
        error: "llm_not_reachable",
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
  const { baseUrl, defaultModel, provider } = getLLMConfig();
  res.json({
    provider,
    baseUrl,
    models: [defaultModel, "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
  });
});

export default router;
