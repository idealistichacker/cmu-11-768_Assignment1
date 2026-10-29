"""Local tests for TODO 1.1.a, using a fake environment and a fake model.

Nothing here launches Modal or calls a real LLM, so it is free and offline.
Run with:  uv run pytest tests/test_build_prompt.py -v
"""

import json

import pytest
from openai.types.chat import ChatCompletion

from assignment.agent.base import Agent


class FakeEnvironment:
    """Stands in for the Modal sandbox.

    The agent only reads a few attributes and calls `execute`, so that is all
    the fake needs. It records commands instead of running them.
    """

    cwd = "/testbed"
    system, release, version, machine = "Linux", "6.1.0", "#1 SMP", "x86_64"

    def __init__(self):
        self.commands = []

    def execute(self, command, **kwargs):
        self.commands.append(command)
        return {"output": f"ran: {command}", "returncode": 0}


def make_completion(message: dict) -> ChatCompletion:
    """Wrap an assistant message the way the real API would return it."""
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "fake-model",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}],
        }
    )


def tool_call_message(call_id: str, command: str) -> dict:
    """An assistant message that asks to run one command."""
    return {
        "role": "assistant",
        "content": "Calling execute.",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "execute", "arguments": json.dumps({"command": command})},
            }
        ],
    }


@pytest.fixture
def agent(monkeypatch):
    """A base Agent with fake credentials, a fake environment, and set prompts."""
    # Agent.__init__ refuses to start without these; no request is ever sent.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:0/v1")
    agent = Agent(FakeEnvironment(), model="fake-model", auto_stop_environment=False)
    # Subclasses set these after Agent.__init__, so the test does the same.
    agent.system_prompt = "SYSTEM RULES"
    agent.task_prompt = "FIX THE BUG"
    return agent


def roles(messages):
    return [m["role"] for m in messages]


def test_first_prompt_is_system_then_task(agent):
    prompt = agent.build_prompt()
    assert roles(prompt) == ["system", "user"]
    assert prompt[0]["content"] == "SYSTEM RULES"
    assert prompt[1]["content"] == "FIX THE BUG"


def test_history_is_replayed_in_order(agent):
    action = tool_call_message("call_0", "ls")
    observation = {"role": "tool", "tool_call_id": "call_0", "content": "a.py"}
    agent.record_messages(action, observation)

    prompt = agent.build_prompt()
    assert roles(prompt) == ["system", "user", "assistant", "tool"]
    assert prompt[2]["tool_calls"][0]["id"] == prompt[3]["tool_call_id"]


def test_prompt_and_history_do_not_share_objects(agent):
    action = tool_call_message("call_0", "ls")
    agent.record_messages(action)
    action["content"] = "changed by caller"
    #[system, user assistant user]

    prompt = agent.build_prompt()
    prompt.append({"role": "user", "content": "extra"})

    assert agent.build_prompt()[2]["content"] == "Calling execute."
    assert len(agent.build_prompt()) == 3


def test_prompts_are_read_on_every_call(agent):
    agent.system_prompt = "NEW RULES"
    assert agent.build_prompt()[0]["content"] == "NEW RULES"


def test_fake_model_sees_previous_action_and_result(agent):
    """Drive two model calls by hand, the way the 1.2 loop will."""
    scripted = [tool_call_message("call_0", "ls -la"), tool_call_message("call_1", "pwd")]
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)  # remember exactly what was sent
        return make_completion(scripted[len(calls) - 1])

    # Replace the one method that would hit the network.
    agent.client.chat.completions.create = fake_create

    message = agent.query_language_model()
    agent.record_messages(message)
    command = json.loads(message["tool_calls"][0]["function"]["arguments"])["command"]
    result = agent.env.execute(command)
    agent.record_messages(
        {"role": "tool", "tool_call_id": "call_0", "content": result["output"]}
    )
    agent.query_language_model()

    assert agent.env.commands == ["ls -la"]
    second_request = calls[1]["messages"]
    assert roles(second_request) == ["system", "user", "assistant", "tool"]
    assert second_request[3]["content"] == "ran: ls -la"
    assert agent.steps_taken == 2
