# app.py
# FastAPI entrypoint (planner + glue): accepts questions.txt, calls Gemini to plan,
# writes outputs/abdul_breaked_task.txt, invokes the executor, returns ONLY a JSON array.

from __future__ import annotations

import os
import asyncio
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Gemini (new SDK)
from google import genai

# Executor
from main import run_agent_for_api


app = FastAPI(title="Data Analyst Agent (Planner→Executor)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT = Path(__file__).parent.resolve()
OUTPUTS = ROOT / "outputs"
OUTPUTS.mkdir(parents=True, exist_ok=True)

PLAN_FILE = OUTPUTS / "abdul_breaked_task.txt"
PROMPT_FILE = ROOT / "prompts" / "abdul_task_breakdown.txt"


def _load_planner_prompt() -> str:
    if PROMPT_FILE.exists():
        return PROMPT_FILE.read_text(encoding="utf-8")
    raise RuntimeError(f"Planner prompt missing at: {PROMPT_FILE}")

def plan_with_gemini(task_text: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY")

    client = genai.Client(api_key=api_key)
    prompt_text = _load_planner_prompt()

    # Compose contents: QUESTION then prompt
    resp = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite"),
        contents=[task_text, prompt_text],
    )

    plan = (resp.text or "").strip()
    PLAN_FILE.write_text(plan, encoding="utf-8")
    return plan


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "has_gemini_key": bool(os.getenv("GEMINI_API_KEY")),
        "has_executor_key": bool(os.getenv("OPENAI_API_KEY")),
    }


async def _handle_upload(file: UploadFile) -> JSONResponse:
    if not file:
        raise HTTPException(status_code=400, detail="File is required")

    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except Exception:
        text = raw.decode("latin-1")

    if not text.strip():
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # 1) Plan with Gemini → write outputs/abdul_breaked_task.txt (like senior’s repo)
    try:
        plan = plan_with_gemini(text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Planner failed: {e}")

    # 2) Execute with ChatGPT/tools → final JSON array
    try:
        final_answer = await asyncio.wait_for(
            run_agent_for_api(text, plan), timeout=int(os.getenv("EXECUTOR_TIMEOUT", "170"))
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timed out while executing the plan")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Executor failed: {e}")

    # 3) Return EXACTLY the JSON array (no wrapper object)
    return JSONResponse(content=final_answer)


@app.post("/api/")
@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)):
    return await _handle_upload(file)


@app.get("/")
def root():
    return {"message": "Data Analyst Agent is running. POST /api/ with questions.txt"}


