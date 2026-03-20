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
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return
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


# ─── System prompt: batch planning (5 steps at a time) ───────────────────────

BATCH_SYSTEM_PROMPT = """You are a fast browser automation agent. You control a real Chromium browser via Playwright.

At each planning round you receive:
- URL: current page URL
- ELEMENTS: numbered refs (@e1, @e2, ...) for every interactive element
- SNAPSHOT: the accessibility tree
- Optional SCREENSHOT for vision models

You must respond with ONLY a JSON object (no markdown, no code fences):
{
  "thought": "brief observation of current state and overall plan",
  "steps": [
    {"action": "navigate", "args": ["https://example.com"], "description": "Go to example.com"},
    {"action": "click_ref", "args": ["@e3"], "description": "Click the login button"},
    {"action": "fill", "args": ["#email", "user@email.com"], "description": "Enter email"},
    {"action": "fill", "args": ["#password", "pass123"], "description": "Enter password"},
    {"action": "press", "args": ["Enter"], "description": "Submit login form"}
  ]
}

Plan UP TO 5 concrete steps based on what you currently see.

Available actions:
- navigate [url]              — go to a URL
- click_ref [ref]             — click by ref from ELEMENTS list — MOST RELIABLE
- click [text]                — click by visible text (fallback)
- click_css [selector]        — click by CSS selector (last resort)
- fill [selector, text]       — clear and fill an input
- type [selector, text]       — type without clearing
- press [key]                 — keyboard key (Enter, Tab, Escape, ArrowDown...)
- scroll [direction]          — scroll (up/down/left/right)
- wait [ms]                   — wait milliseconds
- eval [js]                   — run JavaScript in browser console
- skip_video                  — skip/bypass video on page
- ask_human [question]        — ask user (use for CAPTCHAs or impossible situations)
- search_web [query]          — search DuckDuckGo for information
- snapshot                    — refresh element refs
- screenshot                  — take screenshot
- done [result]               — task completed with this result
- fail [reason]               — task is impossible for this reason

Critical rules:
- ALWAYS prefer click_ref using refs from ELEMENTS list
- After navigate, plan at most 2 more steps (page layout changes after navigation)
- If you see a CAPTCHA: put ask_human as the ONLY step in the plan
- If task is done: put done as the ONLY step
- For videos to skip: use eval with JS to skip (e.g. set video.currentTime = video.duration)
- Search for answers online if you don't know them: use search_web
- Keep descriptions short, human-friendly, in Portuguese
- ONLY output valid JSON — no extra text, no markdown"""


VISION_MODELS: set[str] = {
    "meta-llama/llama-4-scout-17b-16e-instruct",
}

