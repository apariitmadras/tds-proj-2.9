# app.py
# FastAPI entrypoint (planner + glue): accepts questions.txt, calls Gemini to plan,
# writes outputs/planner_breaked_task.txt, invokes the executor, returns ONLY a JSON array.

from __future__ import annotations

import os
import time
import uuid
import asyncio
import logging
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Gemini (new SDK)
from google import genai

# Executor
from main import run_agent_for_api

# ---------- Logging setup ----------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)

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

PLAN_FILE = OUTPUTS / "planner_breaked_task.txt"
PROMPT_FILE = ROOT / "prompts" / "planner_task_breakdown.txt"


def _log_big(label: str, text: str, chunk: int = 2000, level: int = logging.INFO) -> None:
    """
    Log long strings in chunks so platform logs don't truncate them.
    """
    if text is None:
        return
    n = max(1, (len(text) + chunk - 1) // chunk)
    logging.log(level, f"{label} (len={len(text)} chars, chunks={n})")
    for i in range(0, len(text), chunk):
        logging.log(level, text[i : i + chunk])


def _load_planner_prompt() -> str:
    if not PROMPT_FILE.exists():
        raise RuntimeError(f"Planner prompt missing at: {PROMPT_FILE}")
    prompt_text = PROMPT_FILE.read_text(encoding="utf-8")
    # Print the ENTIRE planner prompt to logs
    _log_big("📄 planner_task_breakdown.txt", prompt_text, level=logging.INFO)
    return prompt_text


def plan_with_gemini(task_text: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY")

    client = genai.Client(api_key=api_key)
    prompt_text = _load_planner_prompt()
    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")

    logging.info(f"🧭 Planner: calling Gemini model={model}")
    t0 = time.time()
    resp = client.models.generate_content(
        model=model,
        contents=[prompt_text, task_text],  # prompt first for instruction adherence
    )
    dt = time.time() - t0
    logging.info(f"🧭 Planner: Gemini completed in {dt:.2f}s")

    # 1) Save the plan
    plan = (resp.text or "").strip()
    PLAN_FILE.write_text(plan, encoding="utf-8")

    # 2) Read it back and log FULL contents (chunked)
    try:
        plan_text = PLAN_FILE.read_text(encoding="utf-8")
    except Exception:
        plan_text = plan  # fallback
    logging.info("💡 Generated plan saved at outputs/planner_breaked_task.txt")
    _log_big("📄 planner_breaked_task.txt", plan_text, level=logging.INFO)

    return plan


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "has_gemini_key": bool(os.getenv("GEMINI_API_KEY")),
        "has_executor_key": bool(os.getenv("OPENAI_API_KEY")),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite"),
        "executor_timeout": int(os.getenv("EXECUTOR_TIMEOUT", "1700")),
    }


async def _handle_upload(file: UploadFile) -> JSONResponse:
    if not file:
        raise HTTPException(status_code=400, detail="File is required")

    req_id = uuid.uuid4().hex[:8]
    t_start = time.time()
    logging.info(f"[{req_id}] 📥 Received file: {file.filename}")

    raw = await file.read()
    logging.info(f"[{req_id}] 📦 File size: {len(raw)} bytes")
    try:
        text = raw.decode("utf-8")
    except Exception:
        text = raw.decode("latin-1")
    logging.debug(f"[{req_id}] 📝 Task preview: {text[:400]!r}")

    if not text.strip():
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # 1) Plan with Gemini
    try:
        logging.info(f"[{req_id}] 🧭 Planning with Gemini…")
        t0 = time.time()
        plan = plan_with_gemini(text)
        logging.info(f"[{req_id}] ✅ Planner done in {time.time()-t0:.2f}s")
    except Exception as e:
        logging.exception(f"[{req_id}] ❌ Planner failed: {e}")
        raise HTTPException(status_code=500, detail=f"Planner failed: {e}")

    # 2) Execute with ChatGPT/tools → final JSON array
    timeout_s = int(os.getenv("EXECUTOR_TIMEOUT", "1700"))
    try:
        logging.info(f"[{req_id}] 🛠️ Executing plan with executor (timeout={timeout_s}s)…")
        t1 = time.time()
        final_answer = await asyncio.wait_for(
            run_agent_for_api(text, plan),
            timeout=timeout_s,
        )
        logging.info(f"[{req_id}] ✅ Executor done in {time.time()-t1:.2f}s")
    except asyncio.TimeoutError:
        logging.error(f"[{req_id}] ⏰ Executor timed out after {timeout_s}s")
        raise HTTPException(status_code=504, detail="Timed out while executing the plan")
    except Exception as e:
        logging.exception(f"[{req_id}] ❌ Executor failed: {e}")
        raise HTTPException(status_code=500, detail=f"Executor failed: {e}")

    logging.info(f"[{req_id}] 🚀 Returning final JSON array (total {time.time()-t_start:.2f}s)")
    return JSONResponse(content=final_answer)


@app.post("/api/")
@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)):
    return await _handle_upload(file)


@app.get("/")
def root():
    return {"message": "Data Analyst Agent is running. POST /api/ with questions.txt"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=False
    )
