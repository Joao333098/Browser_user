import asyncio
import base64
import json
import os
import re
import signal
import socket
import uuid
from datetime import datetime

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

CHROMIUM_PATH = (
    os.environ.get("CHROMIUM_PATH")
    or "/nix/store/qa9cnw4v5xkxyip6mb9kxqfq1z4x2dx1-chromium-138.0.7204.100/bin/chromium"
)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def _free_port(port: int) -> None:
    """Kill any process occupying the given port before startup."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return  # port is already free
        # Port is in use — find and kill the PID
        with open("/proc/net/tcp") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 4:
                    continue
                local = parts[1]
                hex_port = local.split(":")[1] if ":" in local else ""
                try:
                    if int(hex_port, 16) == port:
                        inode = parts[9]
                        # find PID by inode
                        for pid_dir in os.listdir("/proc"):
                            if not pid_dir.isdigit():
                                continue
                            try:
                                for fd in os.listdir(f"/proc/{pid_dir}/fd"):
                                    link = os.readlink(f"/proc/{pid_dir}/fd/{fd}")
                                    if f"socket:[{inode}]" in link:
                                        os.kill(int(pid_dir), signal.SIGKILL)
                                        return
                            except Exception:
                                pass
                except Exception:
                    pass
    except Exception:
        pass

SYSTEM_PROMPT = """You are a browser automation agent. You control a real web browser using Playwright.
You also have access to web search to look up answers when needed.

At each step you receive:
1. ELEMENTS: numbered refs (@e1, @e2, ...) for every interactive element on the page
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
- click_ref [ref]             — click an element by its ref (e.g. click_ref ["@e3"]) — MOST RELIABLE
- click [text]                — click by visible text as fallback if no ref available
- click_css [selector]        — click using a CSS selector as last resort
- fill [selector, text]       — clear and type text into input (use CSS selector)
- type [selector, text]       — type text without clearing
- press [key]                 — press a keyboard key (Enter, Tab, Escape, ArrowDown, etc)
- scroll [direction]          — scroll the page (up/down/left/right)
- wait [ms]                   — wait milliseconds (e.g. "2000")
- wait_text [text]            — wait for text to appear on page
- eval [js]                   — run JavaScript in the browser console
- skip_video                  — skip/bypass the current video on the page (no args)
- ask_human [question]        — pause and ask the human user a question (use for CAPTCHAs or when stuck)
- search_web [query]          — search the web for information to answer a question
- snapshot                    — get fresh element refs and accessibility tree (no args)
- screenshot                  — take a screenshot (no args)
- done [result]               — finish task with this result message
- fail [reason]               — stop if task is impossible

Rules:
- ALWAYS prefer click_ref using a ref from the ELEMENTS list (e.g. click_ref ["@e5"]) — it is the most reliable
- After navigation or page changes, the refs change — use snapshot to get fresh refs before clicking
- If click_ref fails, fallback to click_css with a CSS selector
- For pagination "Next page": look in ELEMENTS for the ref of the next-page link, then use click_ref
- CAPTCHA DETECTION IS CRITICAL: At every step, look carefully at the screenshot. If you see ANY of: reCAPTCHA checkbox, "I'm not a robot", hCaptcha, image CAPTCHA, text CAPTCHA, Cloudflare challenge, or any "prove you're human" element — immediately use ask_human. Never try to click or solve a CAPTCHA yourself.
- When asking about CAPTCHA, say exactly: "CAPTCHA detectado. Por favor resolva e diga o texto (se for CAPTCHA de texto) ou confirme 'ok' (se for checkbox)."
- After human responds, use fill or click_css to submit the CAPTCHA answer in the correct input field before continuing
- When you don't know an answer to a quiz/question: use search_web to find it
- When you see a video that must be watched: use skip_video to bypass it
- NEVER wait more than once in a row. If you used wait and the page still looks empty or incomplete, DO NOT wait again. Instead, look at the ELEMENTS list and SNAPSHOT to see what IS on the page and proceed immediately — even if it looks incomplete. Use snapshot to get fresh context, then act.
- If the page appears empty after a wait, check the ELEMENTS list carefully — interactive elements may already be present even if the page looks blank visually. Proceed with the task using whatever elements are available.
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




_http_client = httpx.AsyncClient(timeout=30.0)

tasks: dict[str, dict] = {}
task_queues: dict[str, asyncio.Queue] = {}
task_asyncio_tasks: dict[str, asyncio.Task] = {}
# human_input_futures: task_id -> asyncio.Future waiting for human response
human_input_futures: dict[str, asyncio.Future] = {}
# injected_queues: mid-task user instructions injected while agent is running
injected_queues: dict[str, asyncio.Queue] = {}
# task_pages: live playwright page object per running task (for real-time screenshots)
task_pages: dict = {}

MAX_CONCURRENT_SESSIONS = 3


