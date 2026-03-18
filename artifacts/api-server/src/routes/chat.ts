import { Router, type IRouter, type Request, type Response } from "express";
import { SendMessageBody } from "@workspace/api-zod";

const router: IRouter = Router();

const OLLAMA_BASE_URL = process.env.OLLAMA_BASE_URL ?? "http://localhost:11434";

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

  try {
    const response = await fetch(`${OLLAMA_BASE_URL}/api/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
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
      let errorData: { error?: { message?: string } | string } = {};
      try {
        errorData = JSON.parse(errorText);
      } catch {
        errorData = {};
      }
      const parsedError = typeof errorData?.error === "string"
        ? errorData.error
        : errorData?.error?.message;
      res.status(response.status).json({
        error: "ollama_api_error",
        message: parsedError || errorText || `Ollama API returned status ${response.status}`,
      });
      return;
    }

    const data = await response.json() as {
      model: string;
      created_at: string;
      message: { role: string; content: string };
      prompt_eval_count?: number;
      eval_count?: number;
    };

    const promptTokens = data.prompt_eval_count ?? 0;
    const completionTokens = data.eval_count ?? 0;

    res.json({
      id: crypto.randomUUID(),
      content: data.message.content,
      role: data.message.role,
      model: data.model,
      usage: {
        prompt_tokens: promptTokens,
        completion_tokens: completionTokens,
        total_tokens: promptTokens + completionTokens,
      },
    });
  } catch (err) {
    console.error("Ollama API error:", err);
    res.status(500).json({
      error: "server_error",
      message: err instanceof Error
        ? `${err.message}. Ensure Ollama is running at ${OLLAMA_BASE_URL}.`
        : "Unknown error occurred",
    });
  }
});

export default router;