# Video skip JavaScript strategies
VIDEO_SKIP_SCRIPTS = [
    """(function() {
        var videos = document.querySelectorAll('video');
        var skipped = 0;
        videos.forEach(function(v) {
            try {
                v.muted = true;
                if (v.duration && isFinite(v.duration)) { v.currentTime = v.duration - 0.1; }
                v.playbackRate = 16;
                skipped++;
            } catch(e) {}
        });
        return 'Skipped ' + skipped + ' video(s)';
    })()""",
    """(function() {
        try {
            if (window.player && window.player.duration) {
                window.player.seek(window.player.duration);
                return 'Edgenuity player skipped';
            }
        } catch(e) {}
        try {
            if (typeof jwplayer !== 'undefined') {
                var p = jwplayer(); p.seek(p.getDuration());
                return 'JWPlayer skipped';
            }
        } catch(e) {}
        try {
            if (typeof videojs !== 'undefined') {
                Object.values(videojs.getPlayers()).forEach(function(p) { p.currentTime(p.duration()); });
                return 'VideoJS skipped';
            }
        } catch(e) {}
        document.querySelectorAll('video').forEach(function(v) {
            try { v.dispatchEvent(new Event('ended')); v.currentTime = v.duration || 99999; } catch(e) {}
        });
        return 'Dispatched ended events';
    })()""",
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
human_input_futures: dict[str, asyncio.Future] = {}
injected_queues: dict[str, asyncio.Queue] = {}
task_pages: dict = {}

MAX_CONCURRENT_SESSIONS = 3
FALLBACK_MODELS = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"]


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
        raise HTTPException(status_code=429, detail="Limite de sessões atingido.")

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
    cleared = []
    for task_id, task in tasks.items():
        if task["status"] == "running":
            t = task_asyncio_tasks.get(task_id)
            if t and not t.done():
                t.cancel()
            task["status"] = "failed"
            task["error"] = "Cancelado — sessão travada."
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
    if task_id not in human_input_futures:
        raise HTTPException(status_code=404, detail="No pending human input")
    future = human_input_futures.pop(task_id)
    if not future.done():
        future.set_result(body.response)
    return {"ok": True}


@app.post("/tasks/{task_id}/inject")
async def inject_message(task_id: str, body: HumanInputRequest):
    q = injected_queues.get(task_id)
    if not q:
        raise HTTPException(status_code=404, detail="Task not found")
    await q.put(body.response)
    return {"ok": True}


@app.get("/screenshot/{task_id}")
async def get_live_screenshot(task_id: str):
    page = task_pages.get(task_id)
    if not page:
        raise HTTPException(status_code=404, detail="No active browser")
    try:
        shot_bytes = await page.screenshot(type="jpeg", quality=55, scale="css",
                                            clip={"x": 0, "y": 0, "width": 1280, "height": 720})
        return {"screenshot": base64.b64encode(shot_bytes).decode()}
    except Exception:
        try:
            shot_bytes = await page.screenshot(type="jpeg", quality=55)
            return {"screenshot": base64.b64encode(shot_bytes).decode()}
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


# ─── DuckDuckGo search ────────────────────────────────────────────────────────

async def _search_duckduckgo(query: str) -> str:
    try:
        url = "https://html.duckduckgo.com/html/"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        }
        resp = await _http_client.post(url, data={"q": query, "b": "", "kl": "us-en"}, headers=headers, timeout=15)
        html = resp.text
        results = []
        titles = re.findall(r'class="result__title"[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html, re.DOTALL)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</(?:a|td)>', html, re.DOTALL)
        for i, snippet in enumerate(snippets[:6]):
            clean = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', snippet)).strip()
            if clean:
                title_text = ""
                if i < len(titles):
                    title_text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', titles[i][1])).strip()
                results.append(f"• {title_text}: {clean}" if title_text else f"• {clean}")
        return f"Search results for '{query}':\n" + "\n".join(results) if results else f"(No results for '{query}')"
    except Exception as e:
        return f"(Search error: {e})"


# ─── LLM caller with streaming + thinking events ──────────────────────────────

class DailyLimitExceeded(Exception):
    pass


async def _ask_llm(
    api_key: str,
    model: str,
    messages: list,
    retries: int = 3,
    queue: asyncio.Queue | None = None,
    task_id: str | None = None,
    current_url: str = "",
) -> str:
    import sys

    for attempt in range(retries):
        try:
            print(f"[LLM] model={model} msgs={len(messages)} attempt={attempt+1}", file=sys.stderr, flush=True)
            accumulated = ""
            thinking_buffer = ""
            last_emit = 0.0

            async with _http_client.stream(
                "POST",
                f"{GROQ_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": 800,
                    "temperature": 0.1,
                    "stream": True,
                },
                timeout=30,
            ) as response:
                if response.status_code == 429:
                    body = await response.aread()
                    msg = json.loads(body).get("error", {}).get("message", "")
                    if "per day (TPD)" in msg or "tokens per day" in msg.lower():
                        raise DailyLimitExceeded(f"Limite diário atingido para '{model}'.")
                    wait = 15
                    m = re.search(r"(\d+)\s*second", msg)
                    if m:
                        wait = int(m.group(1)) + 2
                    wait = min(wait * (attempt + 1), 45)
                    await asyncio.sleep(wait)
                    continue

                if response.status_code != 200:
                    body = await response.aread()
                    raise Exception(f"HTTP {response.status_code}: {body.decode()[:300]}")

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
                                if queue and task_id and (now - last_emit) > 0.1 and len(thinking_buffer) >= 15:
                                    await queue.put({
                                        "type": "thinking",
                                        "text": accumulated,
                                        "url": current_url,
                                        "timestamp": datetime.now().isoformat(),
                                    })
                                    thinking_buffer = ""
                                    last_emit = now
                        except Exception:
                            pass

            if accumulated:
                print(f"[LLM] OK ({len(accumulated)} chars)", file=sys.stderr, flush=True)
                return accumulated

            await asyncio.sleep(1 * (attempt + 1))

        except DailyLimitExceeded:
            raise
        except Exception as e:
            print(f"[LLM] Error attempt={attempt+1}: {e}", file=sys.stderr, flush=True)
            if attempt == retries - 1:
                raise
            await asyncio.sleep(2 * (attempt + 1))

    raise Exception("Max retries exceeded")