VISION_MODELS: set[str] = {
    "meta-llama/llama-4-scout-17b-16e-instruct",
}


class RunRequest(BaseModel):
    task: str
    model: str = "llama-3.1-8b-instant"


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
    groq_api_key = os.environ.get("GROQ_API_KEY")
    if not groq_api_key:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY not configured.")

    running_count = sum(1 for t in tasks.values() if t["status"] == "running")
    if running_count >= MAX_CONCURRENT_SESSIONS:
        raise HTTPException(status_code=429, detail="Limite de sessões atingido. Aguarde a conclusão das tarefas em andamento.")

    task_id = str(uuid.uuid4())[:8]
    queue: asyncio.Queue = asyncio.Queue()
    task_queues[task_id] = queue
    injected_queues[task_id] = asyncio.Queue()
    tasks[task_id] = {
        "id": task_id,
        "task": request.task,
        "model": request.model,
        "status": "running",
        "started_at": datetime.now().isoformat(),
        "result": None,
        "error": None,
    }
    t = asyncio.create_task(_run_agent(task_id, request.task, request.model, groq_api_key, queue))
    task_asyncio_tasks[task_id] = t
    return {"task_id": task_id}


@app.post("/tasks/clear-stuck")
async def clear_stuck_tasks():
    """Mark all tasks stuck in 'running' state as failed and cancel their asyncio tasks."""
    cleared = []
    for task_id, task in tasks.items():
        if task["status"] == "running":
            t = task_asyncio_tasks.get(task_id)
            if t and not t.done():
                t.cancel()
            task["status"] = "failed"
            task["error"] = "Cancelado — sessão travada limpa pelo usuário."
            cleared.append(task_id)
            q = task_queues.get(task_id)
            if q:
                try:
                    await q.put({"type": "error", "error": "Sessão limpa.", "timestamp": datetime.now().isoformat()})
                    await q.put(None)
                except Exception:
                    pass
    return {"cleared": cleared, "count": len(cleared)}


@app.post("/tasks/{task_id}/stop")
async def stop_task(task_id: str):
    """Cancel a running browser agent task."""
    t = task_asyncio_tasks.get(task_id)
    if t and not t.done():
        t.cancel()
    if task_id in tasks:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = "Cancelado pelo usuário."
    q = task_queues.get(task_id)
    if q:
        await q.put({"type": "error", "error": "Cancelado pelo usuário.", "timestamp": datetime.now().isoformat()})
        await q.put(None)
    return {"ok": True}


@app.post("/tasks/{task_id}/respond")
async def human_respond(task_id: str, body: HumanInputRequest):
    """Endpoint called by the frontend when the user answers a human_input_required request."""
    if task_id not in human_input_futures:
        raise HTTPException(status_code=404, detail="No pending human input for this task")
    future = human_input_futures.pop(task_id)
    if not future.done():
        future.set_result(body.response)
    return {"ok": True}


@app.post("/tasks/{task_id}/inject")
async def inject_message(task_id: str, body: HumanInputRequest):
    """Inject an additional instruction into a running agent mid-task."""
    q = injected_queues.get(task_id)
    if not q:
        raise HTTPException(status_code=404, detail="Task not found or already finished")
    await q.put(body.response)
    return {"ok": True}


@app.get("/screenshot/{task_id}")
async def get_live_screenshot(task_id: str):
    """Take a real-time screenshot of the active browser page for this task."""
    page = task_pages.get(task_id)
    if not page:
        raise HTTPException(status_code=404, detail="No active browser for this task")
    try:
        shot_bytes = await page.screenshot(
            type="jpeg", quality=55, scale="css",
            clip={"x": 0, "y": 0, "width": 1280, "height": 720}
        )
        shot_b64 = base64.b64encode(shot_bytes).decode()
        return {"screenshot": shot_b64}
    except Exception:
        try:
            shot_bytes = await page.screenshot(type="jpeg", quality=55)
            shot_b64 = base64.b64encode(shot_bytes).decode()
            return {"screenshot": shot_b64}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


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


async def _search_duckduckgo(query: str) -> str:
    """Fetch real search results from DuckDuckGo HTML endpoint."""
    try:
        url = "https://html.duckduckgo.com/html/"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = await _http_client.post(url, data={"q": query, "b": "", "kl": "us-en"}, headers=headers, timeout=15)
        html = resp.text

        # Extract result snippets
        results = []
        # Match result blocks: title, url, snippet
        titles = re.findall(r'class="result__title"[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html, re.DOTALL)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</(?:a|td)>', html, re.DOTALL)

        for i, snippet in enumerate(snippets[:6]):
            clean = re.sub(r'<[^>]+>', '', snippet).strip()
            clean = re.sub(r'\s+', ' ', clean)
            if clean:
                title_text = ""
                if i < len(titles):
                    title_text = re.sub(r'<[^>]+>', '', titles[i][1]).strip()
                    title_text = re.sub(r'\s+', ' ', title_text)
                results.append(f"• {title_text}: {clean}" if title_text else f"• {clean}")

        if results:
            return f"Search results for '{query}':\n" + "\n".join(results)
        return f"(No results found for '{query}')"
    except Exception as e:
        return f"(Search error: {e})"


