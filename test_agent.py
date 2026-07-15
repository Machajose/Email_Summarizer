"""
A minimal agent, no Gmail yet — just proving the agent loop works with Groq.

THE CORE IDEA (the "agent loop"):
    1. We tell the LLM what tools (Python functions) it's allowed to call.
    2. We send it a task in plain English.
    3. The LLM responds with either a final answer, OR a request to call one
       of our functions (with arguments it picked itself).
    4. We actually run that function in Python, and send the result back.
    5. Repeat until the LLM has enough info to give a final answer.

This version has two toy tools that need zero setup, so you can see the
whole loop working right now. Once this feels solid, we swap in Gmail.
"""

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("Set GROQ_API_KEY in your .env file first")

GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


# ---------------------------------------------------------------------------
# TOOLS — plain Python functions the agent is allowed to call.
# No API keys, no OAuth, nothing to set up. Just here to prove the loop works.
# ---------------------------------------------------------------------------
def get_weather(city: str) -> dict:
    """Fake weather lookup, just for demo purposes."""
    fake_data = {
        "nairobi": {"condition": "Sunny", "temp_c": 24},
        "london": {"condition": "Rainy", "temp_c": 14},
        "tokyo": {"condition": "Cloudy", "temp_c": 19},
    }
    return fake_data.get(city.lower(), {"condition": "Unknown", "temp_c": None})


def add_numbers(a: float, b: float) -> float:
    """Add two numbers together."""
    return a + b


AVAILABLE_FUNCTIONS = {
    "get_weather": get_weather,
    "add_numbers": add_numbers,
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name, e.g. 'Nairobi'"}
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_numbers",
            "description": "Add two numbers together and return the result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# THE AGENT LOOP
# ---------------------------------------------------------------------------
def call_groq(messages: list[dict]) -> dict:
    response = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={"model": GROQ_MODEL, "messages": messages, "tools": TOOLS},
    )
    response.raise_for_status()
    return response.json()


def run_agent(task: str, max_steps: int = 6) -> str:
    messages = [{"role": "user", "content": task}]

    for step in range(max_steps):
        result = call_groq(messages)
        message = result["choices"][0]["message"]
        messages.append(message)

        tool_calls = message.get("tool_calls")

        if not tool_calls:
            return message.get("content", "")

        for call in tool_calls:
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"] or "{}")
            print(f"  [agent] calling {name}({args})")

            func = AVAILABLE_FUNCTIONS[name]
            output = func(**args)

            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(output),
            })

    return "Stopped after too many steps without a final answer."


if __name__ == "__main__":
    task = "What's the weather in Nairobi and Tokyo? Also, what's 47 + 89?"
    print(f"Task: {task}\n")
    answer = run_agent(task)
    print("\n--- FINAL ANSWER ---")
    print(answer)