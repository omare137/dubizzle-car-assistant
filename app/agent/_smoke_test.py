"""Throwaway manual smoke test for the LiteLLM -> Gemini wiring.

Run: uv run python -m app.agent._smoke_test
Not part of the pytest suite — just for eyeballing one real round trip.
"""

from app.agent.llm_client import get_completion


def main() -> None:
    response = get_completion(
        [
            {
                "role": "system",
                "content": "You are a helpful car dealership assistant for dubizzle cars in the UAE.",
            },
            {
                "role": "user",
                "content": "Say hello and confirm you're working in one sentence.",
            },
        ]
    )
    print("MODEL REPLY:")
    print(response.choices[0].message.content)
    usage = getattr(response, "usage", None)
    if usage:
        print(
            f"\nTOKEN USAGE: prompt={usage.prompt_tokens} "
            f"completion={usage.completion_tokens} total={usage.total_tokens}"
        )


if __name__ == "__main__":
    main()