FALLBACK_MODELS = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"]


class DailyLimitExceeded(Exception):
    """Raised when the model's daily token limit (TPD) is fully exhausted."""


async def _ask_llm(api_key: str, model: str, messages: list, retries: int = 3, use_grounding: bool = False, queue: asyncio.Queue | None = None, task_id: str | None = None, current_url: str = "") -> str:
    import sys

    for attempt in range(retries):
        try:
            print(f"[LLM] Calling model={model} msgs={len(messages)} attempt={attempt+1}/{retries}", file=sys.stderr, flush=True)

            accumulated = ""

            async with _http_client.stream(
                "POST",
                f"{GROQ_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": 512,
                    "temperature": 0.2,
                    "stream": True,
                },
                timeout=30,
            ) as response:
                print(f"[LLM] Response status={response.status_code}", file=sys.stderr, flush=True)

                if response.status_code == 429:
                    wait = 15
                    try:
                        body = await response.aread()
                        err_json = json.loads(body)
                        m_msg = err_json.get("error", {}).get("message", "")
                        print(f"[LLM] Rate limit: {m_msg[:200]}", file=sys.stderr, flush=True)
                        if "per day (TPD)" in m_msg or "tokens per day" in m_msg.lower():
                            raise DailyLimitExceeded(f"Limite diário de tokens atingido para '{model}'.")
                        m = re.search(r"(\d+)\s*second", m_msg)
                        if m:
                            wait = int(m.group(1)) + 2
                    except DailyLimitExceeded:
                        raise
                    except Exception:
                        pass
                    wait = min(wait * (attempt + 1), 45)
                    print(f"[LLM] Waiting {wait}s before retry...", file=sys.stderr, flush=True)
                    await asyncio.sleep(wait)
                    continue

                if response.status_code != 200:
                    body = await response.aread()
                    body_text = body.decode()[:300]
                    print(f"[LLM] Error body: {body_text}", file=sys.stderr, flush=True)
                    raise Exception(f"HTTP Error {response.status_code}: {body_text}")

                thinking_buffer = ""
                last_think_emit = 0.0

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line == "data: [DONE]":
                        continue
                    if line.startswith("data: "):
                        try:
                            chunk = json.loads(line[6:])
                            delta = chunk["choices"][0]["delta"].get("content", "")
                            if delta:
                                accumulated += delta
                                thinking_buffer += delta

                                now = asyncio.get_event_loop().time()
                                if queue and task_id and (now - last_think_emit) > 0.15 and len(thinking_buffer) >= 20:
                                    await queue.put({
                                        "type": "thinking",
                                        "text": accumulated,
                                        "url": current_url,
                                        "timestamp": datetime.now().isoformat(),
                                    })
                                    thinking_buffer = ""
                                    last_think_emit = now
                        except Exception:
                            pass

            if accumulated:
                print(f"[LLM] Got response ({len(accumulated)} chars)", file=sys.stderr, flush=True)
                return accumulated

            print(f"[WARN] No content in streamed response", file=sys.stderr, flush=True)
            await asyncio.sleep(1 * (attempt + 1))

        except DailyLimitExceeded:
            raise
        except Exception as e:
            print(f"[LLM] Exception attempt={attempt+1}: {e}", file=sys.stderr, flush=True)
            if attempt == retries - 1:
                raise
            await asyncio.sleep(2 * (attempt + 1))

    raise Exception("Rate limit or empty response: maximum retries exceeded")


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


