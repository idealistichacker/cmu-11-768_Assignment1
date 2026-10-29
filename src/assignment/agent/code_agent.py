"""The Part 1 coding agent: fix a software issue and submit a git patch."""

from __future__ import annotations

import json
from typing import Any

from assignment.agent.base import (
    DEFAULT_COMPACTION_KEEP_RECENT_STEPS,
    DEFAULT_COMPACTION_MAX_TOKENS,
    Agent,
    format_tool_output,          # 新增导入
)
from assignment.agent.tools import EXECUTE_TOOL, SEND_MESSAGE_TOOL
from assignment.env import Environment

# 模型可以覆盖的 execute 可选参数；未传的交给 Environment.execute 的默认值。
EXECUTE_OPTIONS = ("timeout", "cwd", "env", "shell")

# Standing instructions for the coding agent. `{system_information}` and
# `{cwd}` are filled from the sandbox, not from the machine running this code.
CODE_AGENT_SYSTEM_PROMPT_TEMPLATE = """You are an autonomous software engineering agent. You resolve issues in a code repository by working in a terminal on a sandboxed machine.

Work in a loop: reason briefly about what to do next, then act by calling a tool. Each tool result is your observation of the environment. Keep acting through tool calls until the task is complete; describing a command in text does not run it.

Guidelines:
- Explore the repository and reproduce the problem before changing code.
- Make focused, minimal changes that fix the root cause. Do not edit existing tests unless the task asks you to.
- Verify your change by running the relevant tests or a reproduction script.
- Keep command output short: search with grep and read files in line ranges.

The repository is at {cwd}. Your commands run on this machine:

<system_information>
{system_information}
</system_information>"""

CODE_AGENT_TASK_PROMPT_TEMPLATE = """Resolve the following issue in the repository at {cwd}.

<issue>
{task}
</issue>"""

# Appended to the system prompt only when skills are loaded. It lists each
# skill's name and description; the full body stays out of the prompt until
# the model calls `invoke_skill`.
CODE_AGENT_SKILLS_PROMPT_TEMPLATE = """

Reusable skills are available. Call `invoke_skill` with a skill's name to load its full instructions, and follow them in place of your default approach.

<skills>
{catalog}
</skills>"""


def tool_message(call_id: str, content: str) -> dict[str, str]:
    """One observation, linked to the call it answers."""
    return {"role": "tool", "tool_call_id": call_id, "content": content}

