"""Throwaway live smoke test: one real tool-calling round trip against Gemini.

Run: uv run python -m app.agent._orchestrator_smoke_test
Not part of the pytest suite.
"""

import json

from app.agent.orchestrator import run_agent_turn


def print_tool_call(name: str, args: dict, result) -> None:
    print(f"\n>>> TOOL CALL: {name}({json.dumps(args, ensure_ascii=False)})")
    preview = json.dumps(result, ensure_ascii=False, default=str)
    print(f"    result: {preview[:400]}{'…' if len(preview) > 400 else ''}")


def main() -> None:
    result = run_agent_turn(
        [{"role": "user", "content": "Show me Mercedes SUVs under 200k AED"}],
        profile_summary=None,
        on_tool_call=print_tool_call,
    )
    print("\n" + "=" * 70)
    print("REPLY:\n" + result["reply"])
    print("\nCARS RETURNED THIS TURN:", [(c["id"], c["title"][:50]) for c in result["cars"]])
    print("BOOKING_PROMPT:", result["booking_prompt"])
    print("MESSAGE COUNT (updated_messages):", len(result["updated_messages"]))


if __name__ == "__main__":
    main()