async def _get_snapshot_with_refs(page, ref_store: dict) -> str:
    """
    Assign @e1, @e2, ... refs to every interactive element on the page.
    Injects data-agent-ref attributes so click_ref can locate them reliably.
    Returns a compact text representation for the LLM.
    """
    try:
        elements = await page.evaluate("""
        () => {
            // Remove stale refs from previous snapshot
            document.querySelectorAll('[data-agent-ref]').forEach(el => el.removeAttribute('data-agent-ref'));

            const SELECTORS = [
                'a[href]', 'button', 'input', 'select', 'textarea',
                '[role="button"]', '[role="link"]', '[role="menuitem"]',
                '[role="option"]', '[role="tab"]', '[role="checkbox"]',
                '[role="radio"]', '[role="switch"]', '[tabindex]',
                '[onclick]', 'label[for]', 'summary'
            ].join(', ');

            const seen = new Set();
            const results = [];
            let idx = 1;

            document.querySelectorAll(SELECTORS).forEach(el => {
                // Must be visible
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) return;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return;

                // Deduplicate by a signature
                const tag = el.tagName.toLowerCase();
                const label = (
                    el.getAttribute('aria-label') ||
                    el.getAttribute('title') ||
                    el.getAttribute('alt') ||
                    el.value ||
                    el.innerText ||
                    el.textContent || ''
                ).trim().replace(/\\s+/g, ' ').slice(0, 80);
                const type = el.getAttribute('type') || '';
                const sig = `${tag}|${type}|${label}`;
                if (seen.has(sig)) return;
                seen.add(sig);

                const ref = '@e' + idx++;
                el.setAttribute('data-agent-ref', ref);

                // Build compact description
                let desc = `[${tag}`;
                if (type) desc += ` type="${type}"`;
                const role = el.getAttribute('role');
                if (role) desc += ` role="${role}"`;
                desc += ']';

                if (label) desc += ` "${label}"`;

                const ariaLabel = el.getAttribute('aria-label');
                if (ariaLabel && ariaLabel !== label) desc += ` aria-label="${ariaLabel}"`;

                const placeholder = el.getAttribute('placeholder');
                if (placeholder) desc += ` placeholder="${placeholder}"`;

                results.push({ ref, desc });
            });

            return results;
        }
        """)

        if not elements:
            ref_store.clear()
            return "ELEMENTS: (none found)"

        # Update ref_store with current refs
        ref_store.clear()
        lines = []
        for item in elements[:60]:  # Cap at 60 elements to save tokens
            ref_store[item["ref"]] = item["desc"]
            lines.append(f"{item['ref']} {item['desc']}")

        return "ELEMENTS:\n" + "\n".join(lines)
    except Exception as e:
        return f"ELEMENTS: (error: {e})"


async def _robust_click(page, text: str) -> None:
    """Try multiple strategies to click an element by its visible text."""
    text_clean = text.strip().strip('"').strip("'")
    text_lower = text_clean.lower()

    async def _try(coro):
        try:
            await coro
            await asyncio.sleep(0.4)
            return True
        except Exception:
            return False

    # 1. Exact role=button
    if await _try(page.get_by_role("button", name=text_clean).first.click(timeout=4000)):
        return
    # 2. Exact role=link
    if await _try(page.get_by_role("link", name=text_clean).first.click(timeout=4000)):
        return
    # 3. Case-insensitive role=button
    if await _try(page.get_by_role("button", name=text_clean, exact=False).first.click(timeout=4000)):
        return
    # 4. Case-insensitive role=link
    if await _try(page.get_by_role("link", name=text_clean, exact=False).first.click(timeout=4000)):
        return
    # 5. get_by_text exact
    if await _try(page.get_by_text(text_clean, exact=True).first.click(timeout=4000)):
        return
    # 6. get_by_text partial
    if await _try(page.get_by_text(text_clean).first.click(timeout=4000)):
        return
    # 7. locator text=
    if await _try(page.locator(f"text={text_clean}").first.click(timeout=4000)):
        return
    # 8. get_by_label (for inputs/buttons with aria-label)
    if await _try(page.get_by_label(text_clean).first.click(timeout=4000)):
        return
    # 9. get_by_title
    if await _try(page.get_by_title(text_clean).first.click(timeout=4000)):
        return
    # 10. Comprehensive JS — searches ALL element types, checks innerText/aria-label/title/value, case-insensitive
    try:
        found = await page.evaluate(f"""
            () => {{
                const needle = {json.dumps(text_lower)};
                const all = Array.from(document.querySelectorAll(
                    'button, a, [role="button"], [role="link"], [role="menuitem"], [role="option"], [role="tab"], input[type="submit"], input[type="button"], li, span, div, td, th, label'
                ));
                // exact match first
                let el = all.find(e => {{
                    const t = (e.innerText || e.textContent || e.value || e.getAttribute('aria-label') || e.getAttribute('title') || '').trim().toLowerCase();
                    return t === needle;
                }});
                // partial match fallback
                if (!el) {{
                    el = all.find(e => {{
                        const t = (e.innerText || e.textContent || e.value || e.getAttribute('aria-label') || e.getAttribute('title') || '').trim().toLowerCase();
                        return t.includes(needle) || needle.includes(t) && t.length > 2;
                    }});
                }}
                if (el) {{
                    el.scrollIntoView({{block:'center', behavior:'instant'}});
                    el.click();
                    return true;
                }}
                return false;
            }}
        """)
        if found:
            await asyncio.sleep(0.5)
            return
    except Exception:
        pass

    # 11. XPath — searches by normalized text content
    xpath_exact = f"//*[normalize-space(text())={json.dumps(text_clean)} or normalize-space(@aria-label)={json.dumps(text_clean)} or normalize-space(@title)={json.dumps(text_clean)}]"
    if await _try(page.locator(f"xpath={xpath_exact}").first.click(timeout=4000)):
        return

    # 12. XPath case-insensitive via translate
    try:
        lc = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        uc = "abcdefghijklmnopqrstuvwxyz"
        xpath_ci = f"//*[contains(translate(normalize-space(text()),'{lc}','{uc}'),{json.dumps(text_lower)}) or contains(translate(normalize-space(@aria-label),'{lc}','{uc}'),{json.dumps(text_lower)})]"
        if await _try(page.locator(f"xpath={xpath_ci}").first.click(timeout=4000)):
            return
    except Exception:
        pass

    raise Exception(
        f"Could not click '{text_clean}' after all strategies. "
        f"TIP: Use click_css with a CSS selector (e.g. click_css [a[aria-label*='Next'], button.next-page, [data-testid='next']]) or use press [Enter] if a link is already focused."
    )


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


