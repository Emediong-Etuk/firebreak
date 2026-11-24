"""Render tool calls through the pinned Gemma 4 chat template (the exact text the model is
trained to emit), mirroring transformers' apply_chat_template jinja environment."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jinja2.sandbox import ImmutableSandboxedEnvironment

TEMPLATE = Path(__file__).resolve().parent / "pinned" / "chat_template.jinja"
TOOL_CALL_START, TOOL_CALL_END = "<|tool_call>", "<tool_call|>"


def _raise(msg):
    raise ValueError(msg)


@lru_cache(maxsize=1)
def _template():
    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True,
                                        extensions=["jinja2.ext.loopcontrols"])
    env.filters["tojson"] = lambda x, indent=None, ensure_ascii=False, separators=None, sort_keys=False: \
        json.dumps(x, indent=indent, ensure_ascii=ensure_ascii, separators=separators, sort_keys=sort_keys)
    env.globals["raise_exception"] = _raise
    return env.from_string(TEMPLATE.read_text())


def render_conversation(messages: list[dict], tools: list[dict] | None = None, **kw) -> str:
    return _template().render(messages=messages, tools=tools, bos_token="<bos>",
                              add_generation_prompt=False, **kw)


def render_tool_call(name: str, arguments: dict) -> str:
    """Return exactly the `<|tool_call>call:NAME{...}<tool_call|>` span the template produces
    for one assistant tool call."""
    msgs = [{"role": "user", "content": "x"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": name, "arguments": arguments}}]}]
    text = render_conversation(msgs)
    i = text.find(TOOL_CALL_START)
    j = text.find(TOOL_CALL_END, i)
    if i < 0 or j < 0:
        raise ValueError("template produced no tool call span")
    # The span ends at the FIRST <tool_call|> after the start; if a value itself contains
    # <tool_call|>, find the one that closes this call: the template always emits '}<tool_call|>'
    # as the very last thing for the call, so search from the end of the conversation instead.
    j = text.rfind("}" + TOOL_CALL_END)
    return text[i:j + 1 + len(TOOL_CALL_END)]


if __name__ == "__main__":
    print(render_tool_call("edit_file", {"filepath": "a.py", "old_string": 'x = "1"', "new_string": 'x = """2"""'}))
    print(render_tool_call("get_code_subgraph", {"nodes": ["a.b", "List[int]"]}))
