"""Throwaway live smoke test: two-turn conversation through run_turn_with_memory.

Run: uv run python -m app.agent._memory_smoke_test
Proves short-term memory lets turn 2 resolve "the first one" from turn 1.
"""

import json

from app.agent.memory import create_session, load_session_messages, run_turn_with_memory


def print_tool_call(name: str, args: dict, result) -> None:
    print(f"  >>> TOOL CALL: {name}({json.dumps(args, ensure_ascii=False)})")
    preview = json.dumps(result, ensure_ascii=False, default=str)
    print(f"      result: {preview[:250]}{'…' if len(preview) > 250 else ''}")


def main() -> None:
    session_id = create_session(user_id=None)
    print(f"session: {session_id}")

    for i, msg in enumerate(
        ["Show me Toyota SUVs", "What's the mileage on the first one?"], start=1
    ):
        print(f"\n===== TURN {i}: {msg!r}")
        result = run_turn_with_memory(session_id, msg, None, on_tool_call=print_tool_call)
        print(f"REPLY:\n{result['reply']}")
        print(f"cars this turn: {[c['id'] for c in result['cars']]}")

    print("\n===== PERSISTED TRANSCRIPT:")
    for m in load_session_messages(session_id):
        print(f"  [{m['role']}] {m['content'][:100].replace(chr(10), ' ')}…")


if __name__ == "__main__":
    main()
