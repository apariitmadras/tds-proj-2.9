# main.py
# Executor (ChatGPT/tools): runs plan → scraping → extraction → code execution.

from __future__ import annotations

import os
import sys
import json
import time
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, List

import httpx
from bs4 import BeautifulSoup
from tools.scrape_website import scrape_website
from tools.get_relevant_data import get_relevant_data

# ---------- Logging setup ----------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# Paths for runtime artifacts
ROOT = Path(__file__).parent.resolve()
OUTPUTS = ROOT / "outputs"
OUTPUTS.mkdir(parents=True, exist_ok=True)

# Where raw LLM JSON and temp scripts live
GPT_RESP_PATH = OUTPUTS / "gpt_response.json"
TEMP_SCRIPT_PATH = OUTPUTS / "temp_script.py"

# System prompt file
EXECUTOR_PROMPT_FILE = ROOT / "prompts" / "executor.txt"


def _load_executor_prompt() -> str:
    if EXECUTOR_PROMPT_FILE.exists():
        return EXECUTOR_PROMPT_FILE.read_text(encoding="utf-8")
    return (
        "You are an execution agent. Use tools to: (1) fetch the target page, "
        "(2) extract the necessary data, (3) when ready, generate complete Python code and call 'answer_questions' with it. "
        "The code MUST print ONLY the final JSON array required by the task. "
        "Do not include explanations—return only the JSON array."
    )

def _system_prompt() -> str:
    return _load_executor_prompt()


async def answer_questions(code: str) -> str:
    """
    Write provided Python code to a temp file and run it. Must print ONLY JSON array.
    Returns stdout (JSON array string).
    """
    TEMP_SCRIPT_PATH.write_text(code, encoding="utf-8")
    logging.info(f"💻 Running generated code: {TEMP_SCRIPT_PATH.name} (len={len(code)} chars)")

    proc = subprocess.run(
        [sys.executable, str(TEMP_SCRIPT_PATH)],
        capture_output=True,
        text=True,
        env={**os.environ},
    )

    if proc.returncode != 0 and not proc.stdout.strip():
        logging.error("💥 Generated code failed with no stdout; returning stderr JSON")
        logging.debug(f"stderr:\n{proc.stderr[:2000]}")
        return json.dumps({"error": "code_failed", "stderr": proc.stderr})
    if proc.stderr.strip():
        logging.debug(f"stderr (non-fatal):\n{proc.stderr[:2000]}")
    return proc.stdout


# Tools schema for OpenAI function-calling
tools: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "scrape_website",
            "description": "Scrapes a website and saves the HTML to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "output_file": {"type": "string"},
                },
                "required": ["url", "output_file"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_relevant_data",
            "description": "Extract text from saved HTML using a CSS selector.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_name": {"type": "string"},
                    "js_selector": {"type": "string"},
                },
                "required": ["file_name", "js_selector"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "answer_questions",
            "description": "Runs provided Python code that prints ONLY the final JSON array.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    },
]


def _chat(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    base = os.getenv("OPENAI_BASE", "https://api.openai.com").rstrip("/")
    token = os.getenv("OPENAI_API_KEY")
    if not token:
        raise RuntimeError("Missing OPENAI_API_KEY")

    url = f"{base}/v1/chat/completions"
    model = os.getenv("EXECUTOR_MODEL", "gpt-4o-mini")

    t0 = time.time()
    timeout = httpx.Timeout(30.0)
    logging.info(f"🗣️  OpenAI call model={model} base={base} (timeout={timeout.connect}s)")
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "tools": tools, "tool_choice": "auto"},
        )
        r.raise_for_status()
        data = r.json()
    logging.info(f"🗣️  OpenAI responded in {time.time() - t0:.2f}s")

    # save raw response for debugging
    try:
        GPT_RESP_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.debug(f"🗂  Saved raw response → {GPT_RESP_PATH}")
    except Exception as e:
        logging.debug(f"Could not save raw response: {e}")

    return data["choices"][0]["message"]


def _parse_args(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


async def _call_tool(name: str, args: Dict[str, Any]) -> str:
    # Truncate logged args if huge (e.g., code strings)
    def _short(v: Any) -> Any:
        if isinstance(v, str) and len(v) > 300:
            return v[:300] + "…(truncated)"
        return v

    safe_args = {k: _short(v) for k, v in args.items()}
    t0 = time.time()
    logging.info(f"🧰 Tool call: {name}({safe_args})")

    try:
        if name == "scrape_website":
            res = await scrape_website(**args)
            out = json.dumps(res)
        elif name == "get_relevant_data":
            res = get_relevant_data(**args)
            out = json.dumps(res)
        elif name == "answer_questions":
            out = await answer_questions(**args)
        else:
            out = json.dumps({"ok": False, "error": f"Unknown tool '{name}'"})
        logging.info(f"✅ Tool {name} done in {time.time()-t0:.2f}s")
        return out
    except Exception as e:
        logging.exception(f"❌ Tool {name} failed: {e}")
        raise


async def run_agent_for_api(task: str, plan: str = "") -> list:
    import uuid as _uuid
    rid = _uuid.uuid4().hex[:8]
    logging.info(f"[{rid}] ▶️ Executor start")
    logging.debug(f"[{rid}] task preview: {task[:400]!r}")
    logging.debug(f"[{rid}] plan preview: {plan[:1200]!r}")

    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": f"{task}\n\nPlan:\n{plan}\n\nReturn ONLY the JSON array."},
    ]
    start = time.time()
    budget = int(os.getenv("TOOL_LOOP_BUDGET", "110"))
    iter_no = 0

    while True:
        if time.time() - start > budget:
            logging.error(f"[{rid}] ⏰ Tool loop exceeded time budget ({budget}s)")
            raise TimeoutError("Tool loop exceeded time budget")

        iter_no += 1
        logging.info(f"[{rid}] 🔁 Iteration {iter_no}: calling model…")
        msg = _chat(messages)

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            final_text = (msg.get("content") or "").strip()
            logging.info(f"[{rid}] 🧾 Model returned final text; parsing JSON")
            try:
                parsed = json.loads(final_text)
                logging.info(f"[{rid}] ✅ Final JSON parsed successfully")
                return parsed
            except Exception as e:
                logging.exception(f"[{rid}] ❌ Final reply not valid JSON: {e}")
                raise

        # execute requested tools
        messages.append({"role": "assistant", "content": None, "tool_calls": tool_calls})
        for tc in tool_calls:
            name = tc["function"]["name"]
            args = _parse_args(tc["function"].get("arguments"))
            out = await _call_tool(name, args)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