async def _wait_for_page_stable(page, queue: asyncio.Queue, task_id: str, max_checks: int = 5, interval: float = 1.5) -> str:
    """
    Wait for the page to finish loading using Playwright's built-in load states.
    Falls back gracefully if the page redirects or times out.
    Returns a base64 JPEG screenshot of the final page state.
    """
    # 1. Wait for DOM to be ready
    try:
        await asyncio.wait_for(page.wait_for_load_state("load"), timeout=8.0)
    except Exception:
        pass

    # 2. Wait for network to go quiet (catches XHR/fetch after DOM load)
    try:
        await asyncio.wait_for(page.wait_for_load_state("networkidle"), timeout=5.0)
    except Exception:
        pass

    # 3. Extra small pause for JS-rendered content
    await asyncio.sleep(1.0)

    # 4. Take one screenshot of the final state
    shot_b64 = ""
    try:
        shot_bytes = await page.screenshot(type="jpeg", quality=55, scale="css",
                                            clip={"x": 0, "y": 0, "width": 1280, "height": 720})
        shot_b64 = base64.b64encode(shot_bytes).decode()
    except Exception:
        try:
            shot_bytes = await page.screenshot(type="jpeg", quality=55)
            shot_b64 = base64.b64encode(shot_bytes).decode()
        except Exception:
            pass

    await queue.put({
        "type": "step",
        "step": "page_loaded",
        "thought": "Página carregada — pronto para continuar.",
        "action": f"Página carregada: {page.url}",
        "url": page.url,
        "screenshot": shot_b64,
        "timestamp": datetime.now().isoformat(),
    })

    return shot_b64


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


async def _detect_captcha(page) -> dict | None:
    """Detect CAPTCHAs on the page, return type + best input selector."""
    try:
        return await page.evaluate("""
        () => {
            const bodyText = (document.body && document.body.innerText || '').toLowerCase();

            // reCAPTCHA
            if (document.querySelector('iframe[src*="recaptcha"]') ||
                document.querySelector('.g-recaptcha') ||
                document.querySelector('[data-sitekey]')) {
                return { type: 'recaptcha', selector: null,
                         description: 'Google reCAPTCHA — marque a caixa "Não sou um robô" e confirme com "ok"' };
            }
            // hCaptcha
            if (document.querySelector('iframe[src*="hcaptcha"]') ||
                document.querySelector('.h-captcha')) {
                return { type: 'hcaptcha', selector: null,
                         description: 'hCaptcha — complete o desafio e confirme com "ok"' };
            }
            // Cloudflare Turnstile
            if (document.querySelector('iframe[src*="challenges.cloudflare"]') ||
                document.querySelector('.cf-turnstile')) {
                return { type: 'cloudflare', selector: null,
                         description: 'Cloudflare challenge — aguarde ou complete e confirme com "ok"' };
            }
            // Text/image CAPTCHA — look for input near the word captcha
            const allInputs = Array.from(document.querySelectorAll(
                'input[type="text"], input[type="number"], input:not([type])'
            ));
            for (const inp of allInputs) {
                const attrs = [
                    inp.getAttribute('placeholder') || '',
                    inp.getAttribute('aria-label') || '',
                    inp.getAttribute('name') || '',
                    inp.getAttribute('id') || '',
                    inp.getAttribute('autocomplete') || '',
                ].join(' ').toLowerCase();
                if (attrs.includes('captcha') || attrs.includes('security code') ||
                    attrs.includes('verification') || attrs.includes('verif')) {
                    const sel = inp.id ? '#' + inp.id :
                                inp.name ? 'input[name="' + inp.name + '"]' :
                                'input[placeholder*="' + (inp.getAttribute('placeholder') || 'captcha') + '"]';
                    return { type: 'text', selector: sel,
                             description: 'CAPTCHA de texto — olhe a imagem no screenshot e digite a resposta' };
                }
            }
            // Image CAPTCHA with img[src*=captcha] + nearby input
            if (document.querySelector('img[src*="captcha"], img[alt*="captcha"], img[alt*="CAPTCHA"]')) {
                const inp = document.querySelector('input[type="text"], input:not([type])');
                if (inp) {
                    const sel = inp.id ? '#' + inp.id : inp.name ? 'input[name="' + inp.name + '"]' : 'input[type="text"]';
                    return { type: 'image', selector: sel,
                             description: 'CAPTCHA de imagem — leia as letras/números na imagem e escreva aqui' };
                }
            }
            // Generic text heuristic
            if (bodyText.includes('captcha') || bodyText.includes('i am not a robot') ||
                bodyText.includes('prove you are human') || bodyText.includes('verify you are human') ||
                bodyText.includes('não sou um robô') || bodyText.includes('verificação')) {
                const inp = document.querySelector('input[type="text"], input:not([type])');
                const sel = inp && inp.id ? '#' + inp.id : inp && inp.name ? 'input[name="' + inp.name + '"]' : null;
                return { type: 'unknown', selector: sel,
                         description: 'CAPTCHA detectado — resolva conforme a imagem e responda' };
            }
            return null;
        }
        """)
    except Exception:
        return None


