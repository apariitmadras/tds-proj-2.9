# main.py
# Executor (ChatGPT/tools): runs plan → scraping → extraction → code execution.
# Exposes: run_agent_for_api(task: str, plan: str = "") -> list
#
# Env vars:
#   OPENAI_API_KEY   (required)
#   OPENAI_BASE      (optional, default https://api.openai.com)
#   EXECUTOR_MODEL   (optional, default gpt-4o-mini)

from __future__ import annotations

import os
import sys
import json
import time
import asyncio
import subprocess
from pathlib import Path
from typing import Dict, Any, List

import httpx
from bs4 import BeautifulSoup
from tools.scrape_website import scrape_website
from tools.get_relevant_data import get_relevant_data

# Paths for runtime artifacts
down = Path(__file__).parent.resolve()
ROOT = down
OUTPUTS = ROOT / "outputs"
OUTPUTS.mkdir(parents=True, exist_ok=True)

# Where raw LLM JSON and temp scripts live
GPT_RESP_PATH = OUTPUTS / "gpt_response.json"
TEMP_SCRIPT_PATH = OUTPUTS / "temp_script.py"

# New: system prompt loaded from file
EXECUTOR_PROMPT_FILE = ROOT / "prompts" / "executor.txt"

def _load_executor_prompt() -> str:
    """
    Load the system prompt for the execution agent from prompts/executor.txt
    """
    if EXECUTOR_PROMPT_FILE.exists():
        return EXECUTOR_PROMPT_FILE.read_text(encoding="utf-8")
    # Fallback if missing
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

    proc = subprocess.run(
        [sys.executable, str(TEMP_SCRIPT_PATH)],
        capture_output=True,
        text=True,
        env={**os.environ},
    )

    if proc.returncode != 0 and not proc.stdout.strip():
        return json.dumps({"error": "code_failed", "stderr": proc.stderr})
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

    timeout = httpx.Timeout(30.0)
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "tools": tools, "tool_choice": "auto"},
        )
        r.raise_for_status()
        data = r.json()

    # save raw response for debugging
    try:
        GPT_RESP_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

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
    if name == "scrape_website":
        res = await scrape_website(**args)
        return json.dumps(res)
    if name == "get_relevant_data":
        res = get_relevant_data(**args)
        return json.dumps(res)
    if name == "answer_questions":
        return await answer_questions(**args)
    return json.dumps({"ok": False, "error": f"Unknown tool '{name}'"})


async def run_agent_for_api(task: str, plan: str = "") -> list:
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": f"{task}\n\nPlan:\n{plan}\n\nReturn ONLY the JSON array."},
    ]
    start = time.time()
    budget = int(os.getenv("TOOL_LOOP_BUDGET", "110"))
    while True:
        if time.time() - start > budget:
            raise TimeoutError("Tool loop exceeded time budget")

        msg = _chat(messages)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            final_text = (msg.get("content") or "").strip()
            return json.loads(final_text)

        # execute requested tools
        messages.append({"role": "assistant", "content": None, "tool_calls": tool_calls})
        for tc in tool_calls:
            args = _parse_args(tc["function"].get("arguments"))
            out = await _call_tool(tc["function"]["name"], args)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": out})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run the executor locally.")
    parser.add_argument("task", type=str, help="User task/question")
    parser.add_argument("--plan", type=str, default=os.getenv("PLAN", ""), help="Optional pre-generated plan")
    args = parser.parse_args()

    result = asyncio.run(run_agent_for_api(args.task, args.plan))
    print(json.dumps(result, ensure_ascii=False))
