import asyncio
import base64
import json
import os
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




_http_client = httpx.AsyncClient(timeout=30.0)

tasks: dict[str, dict] = {}
task_queues: dict[str, asyncio.Queue] = {}
# human_input_futures: task_id -> asyncio.Future waiting for human response
human_input_futures: dict[str, asyncio.Future] = {}


class RunRequest(BaseModel):
    task: str
    model: str = "meta-llama/llama-4-scout-17b-16e-instruct"


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
    asyncio.create_task(_run_agent(task_id, request.task, request.model, groq_api_key, queue))
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


async def _ask_llm(api_key: str, model: str, messages: list, retries: int = 5, use_grounding: bool = False) -> str:
    import re
    import sys

    for attempt in range(retries):
        try:
            response = await _http_client.post(
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
                },
                timeout=60,
            )

            if response.status_code == 429:
                wait = 20
                try:
                    err_json = response.json()
                    m_msg = err_json.get("error", {}).get("message", "")
                    m = re.search(r"(\d+)\s*second", m_msg)
                    if m:
                        wait = int(m.group(1)) + 2
                except Exception:
                    pass
                wait = min(wait * (attempt + 1), 60)
                await asyncio.sleep(wait)
                continue

            if response.status_code != 200:
                raise Exception(f"HTTP Error {response.status_code}: {response.text[:200]}")

            data = response.json()
            content = data["choices"][0]["message"].get("content")
            if content:
                return content

            print(f"[WARN] No content in response: {json.dumps(data)[:400]}", file=sys.stderr)
            await asyncio.sleep(2 * (attempt + 1))

        except Exception as e:
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
        ref_store: dict = {}

        while step < max_steps:
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

            # Strip images from all messages except the last user message
            # Groq supports max 5 images — keep only the most recent screenshot
            def _strip_images(msgs: list) -> list:
                result = []
                for i, msg in enumerate(msgs):
                    is_last_user = (i == len(msgs) - 1 and msg["role"] == "user")
                    if is_last_user or not isinstance(msg.get("content"), list):
                        result.append(msg)
                    else:
                        text_only = [p for p in msg["content"] if p.get("type") == "text"]
                        result.append({**msg, "content": text_only if text_only else msg["content"]})
                return result

            # Ask LLM
            try:
                raw_response = await _ask_llm(api_key, model, _strip_images(messages))
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

                elif action == "click_ref":
                    ref = args[0] if args else ""
                    try:
                        await page.click(f"[data-agent-ref='{ref}']", timeout=8000)
                    except Exception:
                        # Fallback: use ref description to click by text
                        desc = ref_store.get(ref, "")
                        if desc:
                            label = desc.split('"')[1] if '"' in desc else desc
                            await _robust_click(page, label)
                        else:
                            raise Exception(f"Ref {ref} not found in current page")
                    await asyncio.sleep(0.5)

                elif action == "click_css":
                    # Click by CSS selector as fallback
                    sel = args[0] if args else ""
                    await page.click(sel, timeout=10000)
                    await asyncio.sleep(0.5)

                elif action == "search_web":
                    query = args[0] if args else ""
                    # Make a dedicated grounding call to search the web
                    search_messages = [
                        {"role": "system", "content": "You are a helpful assistant with web search. Answer concisely based on search results."},
                        {"role": "user", "content": f"Search the web and answer: {query}"}
                    ]
                    try:
                        search_result = await _ask_llm(api_key, model, search_messages)
                    except Exception as e:
                        search_result = f"(web search failed: {e})"
                    messages.append({
                        "role": "user",
                        "content": f"Web search results for '{query}':\n{search_result}\n\nNow continue the task using this information."
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
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
