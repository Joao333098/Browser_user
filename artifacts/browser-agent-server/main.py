import asyncio
import base64
import json
import os
import uuid
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

CHROMIUM_PATH = (
    os.environ.get("CHROMIUM_PATH")
    or "/nix/store/qa9cnw4v5xkxyip6mb9kxqfq1z4x2dx1-chromium-138.0.7204.100/bin/chromium"
)

NOVA_BASE_URL = "https://api.nova.amazon.com/v1"

SYSTEM_PROMPT = """You are a browser automation agent. You control a real web browser using Playwright.

At each step you receive:
1. SNAPSHOT: the accessibility tree of the current page
2. SCREENSHOT: a base64 PNG of what the browser currently shows

You must respond with ONLY a JSON object (no markdown, no code fences) in exactly this format:
{
  "thought": "brief reasoning about what to do next",
  "action": "<command>",
  "args": ["<arg1>", "<arg2>"],
  "description": "human-readable description of this step"
}

Available commands and args:
- navigate [url]              — go to a URL
- click [selector]            — click an element by CSS selector or text
- fill [selector, text]       — clear and type text into input
- type [selector, text]       — type text without clearing
- press [key]                 — press a keyboard key (Enter, Tab, Escape, ArrowDown, etc)
- scroll [direction]          — scroll the page (up/down/left/right)
- wait [ms]                   — wait milliseconds (e.g. "2000" to wait 2 seconds)
- wait_text [text]            — wait for text to appear on page
- eval [js]                   — run JavaScript in the browser console
- snapshot                    — get fresh accessibility tree (no args)
- screenshot                  — take a screenshot (no args)
- done [result]               — finish task with this result message
- fail [reason]               — stop if task is impossible

Rules:
- Use specific CSS selectors (e.g. input[name="q"], button[type="submit"]) when possible
- For text-based clicks use: text=Button Label
- Keep thought brief, description clear and human-friendly
- ONLY output valid JSON — no extra text"""


app = FastAPI(title="Browser Agent Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

tasks: dict[str, dict] = {}
task_queues: dict[str, asyncio.Queue] = {}


class RunRequest(BaseModel):
    task: str
    model: str = "nova-pro-v1"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/tasks")
async def list_tasks():
    return list(tasks.values())


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks[task_id]


@app.post("/run")
async def run_task(request: RunRequest):
    nova_api_key = os.environ.get("NOVA_API_KEY")
    if not nova_api_key:
        raise HTTPException(status_code=500, detail="NOVA_API_KEY not configured. Please add it to your secrets.")

    task_id = str(uuid.uuid4())[:8]
    queue: asyncio.Queue = asyncio.Queue()
    task_queues[task_id] = queue
    tasks[task_id] = {
        "id": task_id,
        "task": request.task,
        "model": request.model,
        "status": "running",
        "started_at": datetime.now().isoformat(),
        "result": None,
        "error": None,
    }
    asyncio.create_task(_run_agent(task_id, request.task, request.model, nova_api_key, queue))
    return {"task_id": task_id}


@app.get("/stream/{task_id}")
async def stream_task(task_id: str):
    if task_id not in task_queues:
        raise HTTPException(status_code=404, detail="Task not found")

    async def event_gen():
        queue = task_queues[task_id]
        yield f"data: {json.dumps({'type': 'connected', 'task_id': task_id})}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=60.0)
                if event is None:
                    yield f"data: {json.dumps({'type': 'stream_end'})}\n\n"
                    break
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _ask_nova(api_key: str, model: str, messages: list, retries: int = 5) -> str:
    """Call Nova API with automatic retry on 429 rate-limit errors."""
    import urllib.request
    import urllib.error

    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0.2,
    }).encode()

    loop = asyncio.get_event_loop()

    for attempt in range(retries):
        req = urllib.request.Request(
            f"{NOVA_BASE_URL}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        def _do_request():
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read()), None
            except urllib.error.HTTPError as e:
                body_bytes = e.read()
                return None, (e.code, body_bytes.decode(errors="replace"))

        data, err = await loop.run_in_executor(None, _do_request)

        if err is None:
            return data["choices"][0]["message"]["content"]

        code, body_text = err
        if code == 429:
            wait = 20
            try:
                import re
                err_json = json.loads(body_text)
                msg = err_json.get("message", "")
                m = re.search(r"(\d+)\s*second", msg)
                if m:
                    wait = int(m.group(1)) + 2
            except Exception:
                pass
            wait = min(wait * (attempt + 1), 60)
            await asyncio.sleep(wait)
            continue

        raise Exception(f"HTTP Error {code}: {body_text[:200]}")

    raise Exception("Rate limit: maximum retries exceeded")


