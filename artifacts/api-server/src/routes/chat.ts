import { Router, type IRouter, type Request, type Response } from "express";
import { SendMessageBody } from "@workspace/api-zod";

const router: IRouter = Router();

const NOVA_API_KEY = process.env.NOVA_API_KEY;
const NOVA_BASE_URL = "https://api.nova.amazon.com/v1";

router.post("/chat", async (req: Request, res: Response) => {
  if (!NOVA_API_KEY) {
    res.status(500).json({
      error: "configuration_error",
      message: "NOVA_API_KEY is not configured. Please add it to your secrets.",
    });
    return;
  }

  const parseResult = SendMessageBody.safeParse(req.body);
  if (!parseResult.success) {
    res.status(400).json({
      error: "invalid_request",
      message: parseResult.error.message,
    });
    return;
  }

  const { messages, model = "nova-2-lite-v1" } = parseResult.data;

  try {
    const response = await fetch(`${NOVA_BASE_URL}/chat/completions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${NOVA_API_KEY}`,
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
      console.error(`Nova API error ${response.status}:`, errorText);
      let errorData: { error?: { message?: string } } = {};
      try {
        errorData = JSON.parse(errorText);
      } catch {
        errorData = {};
      }
      res.status(response.status).json({
        error: "nova_api_error",
        message: errorData?.error?.message || errorText || `Nova API returned status ${response.status}`,
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
  } catch (err) {
    console.error("Nova API error:", err);
    res.status(500).json({
      error: "server_error",
      message: err instanceof Error ? err.message : "Unknown error occurred",
    });
  }
});

export default router;