# ─── Parsing ──────────────────────────────────────────────────────────────────

def _parse_batch(text: str) -> dict | None:
    """Parse batch plan response: {"thought": ..., "steps": [...]}"""
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
        parsed = json.loads(text)
        if "steps" in parsed and isinstance(parsed["steps"], list):
            return parsed
        if "action" in parsed:
            return {"thought": parsed.get("thought", ""), "steps": [parsed]}
        return None
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
    label = role
    if name:
        label += f' "{name}"'
    if value:
        label += f' value="{value}"'
    lines.append(f"{prefix}{label}")
    for child in node.get("children", []):
        lines.append(_accessibility_to_text(child, indent + 1))
    return "\n".join(lines)


# ─── Playwright helpers ───────────────────────────────────────────────────────

async def _get_snapshot_with_refs(page, ref_store: dict) -> str:
    try:
        elements = await page.evaluate("""
        () => {
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
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) return;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return;
                const tag = el.tagName.toLowerCase();
                const label = (
                    el.getAttribute('aria-label') || el.getAttribute('title') ||
                    el.getAttribute('alt') || el.value || el.innerText || ''
                ).trim().slice(0, 60).replace(/\\s+/g, ' ');
                const sig = tag + '|' + label;
                if (seen.has(sig)) return;
                seen.add(sig);
                const ref = '@e' + idx++;
                el.setAttribute('data-agent-ref', ref);
                let type = el.getAttribute('type') || '';
                let desc = '[' + tag + (type ? ':' + type : '') + ']';
                if (label) desc += ' "' + label + '"';
                const href = el.getAttribute('href');
                if (href && href.length < 80) desc += ' href="' + href + '"';
                const placeholder = el.getAttribute('placeholder');
                if (placeholder) desc += ' placeholder="' + placeholder + '"';
                results.push({ ref, desc });
            });
            return results;
        }
        """)
        if not elements:
            ref_store.clear()
            return "ELEMENTS: (none)"
        ref_store.clear()
        lines = []
        for item in elements[:60]:
            ref_store[item["ref"]] = item["desc"]
            lines.append(f"{item['ref']} {item['desc']}")
        return "ELEMENTS:\n" + "\n".join(lines)
    except Exception as e:
        return f"ELEMENTS: (error: {e})"


async def _take_screenshot(page) -> str:
    try:
        b = await page.screenshot(type="jpeg", quality=55, scale="css",
                                   clip={"x": 0, "y": 0, "width": 1280, "height": 720})
        return base64.b64encode(b).decode()
    except Exception:
        try:
            b = await page.screenshot(type="jpeg", quality=55)
            return base64.b64encode(b).decode()
        except Exception:
            return ""


