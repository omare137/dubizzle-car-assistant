"""Throwaway diagnostic script for the four suspected bugs (Phase 13 audit).

Run: uv run python -m app.agent._bug_audit_smoke <bug1|bug3|bug4>

All instrumentation lives HERE (tool-call observer + a get_completion wrapper),
so no application code is modified.
"""

import json
import sys
import time

from app.agent import orchestrator
from app.agent.memory import create_session, run_turn_with_memory
from app.db.database import get_connection
from app.db.schema import leads, messages, sessions

TRACE: list[str] = []


def spy(name: str, args: dict, result) -> None:
    if isinstance(result, list):
        ids = [r.get("id") for r in result if isinstance(r, dict)]
        summary = f"returned ids={ids}"
    elif isinstance(result, dict):
        summary = json.dumps({k: result[k] for k in list(result)[:4]}, ensure_ascii=False, default=str)[:160]
    else:
        summary = str(result)[:160]
    line = f"TOOL {name}({json.dumps(args, ensure_ascii=False)}) -> {summary}"
    TRACE.append(line)
    print("  " + line)


def cleanup(sid: str) -> None:
    with get_connection() as conn:
        conn.execute(leads.delete().where(leads.c.session_id == sid))
        conn.execute(messages.delete().where(messages.c.session_id == sid))
        conn.execute(sessions.delete().where(sessions.c.id == sid))


def bug1() -> None:
    """Compare-two-cars turn; log every car id returned by tools + final cars list."""
    sid = create_session(user_id=None)
    try:
        result = run_turn_with_memory(
            sid, "Compare the Audi Q8 and the BMW i4 for me", None, on_tool_call=spy
        )
        print("\nFINAL result['cars'] ids:", [c["id"] for c in result["cars"]])
        print("FINAL result['cars'] titles:")
        for c in result["cars"]:
            print(f"  id={c['id']}: {c['title'][:60]}")
        print("\nREPLY (first 400):", result["reply"][:400])
    finally:
        cleanup(sid)


def bug3() -> None:
    """Interest-in-one-car conversation; full tool trace for the offer turn, x3 runs."""
    for run in (1, 2, 3):
        print(f"\n########## RUN {run} ##########")
        sid = create_session(user_id=None)
        try:
            turns = [
                "Tell me about the 2023 Toyota Prado GXR",
                "That sounds great, I'd love to come see it this week",
            ]
            for i, msg in enumerate(turns, 1):
                time.sleep(15)  # pace under the 15 req/min free-tier limit
                TRACE.clear()
                print(f"-- turn {i}: {msg!r}")
                result = run_turn_with_memory(sid, msg, None, on_tool_call=spy)
                if not TRACE:
                    print("  (no tool calls this turn)")
                print(f"  booking_prompt: {result['booking_prompt']}")
                print(f"  REPLY: {result['reply'][:350]}")
        finally:
            cleanup(sid)


def bug4() -> None:
    """Two-turn follow-up; dump the exact messages array sent to the LLM on turn 2."""
    real_get_completion = orchestrator.get_completion
    captured: list[list[dict]] = []

    def logging_get_completion(msgs, tools=None):
        captured.append([dict(m) for m in msgs])
        return real_get_completion(msgs, tools)

    orchestrator.get_completion = logging_get_completion
    sid = create_session(user_id=None)
    try:
        r1 = run_turn_with_memory(sid, "Show me BMWs under 200k AED", None, on_tool_call=spy)
        print("\nTURN 1 cars:", [c["id"] for c in r1["cars"]])
        print("TURN 1 reply (first 300):", r1["reply"][:300])

        captured.clear()
        r2 = run_turn_with_memory(sid, "Tell me more about the i4", None, on_tool_call=spy)
        print("\nTURN 2 reply (first 400):", r2["reply"][:400])

        print("\n===== EXACT messages array sent to LLM on turn 2 (first LLM call):")
        for m in captured[0]:
            role = m.get("role")
            content = (m.get("content") or "")[:220].replace("\n", " ")
            extra = f" tool_calls={bool(m.get('tool_calls'))}" if m.get("tool_calls") else ""
            print(f"  [{role}]{extra} {content}")
    finally:
        orchestrator.get_completion = real_get_completion
        cleanup(sid)


if __name__ == "__main__":
    {"bug1": bug1, "bug3": bug3, "bug4": bug4}[sys.argv[1]]()