def _parse_action(text: str) -> dict | None:
    """Parse JSON action from LLM response, stripping code fences if needed."""
    text = text.strip()
    if text.startswith("```"):
        idx = text.find("\n")
        text = text[idx + 1:].strip() if idx >= 0 else text
        if text.endswith("```"):
            text = text[:-3].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except Exception:
        return None


def _accessibility_to_text(node: dict | None, indent: int = 0) -> str:
    """Convert Playwright accessibility snapshot to readable text."""
    if not node:
        return ""
    lines = []
    role = node.get("role", "")
    name = node.get("name", "")
    value = node.get("value", "")
    prefix = "  " * indent
    label = f"{role}"
    if name:
        label += f' "{name}"'
    if value:
        label += f' value="{value}"'
    lines.append(f"{prefix}{label}")
    for child in node.get("children", []):
        lines.append(_accessibility_to_text(child, indent + 1))
    return "\n".join(lines)


async def _run_agent(task_id: str, task: str, model: str, api_key: str, queue: asyncio.Queue):
    from playwright.async_api import async_playwright

    playwright_ctx = None
    browser = None

    try:
        playwright_ctx = await async_playwright().start()
        browser = await playwright_ctx.chromium.launch(
            executable_path=CHROMIUM_PATH,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--window-size=1280,720",
            ],
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 720})
        page = await context.new_page()

        await queue.put({
            "type": "started",
            "message": f"Agente iniciado: {task}",
            "timestamp": datetime.now().isoformat(),
        })

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Complete this task: {task}"},
        ]

        step = 0
        max_steps = 100

        while step < max_steps:
            step += 1

            # Get current URL
            current_url = page.url

            # Get accessibility snapshot
            try:
                ax_tree = await page.accessibility.snapshot()
                snapshot = _accessibility_to_text(ax_tree)[:5000]
            except Exception:
                snapshot = "(could not get accessibility tree)"

            # Take screenshot
            screenshot_b64 = None
            try:
                screenshot_bytes = await page.screenshot(type="png")
                screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
            except Exception:
                pass

            # Build LLM message (include image every 3 steps)
            include_image = screenshot_b64 and (step % 3 == 1 or step == 1)
            user_content: list = [
                {"type": "text", "text": f"Step {step}\nURL: {current_url}\n\nSNAPSHOT:\n{snapshot}"}
            ]
            if include_image:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"},
                })

            messages.append({"role": "user", "content": user_content})

            # Trim history: keep system + first user + last 12 messages
            if len(messages) > 15:
                messages = messages[:2] + messages[-12:]

            # Ask LLM
            try:
                raw_response = await _ask_nova(api_key, model, messages)
            except Exception as e:
                await queue.put({
                    "type": "step",
                    "step": step,
                    "thought": f"Erro LLM: {e}",
                    "action": "Falha permanente",
                    "url": current_url,
                    "screenshot": screenshot_b64,
                    "timestamp": datetime.now().isoformat(),
                })
                break

            messages.append({"role": "assistant", "content": raw_response})

            parsed = _parse_action(raw_response)
            if not parsed:
                await queue.put({
                    "type": "step",
                    "step": step,
                    "thought": "Resposta inválida do modelo, tentando novamente...",
                    "action": None,
                    "url": current_url,
                    "screenshot": screenshot_b64,
                    "timestamp": datetime.now().isoformat(),
                })
                continue

            thought = parsed.get("thought", "")
            action = parsed.get("action", "")
            args = parsed.get("args", [])
            description = parsed.get("description", action)

            # Emit step event
            await queue.put({
                "type": "step",
                "step": step,
                "thought": thought,
                "action": description,
                "url": current_url,
                "screenshot": screenshot_b64,
                "timestamp": datetime.now().isoformat(),
            })

            # Execute action using Playwright
            try:
                if action == "done":
                    result = args[0] if args else "Tarefa concluída."
                    tasks[task_id]["status"] = "completed"
                    tasks[task_id]["result"] = result
                    await queue.put({
                        "type": "done",
                        "result": result,
                        "steps": step,
                        "timestamp": datetime.now().isoformat(),
                    })
                    return

                elif action == "fail":
                    reason = args[0] if args else "Tarefa impossível."
                    raise Exception(reason)

                elif action == "navigate":
                    url = args[0] if args else ""
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                elif action == "click":
                    sel = args[0] if args else ""
                    await page.click(sel, timeout=10000)
                    await asyncio.sleep(0.5)

                elif action == "fill":
                    sel = args[0] if args else ""
                    text = args[1] if len(args) > 1 else ""
                    await page.fill(sel, text, timeout=10000)
                    await asyncio.sleep(0.3)

                elif action == "type":
                    sel = args[0] if args else ""
                    text = args[1] if len(args) > 1 else ""
                    await page.type(sel, text, timeout=10000)
                    await asyncio.sleep(0.3)

                elif action == "press":
                    key = args[0] if args else "Enter"
                    await page.keyboard.press(key)
                    await asyncio.sleep(0.3)

                elif action == "scroll":
                    direction = args[0] if args else "down"
                    scroll_map = {
                        "down": (0, 500),
                        "up": (0, -500),
                        "right": (500, 0),
                        "left": (-500, 0),
                    }
                    dx, dy = scroll_map.get(direction, (0, 500))
                    await page.evaluate(f"window.scrollBy({dx}, {dy})")

                elif action == "wait":
                    ms = int(args[0]) if args else 1000
                    await asyncio.sleep(ms / 1000)

                elif action == "wait_text":
                    text = args[0] if args else ""
                    await page.wait_for_selector(f"text={text}", timeout=30000)

                elif action == "eval":
                    js = args[0] if args else ""
                    await page.evaluate(js)
                    await asyncio.sleep(0.5)

                elif action in ("snapshot", "screenshot"):
                    pass  # already done at top of loop

                else:
                    pass  # Unknown action — continue

            except Exception as action_err:
                # Log action error but keep going
                await queue.put({
                    "type": "step",
                    "step": step,
                    "thought": f"Erro ao executar '{action}': {action_err}",
                    "action": None,
                    "url": current_url,
                    "screenshot": screenshot_b64,
                    "timestamp": datetime.now().isoformat(),
                })

        # Max steps reached
        tasks[task_id]["status"] = "completed"
        tasks[task_id]["result"] = "Limite de passos atingido."
        await queue.put({
            "type": "done",
            "result": "Limite de passos atingido.",
            "steps": step,
            "timestamp": datetime.now().isoformat(),
        })

    except Exception as e:
        error_msg = str(e)
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = error_msg
        await queue.put({
            "type": "error",
            "error": error_msg,
            "timestamp": datetime.now().isoformat(),
        })
    finally:
        try:
            if browser:
                await browser.close()
        except Exception:
            pass
        try:
            if playwright_ctx:
                await playwright_ctx.stop()
        except Exception:
            pass
        await queue.put(None)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