async def _wait_for_page_stable(page, queue: asyncio.Queue, task_id: str) -> str:
    try:
        await asyncio.wait_for(page.wait_for_load_state("load"), timeout=8.0)
    except Exception:
        pass
    try:
        await asyncio.wait_for(page.wait_for_load_state("networkidle"), timeout=4.0)
    except Exception:
        pass
    await asyncio.sleep(0.5)
    shot_b64 = await _take_screenshot(page)
    await queue.put({
        "type": "step",
        "step": "nav",
        "thought": "Página carregada.",
        "action": f"Página: {page.url}",
        "url": page.url,
        "screenshot": shot_b64,
        "timestamp": datetime.now().isoformat(),
    })
    return shot_b64


async def _skip_video(page) -> str:
    results = []
    for script in VIDEO_SKIP_SCRIPTS:
        try:
            result = await page.evaluate(script)
            results.append(str(result))
        except Exception as e:
            results.append(f"erro: {e}")
    return "; ".join(results)


async def _detect_captcha(page) -> dict | None:
    try:
        return await page.evaluate("""
        () => {
            const bodyText = (document.body && document.body.innerText || '').toLowerCase();
            if (document.querySelector('iframe[src*="recaptcha"]') || document.querySelector('.g-recaptcha') || document.querySelector('[data-sitekey]'))
                return { type: 'recaptcha', description: 'Google reCAPTCHA — resolva e diga "ok"' };
            if (document.querySelector('iframe[src*="hcaptcha"]') || document.querySelector('.h-captcha'))
                return { type: 'hcaptcha', description: 'hCaptcha — complete e diga "ok"' };
            if (document.querySelector('iframe[src*="challenges.cloudflare"]') || document.querySelector('.cf-turnstile'))
                return { type: 'cloudflare', description: 'Cloudflare — aguarde ou complete e diga "ok"' };
            const inputs = Array.from(document.querySelectorAll('input[type="text"], input[type="number"], input:not([type])'));
            for (const inp of inputs) {
                const attrs = [inp.getAttribute('placeholder')||'', inp.getAttribute('name')||'', inp.getAttribute('id')||''].join(' ').toLowerCase();
                if (attrs.includes('captcha')) {
                    const sel = inp.id ? '#'+inp.id : inp.name ? 'input[name="'+inp.name+'"]' : 'input[type="text"]';
                    return { type: 'text', selector: sel, description: 'CAPTCHA de texto — olhe o screenshot e escreva a resposta' };
                }
            }
            if (bodyText.includes('captcha') || bodyText.includes('i am not a robot') || bodyText.includes('não sou um robô'))
                return { type: 'unknown', description: 'CAPTCHA detectado — resolva e diga "ok"' };
            return null;
        }
        """)
    except Exception:
        return None


async def _robust_click(page, text: str) -> None:
    text_clean = text.strip().strip('"').strip("'")
    text_lower = text_clean.lower()

    async def _try(coro):
        try:
            await coro
            await asyncio.sleep(0.3)
            return True
        except Exception:
            return False

    if await _try(page.get_by_role("button", name=text_clean).first.click(timeout=4000)):
        return
    if await _try(page.get_by_role("link", name=text_clean).first.click(timeout=4000)):
        return
    if await _try(page.get_by_role("button", name=text_clean, exact=False).first.click(timeout=4000)):
        return
    if await _try(page.get_by_role("link", name=text_clean, exact=False).first.click(timeout=4000)):
        return
    if await _try(page.get_by_text(text_clean, exact=True).first.click(timeout=4000)):
        return
    if await _try(page.get_by_text(text_clean).first.click(timeout=4000)):
        return
    try:
        found = await page.evaluate(f"""
            () => {{
                const needle = {json.dumps(text_lower)};
                const all = Array.from(document.querySelectorAll('button,a,[role="button"],[role="link"],li,span,div,td,label'));
                let el = all.find(e => {{
                    const t = (e.innerText||e.textContent||e.getAttribute('aria-label')||'').trim().toLowerCase();
                    return t === needle;
                }}) || all.find(e => {{
                    const t = (e.innerText||e.textContent||e.getAttribute('aria-label')||'').trim().toLowerCase();
                    return t.includes(needle) && t.length < needle.length + 20;
                }});
                if (el) {{ el.scrollIntoView({{block:'center',behavior:'instant'}}); el.click(); return true; }}
                return false;
            }}
        """)
        if found:
            await asyncio.sleep(0.4)
            return
    except Exception:
        pass

    raise Exception(f"Could not click '{text_clean}'. Use click_css or click_ref instead.")