class CodeAgent(Agent):
    """An agent that fixes a software issue and submits a git patch."""

    def __init__(
        self,
        task: str,
        environment: Environment,
        model: str | None = None,
        logs_save_path: str | None = None,
        step_limit: int = 100,
        skills_path: str | None = None,
        auto_stop_environment: bool = True,
        compact_threshold_tokens: int | None = None,
        compaction_keep_recent_steps: int = DEFAULT_COMPACTION_KEEP_RECENT_STEPS,
        compaction_max_tokens: int = DEFAULT_COMPACTION_MAX_TOKENS,
    ):
        super().__init__(
            environment=environment,
            model=model,
            logs_save_path=logs_save_path,
            step_limit=step_limit,
            skills_path=skills_path,
            auto_stop_environment=auto_stop_environment,
            compact_threshold_tokens=compact_threshold_tokens,
            compaction_keep_recent_steps=compaction_keep_recent_steps,
            compaction_max_tokens=compaction_max_tokens,
        )
        self.task = task
        self.submitted_patch = ""

        # TODO(Part 1.3): Make the `execute` and `send_message` tools available
        self.tools.extend([EXECUTE_TOOL, SEND_MESSAGE_TOOL])

        # to the agent.

        # TODO(1.1.b): Construct the system prompt and task_prompt. These
        # should be usable by the `Agent.build_prompt` method.
        # json.dumps quotes each value and, with indent=2, reproduces the
        # required block layout; the keys keep this insertion order.
        system_information = json.dumps(
            {
                "machine": self.env.machine,
                "release": self.env.release,
                "system": self.env.system,
                "version": self.env.version,
            },
            indent=2,
        )
        self.system_prompt = CODE_AGENT_SYSTEM_PROMPT_TEMPLATE.format(
            cwd=self.env.cwd,
            system_information=system_information,
        )
        self.task_prompt = CODE_AGENT_TASK_PROMPT_TEMPLATE.format(
            cwd=self.env.cwd,
            task=self.task,
        )
        # TODO(1.4): If any skills are available to the agent, make their
        # descriptions/metadata available to the agent in the prompt.
        if self.skills:
            catalog = "\n".join(skill["metadata"] for skill in self.skills.values())
            self.system_prompt += CODE_AGENT_SKILLS_PROMPT_TEMPLATE.format(
                catalog=catalog
            )

    def execute_tool_calls(
        self, tool_calls: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        """Execute ``execute``, ``send_message``, and ``invoke_skill`` calls."""

        # TODO(Part 1.3): Parse each call, execute recognized tools, and return
        # one message per call (there may be multiple tool calls in one agent
        # response!). Malformed JSON and unknown tools must become recoverable
        # observations relayed to the agent instead of exceptions.
        """
        for 每个 call:
        取出 id、name、arguments 字符串
        已经提交过了吗？      → 是：返回“已提交，不执行”
        json.loads 失败？     → 返回错误
        解析结果不是字典？     → 返回错误
        name == "execute"：
            缺少 command？    → 返回错误
            执行命令，格式化输出 → 返回结果
        name == "send_message"：
            finished = True   → 返回确认
        其他名字              → 返回“未知工具”
        
        Args:
            tool_calls:
                "tool_calls": [
                {
                    "id": "call_abc123",
                    "type": "function",
                    "function": {
                    "name": "get_current_weather",
                    "arguments": "{\n\"location\": \"Boston, MA\"\n}"
                    }
                }
                ]
        Returns:
            {"role": "tool", "tool_call_id": "call_0", "content": "a.py"}
        """
        observations: list[dict[str, str]] = []
        for call in tool_calls:
            call_id = call.get("id", "")
            function = call.get("function") or {}
            name = function.get("name")
            raw_arguments = function.get("arguments") or "{}"

            # 完成任务之后不再执行任何操作，但这个调用仍然需要一条回复。
            if self.finished:
                observations.append(tool_message(
                    call_id, "Error: the task was already submitted; this call was not run."
                ))
                continue

            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                observations.append(tool_message(
                    call_id,
                    f"Error: arguments are not valid JSON ({exc}). "
                    "Re-send the call with a JSON object.",
                ))
                continue
            if not isinstance(arguments, dict):
                observations.append(tool_message(
                    call_id, "Error: arguments must be a JSON object."
                ))
                continue

            if name == "execute":
                command = arguments.get("command")
                if not isinstance(command, (str, list)) or not command:
                    observations.append(tool_message(
                        call_id, "Error: `execute` needs a non-empty `command` string or list."
                    ))
                    continue
                # 只转发已知的可选参数；多余的键会让 execute() 报 TypeError。
                options = {key: arguments[key] for key in EXECUTE_OPTIONS if key in arguments}
                result = self.env.execute(command, **options)
                # `output` 已包含 stdout 和 stderr，只展示它和退出码，避免重复。
                shown = {
                    "output": result.get("output", ""),
                    "returncode": result.get("returncode"),
                }
                if result.get("exception_info"):
                    shown["exception_info"] = result["exception_info"]
                observations.append(tool_message(call_id, format_tool_output(shown)))

            elif name == "send_message":
                self.finished = True
                observations.append(tool_message(call_id, "Message sent. The task is complete."))

            # `invoke_skill` is only registered when skills were loaded.
            elif name == "invoke_skill" and self.skills:
                skill_name = arguments.get("name")
                if not isinstance(skill_name, str) or not skill_name:
                    observations.append(tool_message(
                        call_id, "Error: `invoke_skill` needs a string `name`."
                    ))
                elif skill_name not in self.skills:
                    observations.append(tool_message(
                        call_id,
                        f"Error: unknown skill {skill_name!r}. "
                        f"Available skills: {', '.join(self.skills)}.",
                    ))
                else:
                    # Progressive disclosure: the full skill body is revealed only now.
                    observations.append(tool_message(call_id, self.skills[skill_name]["content"]))

            else:
                available = ", ".join(tool["function"]["name"] for tool in self.tools)
                observations.append(tool_message(
                    call_id, f"Error: unknown tool {name!r}. Available tools: {available}."
                ))

        return observations
