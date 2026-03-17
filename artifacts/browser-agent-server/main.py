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
You also have access to web search (nova_grounding) to look up answers when needed.

At each step you receive:
1. CLICKABLE ELEMENTS: list of buttons/links on the page with their exact text
2. SNAPSHOT: the accessibility tree of the current page
3. SCREENSHOT: a base64 PNG of what the browser currently shows

You must respond with ONLY a JSON object (no markdown, no code fences) in exactly this format:
{
  "thought": "brief reasoning about what to do next",
  "action": "<command>",
  "args": ["<arg1>", "<arg2>"],
  "description": "human-readable description of this step"
}

Available commands and args:
- navigate [url]              — go to a URL
- click [text]                — click a button or link by its EXACT visible text (e.g. "Next", "Submit", "Continue")
- click_css [selector]        — click using a CSS selector (e.g. button[type=submit], #btn-next)
- fill [selector, text]       — clear and type text into input (use CSS selector for the field)
- type [selector, text]       — type text without clearing
- press [key]                 — press a keyboard key (Enter, Tab, Escape, ArrowDown, etc)
- scroll [direction]          — scroll the page (up/down/left/right)
- wait [ms]                   — wait milliseconds (e.g. "2000")
- wait_text [text]            — wait for text to appear on page
- eval [js]                   — run JavaScript in the browser console
- skip_video                  — skip/bypass the current video on the page (no args)
- ask_human [question]        — pause and ask the human user a question (use for CAPTCHAs or when stuck)
- search_web [query]          — search the web for information to answer a question
- snapshot                    — get fresh accessibility tree (no args)
- screenshot                  — take a screenshot (no args)
- done [result]               — finish task with this result message
- fail [reason]               — stop if task is impossible

Rules:
- ALWAYS prefer click with the button's exact visible text (e.g. click ["Next"], click ["Submit"], click ["Continue"])
- Only use click_css when click by text fails
- For CAPTCHA: use ask_human, the screenshot will be sent to the user automatically
- When you don't know an answer to a quiz/question: use search_web to find it
- When you see a video that must be watched: use skip_video to bypass it
- Keep thought brief, description clear and human-friendly
- ONLY output valid JSON — no extra text"""

# JavaScript snippets for video skipping
VIDEO_SKIP_SCRIPTS = [
    # Generic: skip all videos
    """
    (function() {
        var videos = document.querySelectorAll('video');
        var skipped = 0;
        videos.forEach(function(v) {
            try {
                v.muted = true;
                if (v.duration && isFinite(v.duration)) {
                    v.currentTime = v.duration - 0.1;
                }
                v.playbackRate = 16;
                skipped++;
            } catch(e) {}
        });
        return 'Skipped ' + skipped + ' video(s)';
    })()
    """,
    # Edgenuity / ITS Learning specific
    """
    (function() {
        try {
            // Try Edgenuity skip
            if (window.player && window.player.duration) {
                window.player.seek(window.player.duration);
                return 'Edgenuity player skipped';
            }
        } catch(e) {}
        try {
            // Try jwplayer
            if (typeof jwplayer !== 'undefined') {
                var p = jwplayer();
                p.seek(p.getDuration());
                return 'JWPlayer skipped';
            }
        } catch(e) {}
        try {
            // Try VideoJS
            if (typeof videojs !== 'undefined') {
                var players = Object.values(videojs.getPlayers());
                players.forEach(function(p) { p.currentTime(p.duration()); });
                return 'VideoJS skipped';
            }
        } catch(e) {}
        // Dispatch ended events on all videos
        document.querySelectorAll('video').forEach(function(v) {
            try {
                v.dispatchEvent(new Event('ended'));
                v.currentTime = v.duration || 99999;
            } catch(e) {}
        });
        return 'Dispatched ended events';
    })()
    """,
]


app = FastAPI(title="Browser Agent Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

tasks: dict[str, dict] = {}
task_queues: dict[str, asyncio.Queue] = {}
# human_input_futures: task_id -> asyncio.Future waiting for human response
human_input_futures: dict[str, asyncio.Future] = {}


class RunRequest(BaseModel):
    task: str
    model: str = "nova-pro-v1"


class HumanInputRequest(BaseModel):
    response: str


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
        raise HTTPException(status_code=500, detail="NOVA_API_KEY not configured.")

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


@app.post("/tasks/{task_id}/respond")
async def human_respond(task_id: str, body: HumanInputRequest):
    """Endpoint called by the frontend when the user answers a human_input_required request."""
    if task_id not in human_input_futures:
        raise HTTPException(status_code=404, detail="No pending human input for this task")
    future = human_input_futures.pop(task_id)
    if not future.done():
        future.set_result(body.response)
    return {"ok": True}


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
    import urllib.request
    import urllib.error

    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0.2,
        "system_tools": ["nova_grounding"],
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


async def _get_clickable_elements(page) -> str:
    """Extract all visible clickable elements (buttons, links) with their exact text."""
    try:
        result = await page.evaluate("""
        () => {
            const elements = [];
            const seen = new Set();
            const selectors = 'button, a, [role="button"], input[type="submit"], input[type="button"], [onclick]';
            document.querySelectorAll(selectors).forEach(el => {
                const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
                if (text && text.length < 100 && !seen.has(text)) {
                    seen.add(text);
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        elements.push(text);
                    }
                }
            });
            return elements;
        }
        """)
        if result:
            return "CLICKABLE ELEMENTS: " + " | ".join(f'"{t}"' for t in result[:30])
        return "CLICKABLE ELEMENTS: (none found)"
    except Exception:
        return "CLICKABLE ELEMENTS: (error)"


async def _robust_click(page, text: str) -> None:
    """Try multiple strategies to click an element by its visible text."""
    text_clean = text.strip().strip('"').strip("'")

    strategies = [
        # 1. Playwright get_by_role button
        lambda: page.get_by_role("button", name=text_clean).first.click(timeout=5000),
        # 2. get_by_role link
        lambda: page.get_by_role("link", name=text_clean).first.click(timeout=5000),
        # 3. get_by_text exact
        lambda: page.get_by_text(text_clean, exact=True).first.click(timeout=5000),
        # 4. get_by_text partial
        lambda: page.get_by_text(text_clean).first.click(timeout=5000),
        # 5. locator text=
        lambda: page.locator(f"text={text_clean}").first.click(timeout=5000),
        # 6. JS click on element containing text
        lambda: page.evaluate(f"""
            () => {{
                const els = Array.from(document.querySelectorAll('button, a, [role="button"], input[type="submit"]'));
                const el = els.find(e => (e.innerText || e.value || '').trim().includes({json.dumps(text_clean)}));
                if (el) {{ el.click(); return true; }}
                throw new Error('not found');
            }}
        """),
    ]

    last_err = None
    for strategy in strategies:
        try:
            await strategy()
            await asyncio.sleep(0.5)
            return
        except Exception as e:
            last_err = e
            continue

    raise Exception(f"Could not click '{text_clean}' after all strategies. Last error: {last_err}")


async def _wait_for_human(task_id: str, question: str, queue: asyncio.Queue, screenshot_b64: str | None) -> str:
    """Pause agent, emit human_input_required event, and wait for user response."""
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    human_input_futures[task_id] = future

    await queue.put({
        "type": "human_input_required",
        "question": question,
        "screenshot": screenshot_b64,
        "timestamp": datetime.now().isoformat(),
    })

    # Wait up to 5 minutes for human to respond
    try:
        response = await asyncio.wait_for(future, timeout=300)
        return str(response)
    except asyncio.TimeoutError:
        human_input_futures.pop(task_id, None)
        return "(sem resposta)"


async def _skip_video(page) -> str:
    """Try multiple strategies to skip video on the page."""
    results = []
    for script in VIDEO_SKIP_SCRIPTS:
        try:
            result = await page.evaluate(script)
            results.append(str(result))
        except Exception as e:
            results.append(f"erro: {e}")
    return "; ".join(results)


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

            current_url = page.url

            # Clickable elements
            clickable = await _get_clickable_elements(page)

            # Accessibility snapshot
            try:
                ax_tree = await page.accessibility.snapshot()
                snapshot = _accessibility_to_text(ax_tree)[:4000]
            except Exception:
                snapshot = "(could not get accessibility tree)"

            # Screenshot — always
            screenshot_b64 = None
            try:
                screenshot_bytes = await page.screenshot(type="png")
                screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
            except Exception:
                pass

            # Build LLM message — always include screenshot
            user_content: list = [
                {"type": "text", "text": f"Step {step}\nURL: {current_url}\n\n{clickable}\n\nSNAPSHOT:\n{snapshot}"}
            ]
            if screenshot_b64:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"},
                })

            messages.append({"role": "user", "content": user_content})

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

            # Execute action
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

                elif action == "ask_human":
                    question = args[0] if args else "O que devo fazer aqui?"
                    human_response = await _wait_for_human(task_id, question, queue, screenshot_b64)
                    # Emit confirmation that we got the response
                    await queue.put({
                        "type": "step",
                        "step": step,
                        "thought": f"Recebi a resposta do usuário: {human_response}",
                        "action": f"Resposta recebida — continuando a tarefa",
                        "url": current_url,
                        "screenshot": screenshot_b64,
                        "timestamp": datetime.now().isoformat(),
                    })
                    # Inject human response back into the conversation explicitly
                    messages.append({
                        "role": "user",
                        "content": (
                            f"The human answered your question '{question}' with: \"{human_response}\"\n"
                            f"Now immediately use this information to continue. "
                            f"If this was a CAPTCHA answer, use the 'fill' or 'type' action to enter \"{human_response}\" into the CAPTCHA input field on the page, then submit."
                        )
                    })

                elif action == "skip_video":
                    skip_result = await _skip_video(page)
                    await asyncio.sleep(1)
                    await queue.put({
                        "type": "step",
                        "step": step,
                        "thought": f"Resultado do skip: {skip_result}",
                        "action": "Vídeo pulado via JavaScript",
                        "url": current_url,
                        "screenshot": screenshot_b64,
                        "timestamp": datetime.now().isoformat(),
                    })

                elif action == "navigate":
                    url = args[0] if args else ""
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                elif action == "click":
                    # Robust click by visible text
                    text = args[0] if args else ""
                    await _robust_click(page, text)

                elif action == "click_css":
                    # Click by CSS selector as fallback
                    sel = args[0] if args else ""
                    await page.click(sel, timeout=10000)
                    await asyncio.sleep(0.5)

                elif action == "search_web":
                    # Web search is handled automatically by nova_grounding in the LLM call
                    # Just inject a prompt so the LLM uses its search results next step
                    query = args[0] if args else ""
                    messages.append({
                        "role": "user",
                        "content": f"Please search the web for: {query}\nUse the search results to determine the correct answer, then continue the task."
                    })

                elif action == "fill":
                    sel = args[0] if args else ""
                    text = args[1] if len(args) > 1 else ""
                    try:
                        await page.fill(sel, text, timeout=8000)
                    except Exception:
                        # fallback: try to find first visible text input
                        await page.evaluate(f"""
                        () => {{
                            const inputs = Array.from(document.querySelectorAll('input[type="text"], input:not([type]), input[type="email"], input[type="password"], textarea'));
                            const visible = inputs.find(i => i.offsetWidth > 0 && i.offsetHeight > 0);
                            if (visible) {{ visible.value = {json.dumps(text)}; visible.dispatchEvent(new Event('input', {{bubbles:true}})); visible.dispatchEvent(new Event('change', {{bubbles:true}})); }}
                        }}
                        """)
                    await asyncio.sleep(0.3)

                elif action == "type":
                    sel = args[0] if args else ""
                    text = args[1] if len(args) > 1 else ""
                    try:
                        await page.type(sel, text, timeout=8000)
                    except Exception:
                        await page.keyboard.type(text)
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
                    pass

                else:
                    pass

            except Exception as action_err:
                await queue.put({
                    "type": "step",
                    "step": step,
                    "thought": f"Erro ao executar '{action}': {action_err}",
                    "action": None,
                    "url": current_url,
                    "screenshot": screenshot_b64,
                    "timestamp": datetime.now().isoformat(),
                })

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
        human_input_futures.pop(task_id, None)
        await queue.put(None)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