async def _wait_for_human(task_id: str, question: str, queue: asyncio.Queue, screenshot_b64: str | None) -> str:
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    human_input_futures[task_id] = future
    await queue.put({
        "type": "human_input_required",
        "question": question,
        "screenshot": screenshot_b64,
        "timestamp": datetime.now().isoformat(),
    })
    try:
        return str(await asyncio.wait_for(future, timeout=300))
    except asyncio.TimeoutError:
        human_input_futures.pop(task_id, None)
        return "(sem resposta)"


# ─── Execute a single planned step ───────────────────────────────────────────

async def _execute_step(
    page,
    action: str,
    args: list,
    task_id: str,
    queue: asyncio.Queue,
    messages: list,
    ref_store: dict,
    step: int,
    current_url: str,
    screenshot_b64: str,
) -> tuple[bool, str, str]:
    """
    Execute one planned step.
    Returns (navigated: bool, new_screenshot_b64: str, action_result_msg: str)
    """
    navigated = False
    result_msg = ""

    if action == "done":
        result = args[0] if args else "Tarefa concluída."
        tasks[task_id]["status"] = "completed"
        tasks[task_id]["result"] = result
        await queue.put({
            "type": "done",
            "result": result,
            "step": step,
            "timestamp": datetime.now().isoformat(),
        })
        return True, screenshot_b64, "done"

    elif action == "fail":
        reason = args[0] if args else "Tarefa impossível."
        raise Exception(reason)

    elif action == "ask_human":
        question = args[0] if args else "O que devo fazer aqui?"
        human_response = await _wait_for_human(task_id, question, queue, screenshot_b64)
        await queue.put({
            "type": "step",
            "step": step,
            "thought": f"Resposta do usuário: {human_response}",
            "action": "Resposta recebida — continuando",
            "url": current_url,
            "timestamp": datetime.now().isoformat(),
        })
        messages.append({
            "role": "user",
            "content": (
                f"The human answered '{question}' with: \"{human_response}\". "
                f"If this was a CAPTCHA, fill the input with this answer and submit. "
                f"Continue the task."
            )
        })

    elif action == "skip_video":
        skip_result = await _skip_video(page)
        await asyncio.sleep(0.8)
        result_msg = f"Video skip: {skip_result}"

    elif action == "navigate":
        url = args[0] if args else ""
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        screenshot_b64 = await _wait_for_page_stable(page, queue, task_id)
        navigated = True

    elif action == "click":
        text = args[0] if args else ""
        url_before = page.url
        await _robust_click(page, text)
        await asyncio.sleep(0.8)
        if page.url != url_before:
            screenshot_b64 = await _wait_for_page_stable(page, queue, task_id)
            navigated = True

    elif action == "click_ref":
        ref = args[0] if args else ""
        url_before = page.url
        clicked = False
        try:
            await page.click(f"[data-agent-ref='{ref}']", timeout=5000)
            clicked = True
        except Exception:
            pass
        if not clicked:
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
            desc = ref_store.get(ref, "")
            if desc:
                m = re.search(r'"([^"]+)"', desc)
                label = m.group(1) if m else desc.split("]")[-1].strip()
                if label:
                    await _robust_click(page, label)
                    clicked = True
            if not clicked:
                raise Exception(f"Could not click ref '{ref}'. Use snapshot to get fresh refs.")
        await asyncio.sleep(0.8)
        if page.url != url_before:
            screenshot_b64 = await _wait_for_page_stable(page, queue, task_id)
            navigated = True
        else:
            await asyncio.sleep(0.3)

    elif action == "click_css":
        sel = args[0] if args else ""
        url_before = page.url
        await page.click(sel, timeout=10000)
        await asyncio.sleep(0.8)
        if page.url != url_before:
            screenshot_b64 = await _wait_for_page_stable(page, queue, task_id)
            navigated = True

    elif action == "fill":
        sel, text = (args[0] if args else ""), (args[1] if len(args) > 1 else "")
        try:
            await page.fill(sel, text, timeout=8000)
        except Exception:
            await page.evaluate(f"""
            () => {{
                const inputs = Array.from(document.querySelectorAll('input[type="text"],input:not([type]),input[type="email"],input[type="password"],textarea'));
                const v = inputs.find(i => i.offsetWidth > 0 && i.offsetHeight > 0);
                if (v) {{ v.value = {json.dumps(text)}; v.dispatchEvent(new Event('input',{{bubbles:true}})); v.dispatchEvent(new Event('change',{{bubbles:true}})); }}
            }}
            """)
        await asyncio.sleep(0.2)

    elif action == "type":
        sel, text = (args[0] if args else ""), (args[1] if len(args) > 1 else "")
        try:
            await page.type(sel, text, timeout=8000)
        except Exception:
            await page.keyboard.type(text)
        await asyncio.sleep(0.2)

    elif action == "press":
        key = args[0] if args else "Enter"
        await page.keyboard.press(key)
        await asyncio.sleep(0.5)

    elif action == "scroll":
        direction = args[0] if args else "down"
        scroll_map = {"down": (0, 500), "up": (0, -500), "right": (500, 0), "left": (-500, 0)}
        dx, dy = scroll_map.get(direction, (0, 500))
        await page.evaluate(f"window.scrollBy({dx}, {dy})")

    elif action == "wait":
        ms = int(args[0]) if args else 1000
        await asyncio.sleep(ms / 1000)
        screenshot_b64 = await _take_screenshot(page)

    elif action == "wait_text":
        text = args[0] if args else ""
        await page.wait_for_selector(f"text={text}", timeout=30000)

    elif action == "eval":
        js = args[0] if args else ""
        eval_result = await page.evaluate(js)
        await asyncio.sleep(0.5)
        result_msg = str(eval_result)

    elif action == "search_web":
        query = args[0] if args else ""
        await queue.put({
            "type": "step",
            "step": step,
            "action": f"Pesquisando: {query}",
            "url": current_url,
            "timestamp": datetime.now().isoformat(),
        })
        search_result = await _search_duckduckgo(query)
        messages.append({"role": "user", "content": f"{search_result}\n\nUse these results to answer and continue."})

    elif action in ("snapshot", "screenshot"):
        pass

    return navigated, screenshot_b64, result_msg