async def _run_agent(task_id: str, task: str, model: str, api_key: str, queue: asyncio.Queue):
    import sys
    from playwright.async_api import async_playwright

    playwright_ctx = None
    browser = None

    print(f"[AGENT] Starting task {task_id} model={model}", file=sys.stderr, flush=True)

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
        task_pages[task_id] = page

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
        ref_store: dict = {}
        consecutive_waits = 0
        last_screenshot_step = 0  # Track when we last sent a screenshot to the LLM
        force_screenshot = True   # Always send screenshot on first step and after navigations

        while True:
            step += 1

            current_url = page.url

            # Clickable elements with refs
            clickable = await _get_snapshot_with_refs(page, ref_store)

            # Accessibility snapshot
            try:
                ax_tree = await page.accessibility.snapshot()
                snapshot = _accessibility_to_text(ax_tree)[:4000]
            except Exception:
                snapshot = "(could not get accessibility tree)"

            # Screenshot — always (JPEG at reduced size to keep tokens low)
            screenshot_b64 = None
            try:
                screenshot_bytes = await page.screenshot(type="jpeg", quality=60, scale="css", clip={"x": 0, "y": 0, "width": 1280, "height": 720})
                screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
            except Exception:
                try:
                    screenshot_bytes = await page.screenshot(type="jpeg", quality=60)
                    screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
                except Exception:
                    pass

            # Proactive CAPTCHA detection — intercept before LLM
            captcha = await _detect_captcha(page)
            if captcha:
                cap_type = captcha.get("type", "unknown")
                cap_desc = captcha.get("description", "CAPTCHA detectado")
                cap_sel  = captcha.get("selector")

                await queue.put({
                    "type": "step",
                    "step": step,
                    "thought": f"CAPTCHA detectado: {cap_desc}",
                    "action": "Aguardando resolução do CAPTCHA pelo usuário",
                    "url": current_url,
                    "screenshot": screenshot_b64,
                    "timestamp": datetime.now().isoformat(),
                })

                human_response = await _wait_for_human(
                    task_id,
                    f"🔒 {cap_desc}\n\nOlhe o screenshot e responda:\n"
                    + ("— Digite o texto/código do CAPTCHA" if cap_type in ("text", "image", "unknown") else
                       "— Resolva o CAPTCHA no seu navegador e escreva 'ok' quando terminar"),
                    queue,
                    screenshot_b64,
                )

                if cap_sel and cap_type in ("text", "image", "unknown") and human_response.strip().lower() not in ("ok", ""):
                    try:
                        await page.fill(cap_sel, human_response.strip(), timeout=6000)
                        await asyncio.sleep(0.3)
                        await page.keyboard.press("Enter")
                        await asyncio.sleep(1)
                    except Exception as e:
                        messages.append({
                            "role": "user",
                            "content": f"Tentei preencher o CAPTCHA ({cap_sel}) com '{human_response}' mas ocorreu erro: {e}. Tente usar fill ou click_css para submeter."
                        })

                messages.append({
                    "role": "user",
                    "content": f"CAPTCHA resolvido pelo usuário. Resposta: '{human_response}'. "
                               + (f"Já preenchi o campo '{cap_sel}' e pressionei Enter. " if cap_sel else "")
                               + "Continue a tarefa do ponto onde parou."
                })
                continue

            # Build LLM message — screenshots only for vision-capable models
            is_vision = model in VISION_MODELS
            send_screenshot = bool(is_vision and screenshot_b64 and (force_screenshot or step - last_screenshot_step >= 3))
            if send_screenshot:
                last_screenshot_step = step
                force_screenshot = False

            user_content: list = [
                {"type": "text", "text": f"Step {step}\nURL: {current_url}\n\n{clickable}\n\nSNAPSHOT:\n{snapshot}"}
            ]
            if send_screenshot:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"},
                })

            messages.append({"role": "user", "content": user_content})

            if len(messages) > 15:
                messages = messages[:2] + messages[-12:]

            # Strip images from all messages except the last user message
            # Groq supports max 5 images — keep only the most recent screenshot
            def _flatten_content(content):
                """Convert list content to plain string for text-only models."""
                if isinstance(content, list):
                    parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                    return "\n".join(parts)
                return content

            def _strip_images(msgs: list) -> list:
                result = []
                for i, msg in enumerate(msgs):
                    is_last_user = (i == len(msgs) - 1 and msg["role"] == "user")
                    if not isinstance(msg.get("content"), list):
                        result.append(msg)
                    elif is_vision and is_last_user:
                        # Vision model: keep list with images for last user message
                        result.append(msg)
                    elif is_vision:
                        # Vision model: strip images from non-last messages, keep list format
                        text_only = [p for p in msg["content"] if p.get("type") == "text"]
                        result.append({**msg, "content": text_only if text_only else msg["content"]})
                    else:
                        # Text-only model: flatten all list content to plain strings
                        result.append({**msg, "content": _flatten_content(msg["content"])})
                return result

            # Check for any mid-task instructions injected by the user
            inj_q = injected_queues.get(task_id)
            if inj_q:
                while True:
                    try:
                        injected_msg = inj_q.get_nowait()
                        messages.append({"role": "user", "content": f"[Instrução adicional do usuário]: {injected_msg}"})
                        await queue.put({
                            "type": "step",
                            "step": step,
                            "thought": f"Instrução recebida: {injected_msg}",
                            "action": "Nova instrução do usuário recebida",
                            "url": current_url,
                            "screenshot": screenshot_b64,
                            "timestamp": datetime.now().isoformat(),
                        })
                    except asyncio.QueueEmpty:
                        break

            # Notify UI the agent is thinking — always send current screenshot so user sees live view
            await queue.put({
                "type": "step",
                "step": f"thinking_{step}",
                "thought": "Analisando a página e decidindo a próxima ação…",
                "action": "Pensando…",
                "url": current_url,
                "screenshot": screenshot_b64,
                "timestamp": datetime.now().isoformat(),
            })

            # Ask LLM — auto-fallback to FALLBACK_MODEL if daily limit is exhausted
            try:
                raw_response = await _ask_llm(api_key, model, _strip_images(messages), queue=queue, task_id=task_id, current_url=current_url)
            except DailyLimitExceeded as e:
                # Try each fallback model in order
                switched = False
                for fallback in FALLBACK_MODELS:
                    if fallback != model:
                        old_model = model
                        model = fallback
                        is_vision = model in VISION_MODELS
                        print(f"[AGENT] Daily limit hit for {old_model}, switching to {model}", file=sys.stderr, flush=True)
                        await queue.put({
                            "type": "step",
                            "step": step,
                            "thought": f"Limite diário do modelo '{old_model}' atingido. Trocando automaticamente para '{model}'.",
                            "action": f"Trocando para {model}",
                            "url": current_url,
                            "screenshot": screenshot_b64,
                            "timestamp": datetime.now().isoformat(),
                        })
                        try:
                            raw_response = await _ask_llm(api_key, model, _strip_images(messages), queue=queue, task_id=task_id, current_url=current_url)
                            switched = True
                            break
                        except DailyLimitExceeded:
                            continue
                        except Exception as e2:
                            error_detail = str(e2)
                            await queue.put({
                                "type": "error",
                                "error": f"Erro no modelo de fallback '{model}': {error_detail}",
                                "timestamp": datetime.now().isoformat(),
                            })
                            tasks[task_id]["status"] = "failed"
                            tasks[task_id]["error"] = error_detail
                            return
                if not switched:
                    error_detail = "Limite diário atingido em todos os modelos. Tente novamente amanhã."
                    await queue.put({
                        "type": "error",
                        "error": error_detail,
                        "timestamp": datetime.now().isoformat(),
                    })
                    tasks[task_id]["status"] = "failed"
                    tasks[task_id]["error"] = error_detail
                    return
            except Exception as e:
                error_detail = str(e)
                await queue.put({
                    "type": "error",
                    "error": f"Falha na chamada ao modelo '{model}': {error_detail}",
                    "timestamp": datetime.now().isoformat(),
                })
                tasks[task_id]["status"] = "failed"
                tasks[task_id]["error"] = error_detail
                return

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
                    # Wait for page to fully stabilise — takes screenshot every 5 s
                    await queue.put({
                        "type": "step",
                        "step": step,
                        "thought": f"Navegando para {url} — aguardando a página carregar completamente…",
                        "action": "Aguardando carregamento da página",
                        "url": page.url,
                        "screenshot": screenshot_b64,
                        "timestamp": datetime.now().isoformat(),
                    })
                    screenshot_b64 = await _wait_for_page_stable(page, queue, task_id)
                    force_screenshot = True  # Always show the new page to the LLM

                elif action == "click":
                    # Robust click by visible text
                    text = args[0] if args else ""
                    url_before = page.url
                    await _robust_click(page, text)
                    # If a navigation was triggered, wait for the new page to stabilise
                    await asyncio.sleep(1.0)
                    if page.url != url_before:
                        screenshot_b64 = await _wait_for_page_stable(page, queue, task_id)
                        force_screenshot = True

                elif action == "click_ref":
                    ref = args[0] if args else ""
                    url_before = page.url
                    clicked = False
                    # Strategy 1: data-agent-ref attribute (most reliable)
                    try:
                        await page.click(f"[data-agent-ref='{ref}']", timeout=5000)
                        clicked = True
                    except Exception:
                        pass
                    if not clicked:
                        # Strategy 2: JS direct click via data-agent-ref (catches shadow DOM edge cases)
                        try:
                            found = await page.evaluate(f"""
                                () => {{
                                    const el = document.querySelector("[data-agent-ref='{ref}']");
                                    if (el) {{ el.scrollIntoView({{block:'center',behavior:'instant'}}); el.click(); return true; }}
                                    return false;
                                }}
                            """)
                            if found:
                                clicked = True
                        except Exception:
                            pass
                    if not clicked:
                        # Strategy 3: use stored description to extract text and click robustly
                        desc = ref_store.get(ref, "")
                        if desc:
                            # Extract text label from description like [button] "Label text"
                            m = re.search(r'"([^"]+)"', desc)
                            label = m.group(1) if m else desc.split("]")[-1].strip()
                            if label:
                                await _robust_click(page, label)
                                clicked = True
                        if not clicked:
                            raise Exception(
                                f"Could not click '{ref}' after all strategies. "
                                f"TIP: Use click_css with a CSS selector or use snapshot to get fresh refs."
                            )
                    await asyncio.sleep(1.0)
                    if page.url != url_before:
                        screenshot_b64 = await _wait_for_page_stable(page, queue, task_id)
                        force_screenshot = True
                    else:
                        await asyncio.sleep(0.5)

                elif action == "click_css":
                    # Click by CSS selector as fallback
                    sel = args[0] if args else ""
                    url_before = page.url
                    await page.click(sel, timeout=10000)
                    await asyncio.sleep(1.0)
                    if page.url != url_before:
                        screenshot_b64 = await _wait_for_page_stable(page, queue, task_id)
                        force_screenshot = True
                    else:
                        await asyncio.sleep(0.5)

                elif action == "search_web":
                    query = args[0] if args else ""
                    await queue.put({
                        "type": "step",
                        "step": step,
                        "thought": f"Buscando na web: {query}",
                        "action": f"Pesquisando no DuckDuckGo: {query}",
                        "url": current_url,
                        "screenshot": None,
                        "timestamp": datetime.now().isoformat(),
                    })
                    search_result = await _search_duckduckgo(query)
                    messages.append({
                        "role": "user",
                        "content": f"{search_result}\n\nUse these real search results to answer the question and continue the task."
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
                    # Take a fresh screenshot after waiting so LLM sees current state
                    try:
                        wait_shot_bytes = await page.screenshot(type="jpeg", quality=60)
                        screenshot_b64 = base64.b64encode(wait_shot_bytes).decode()
                    except Exception:
                        pass

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

            # Track consecutive waits and break the loop if stuck
            if action == "wait":
                consecutive_waits += 1
            else:
                consecutive_waits = 0

            if consecutive_waits >= 2:
                consecutive_waits = 0
                messages.append({
                    "role": "user",
                    "content": (
                        "AVISO DO SISTEMA: Você esperou várias vezes seguidas. "
                        "NÃO espere mais. Olhe os ELEMENTS e o SNAPSHOT da próxima iteração e PROSSIGA IMEDIATAMENTE com a tarefa, "
                        "mesmo que a página pareça vazia ou incompleta. "
                        "Use os elementos disponíveis ou tente fill/click_css diretamente."
                    )
                })

        tasks[task_id]["status"] = "completed"
        tasks[task_id]["result"] = "Tarefa concluída."
        await queue.put({
            "type": "done",
            "result": "Tarefa concluída.",
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
        # Always clean up status so stuck "running" tasks don't block new ones
        if task_id in tasks and tasks[task_id]["status"] == "running":
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = "Tarefa encerrada inesperadamente."
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
        task_pages.pop(task_id, None)
        # Signal end of stream
        try:
            await queue.put(None)
        except Exception:
            pass


@app.api_route("/mobile/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def mobile_proxy(path: str, request: Request):
    target_url = f"{MOBILE_VITE_URL}/mobile/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    try:
        body = await request.body()
        resp = await _http_client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
            follow_redirects=True,
        )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
        )
    except Exception:
        await asyncio.sleep(0.5)
        return Response(content=b"<html><body><p>A carregar...</p><script>setTimeout(()=>location.reload(),800)</script></body></html>", status_code=200, media_type="text/html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    _free_port(port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
