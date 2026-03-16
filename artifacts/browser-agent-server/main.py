import asyncio
import base64
import json
import os
import shutil
import uuid
from datetime import datetime
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from browser_use import Agent, BrowserProfile, BrowserSession, ChatOpenAI
from browser_use.llm.openai.chat import ChatInvokeCompletion


def _strip_code_fences(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        idx = content.find("\n")
        content = content[idx + 1 :].strip() if idx >= 0 else content
        if content.endswith("```"):
            content = content[:-3].strip()
    return content


class NovaChatOpenAI(ChatOpenAI):
    async def ainvoke(self, messages, output_format=None, **kwargs):
        raw = await super().ainvoke(messages, output_format=None, **kwargs)
        if output_format is not None:
            content = raw.completion if isinstance(raw.completion, str) else ""
            content = _strip_code_fences(content)
            parsed = output_format.model_validate_json(content)
            return ChatInvokeCompletion(
                completion=parsed,
                usage=raw.usage,
                stop_reason=raw.stop_reason,
            )
        return raw


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
    model: str = "nova-2-lite-v1"


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
        raise HTTPException(status_code=500, detail="NOVA_API_KEY not configured")

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


async def _run_agent(task_id: str, task: str, model: str, api_key: str, queue: asyncio.Queue):
    browser_session = None
    try:
        llm = NovaChatOpenAI(
            model=model,
            api_key=api_key,
            base_url="https://api.nova.amazon.com/v1",
        )

        chromium_path = shutil.which("chromium") or shutil.which("chromium-browser") or \
            "/nix/store/qa9cnw4v5xkxyip6mb9kxqfq1z4x2dx1-chromium-138.0.7204.100/bin/chromium"
        profile = BrowserProfile(
            headless=True,
            disable_security=True,
            executable_path=chromium_path,
            args=["--no-sandbox", "--disable-gpu"],
        )
        browser_session = BrowserSession(browser_profile=profile)

        step_count = 0

        async def on_step(browser_state: Any, agent_output: Any, step_number: int):
            nonlocal step_count
            step_count = step_number

            screenshot_b64 = None
            current_url = None

            try:
                if browser_state and hasattr(browser_state, "screenshot"):
                    raw = browser_state.screenshot
                    if isinstance(raw, bytes):
                        screenshot_b64 = base64.b64encode(raw).decode()
                    elif isinstance(raw, str):
                        screenshot_b64 = raw
            except Exception:
                pass

            try:
                if browser_state and hasattr(browser_state, "url"):
                    current_url = browser_state.url
            except Exception:
                pass

            action_text = None
            thought_text = None
            try:
                if agent_output and hasattr(agent_output, "current_state"):
                    state = agent_output.current_state
                    if hasattr(state, "thought"):
                        thought_text = state.thought
                    if hasattr(state, "next_goal"):
                        action_text = state.next_goal
                if not action_text and agent_output and hasattr(agent_output, "action"):
                    action_text = str(agent_output.action)
            except Exception:
                pass

            event = {
                "type": "step",
                "step": step_number,
                "thought": thought_text,
                "action": action_text,
                "url": current_url,
                "screenshot": screenshot_b64,
                "timestamp": datetime.now().isoformat(),
            }
            await queue.put(event)

        agent = Agent(
            task=task,
            llm=llm,
            browser_session=browser_session,
            register_new_step_callback=on_step,
            use_vision=True,
            max_failures=3,
        )

        await queue.put({
            "type": "started",
            "message": f"Agente iniciado: {task}",
            "timestamp": datetime.now().isoformat(),
        })

        history = await agent.run(max_steps=20)

        result_text = None
        try:
            if history and hasattr(history, "final_result"):
                result_text = history.final_result()
            if not result_text and history:
                result_text = str(history)
        except Exception:
            result_text = "Tarefa concluída."

        tasks[task_id]["status"] = "completed"
        tasks[task_id]["result"] = result_text

        await queue.put({
            "type": "done",
            "result": result_text,
            "steps": step_count,
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
        if browser_session:
            try:
                await browser_session.stop()
            except Exception:
                pass
        await queue.put(None)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
