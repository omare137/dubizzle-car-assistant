"""Throwaway live smoke test: conversation -> end_session -> compiled profile.

Run: uv run python -m app.agent._ltm_smoke_test
Runs a two-turn conversation as user 1 (omar.k), ends the session, prints the
compiled profile JSON and the get_profile_summary string.
"""

import json

from app.agent.long_term_memory import get_profile_summary
from app.agent.memory import create_session, end_session, run_turn_with_memory


def main() -> None:
    session_id = create_session(user_id=1)
    print(f"session (user 1 / omar.k): {session_id}")

    for msg in [
        "I'm looking for a Toyota SUV, budget under 150k AED",
        "Tell me more about the BZ4X",
    ]:
        print(f"\n===== USER: {msg!r}")
        result = run_turn_with_memory(session_id, msg, None)
        print(f"ASSISTANT: {result['reply'][:400]}")

    print("\n===== end_session -> compiled profile:")
    profile = end_session(session_id)
    print(json.dumps(profile, indent=2, ensure_ascii=False))

    print("\n===== get_profile_summary(1):")
    print(get_profile_summary(1))


if __name__ == "__main__":
    main()