# ─── Main agent loop ──────────────────────────────────────────────────────────

async def _run_agent(task_id: str, task: str, model: str, api_key: str, queue: asyncio.Queue):
    import sys
    from playwright.async_api import async_playwright

    playwright_ctx = None
    browser = None
    is_vision = model in VISION_MODELS

    print(f"[AGENT] Starting task={task_id} model={model}", file=sys.stderr, flush=True)

    try:
        playwright_ctx = await async_playwright().start()
        browser = await playwright_ctx.chromium.launch(
            executable_path=CHROMIUM_PATH,
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                  "--disable-setuid-sandbox", "--window-size=1280,720"],
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
            {"role": "system", "content": BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": f"Complete this task: {task}"},
        ]

        step = 0
        ref_store: dict = {}
        planned_steps: list = []   # buffer of planned steps not yet executed
        batch_steps_done = 0       # how many steps executed from current batch
        consecutive_errors = 0
        is_done = False

        while not is_done:
            # ── Re-plan if buffer is empty ─────────────────────────────────
            if not planned_steps:
                current_url = page.url

                # Check injected user messages
                inj_q = injected_queues.get(task_id)
                if inj_q:
                    while True:
                        try:
                            injected_msg = inj_q.get_nowait()
                            messages.append({"role": "user", "content": f"[Instrução do usuário]: {injected_msg}"})
                            await queue.put({
                                "type": "step",
                                "step": step,
                                "action": f"Nova instrução: {injected_msg}",
                                "url": current_url,
                                "timestamp": datetime.now().isoformat(),
                            })
                        except asyncio.QueueEmpty:
                            break

                # Get page state
                elements = await _get_snapshot_with_refs(page, ref_store)
                try:
                    ax_tree = await page.accessibility.snapshot()
                    snapshot = _accessibility_to_text(ax_tree)[:3000]
                except Exception:
                    snapshot = "(accessibility tree unavailable)"

                screenshot_b64 = await _take_screenshot(page)

                # Proactive CAPTCHA detection
                captcha = await _detect_captcha(page)
                if captcha:
                    cap_desc = captcha.get("description", "CAPTCHA detectado")
                    cap_sel = captcha.get("selector")
                    await queue.put({
                        "type": "step",
                        "step": step,
                        "thought": cap_desc,
                        "action": "CAPTCHA detectado — aguardando usuário",
                        "url": current_url,
                        "screenshot": screenshot_b64,
                        "timestamp": datetime.now().isoformat(),
                    })
                    human_response = await _wait_for_human(task_id, f"🔒 {cap_desc}", queue, screenshot_b64)
                    if cap_sel and human_response.lower() not in ("ok", ""):
                        try:
                            await page.fill(cap_sel, human_response.strip(), timeout=6000)
                            await page.keyboard.press("Enter")
                            await asyncio.sleep(1)
                        except Exception:
                            pass
                    messages.append({"role": "user", "content": f"CAPTCHA resolvido: '{human_response}'. Continue."})
                    continue

                # Emit planning event with current screenshot
                await queue.put({
                    "type": "step",
                    "step": step,
                    "thought": "Analisando página e planejando próximos passos...",
                    "action": "⚡ Planejando próximos passos...",
                    "url": current_url,
                    "screenshot": screenshot_b64,
                    "timestamp": datetime.now().isoformat(),
                })

                # Build context for LLM
                context_text = f"Step {step}\nURL: {current_url}\n\n{elements}\n\nSNAPSHOT:\n{snapshot}"
                if is_vision and screenshot_b64:
                    msg_content: list = [
                        {"type": "text", "text": context_text},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
                    ]
                else:
                    msg_content = context_text  # type: ignore

                messages.append({"role": "user", "content": msg_content})

                # Trim conversation history
                if len(messages) > 20:
                    messages = messages[:2] + messages[-16:]

                # Strip images from non-last messages for vision models
                def _strip_old_images(msgs: list) -> list:
                    result = []
                    for i, msg in enumerate(msgs):
                        is_last = (i == len(msgs) - 1 and msg["role"] == "user")
                        content = msg.get("content")
                        if not isinstance(content, list):
                            result.append(msg)
                        elif is_vision and is_last:
                            result.append(msg)
                        elif is_vision:
                            text_parts = [p for p in content if p.get("type") == "text"]
                            result.append({**msg, "content": text_parts or content})
                        else:
                            text = "\n".join(p.get("text", "") for p in content if p.get("type") == "text")
                            result.append({**msg, "content": text})
                    return result

                # Call LLM for batch plan
                try:
                    raw_response = await _ask_llm(
                        api_key, model, _strip_old_images(messages),
                        queue=queue, task_id=task_id, current_url=current_url
                    )
                except DailyLimitExceeded:
                    switched = False
                    for fallback in FALLBACK_MODELS:
                        if fallback != model:
                            old = model
                            model = fallback
                            is_vision = model in VISION_MODELS
                            await queue.put({
                                "type": "step",
                                "step": step,
                                "action": f"Limite diário atingido para '{old}'. Trocando para '{model}'.",
                                "url": current_url,
                                "timestamp": datetime.now().isoformat(),
                            })
                            try:
                                raw_response = await _ask_llm(api_key, model, _strip_old_images(messages), queue=queue, task_id=task_id, current_url=current_url)
                                switched = True
                                break
                            except Exception:
                                continue
                    if not switched:
                        raise Exception("Limite diário atingido em todos os modelos.")

                messages.append({"role": "assistant", "content": raw_response})

                parsed = _parse_batch(raw_response)
                if not parsed or not parsed.get("steps"):
                    consecutive_errors += 1
                    if consecutive_errors >= 4:
                        raise Exception("Agente não conseguiu planejar ações válidas.")
                    await asyncio.sleep(1)
                    messages.pop()
                    continue

                consecutive_errors = 0
                planned_steps = parsed["steps"]
                batch_steps_done = 0
                thought = parsed.get("thought", "")
                print(f"[AGENT] Planned {len(planned_steps)} steps. Thought: {thought[:100]}", file=sys.stderr, flush=True)

            # ── Execute next planned step ──────────────────────────────────
            step += 1
            action_item = planned_steps.pop(0)
            action = action_item.get("action", "")
            args = action_item.get("args", [])
            description = action_item.get("description", action)

            current_url = page.url
            shot_b64 = await _take_screenshot(page)

            # Emit step event
            await queue.put({
                "type": "step",
                "step": step,
                "thought": action_item.get("thought", ""),
                "action": description,
                "url": current_url,
                "screenshot": shot_b64 if batch_steps_done == 0 else None,
                "timestamp": datetime.now().isoformat(),
            })

            # Execute
            try:
                navigated, new_shot, result_msg = await _execute_step(
                    page, action, args, task_id, queue, messages,
                    ref_store, step, current_url, shot_b64
                )
                batch_steps_done += 1

                if action == "done":
                    is_done = True
                    break

                # After navigation, clear remaining plan (page changed)
                if navigated and planned_steps:
                    messages.append({
                        "role": "user",
                        "content": f"Navegado para {page.url}. Re-planejando próximos passos."
                    })
                    planned_steps.clear()

                # After eval, add result to context if informative
                if action == "eval" and result_msg:
                    messages.append({
                        "role": "user",
                        "content": f"eval() result: {result_msg[:500]}"
                    })

            except Exception as action_err:
                err_msg = str(action_err)
                print(f"[AGENT] Step {step} error: {err_msg}", file=sys.stderr, flush=True)
                await queue.put({
                    "type": "step",
                    "step": step,
                    "thought": f"Erro: {err_msg}",
                    "action": f"Erro em '{action}' — re-planejando",
                    "url": current_url,
                    "timestamp": datetime.now().isoformat(),
                })
                messages.append({
                    "role": "user",
                    "content": f"Step {step} ('{action}' {args}) failed: {err_msg}. Adjust plan and try differently."
                })
                planned_steps.clear()
                consecutive_errors += 1
                if consecutive_errors >= 6:
                    raise Exception(f"Muitos erros consecutivos: {err_msg}")
                continue

            # After executing all steps in batch: take verification screenshot
            if not planned_steps and not is_done:
                verify_shot = await _take_screenshot(page)
                await queue.put({
                    "type": "step",
                    "step": step,
                    "thought": "Verificando resultado do lote de passos...",
                    "action": "🔍 Verificando resultado...",
                    "url": page.url,
                    "screenshot": verify_shot,
                    "timestamp": datetime.now().isoformat(),
                })
                # Add verification screenshot to conversation
                if is_vision and verify_shot:
                    messages.append({"role": "user", "content": [
                        {"type": "text", "text": f"Executed batch. Current URL: {page.url}. Plan next steps."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{verify_shot}"}},
                    ]})
                else:
                    messages.append({
                        "role": "user",
                        "content": f"Batch executed. Current URL: {page.url}. Plan next steps."
                    })

        if not is_done:
            tasks[task_id]["status"] = "completed"
            tasks[task_id]["result"] = "Tarefa concluída."
            await queue.put({
                "type": "done",
                "result": "Tarefa concluída.",
                "step": step,
                "timestamp": datetime.now().isoformat(),
            })

    except Exception as e:
        error_msg = str(e)
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = error_msg
        await queue.put({"type": "error", "error": error_msg, "timestamp": datetime.now().isoformat()})
    finally:
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
        try:
            await queue.put(None)
        except Exception:
            pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    _free_port(port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
