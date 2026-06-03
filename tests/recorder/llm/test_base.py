from mymcp.recorder.llm.base import (
    LLMResponse,
    Message,
    ToolResult,
    ToolSchema,
    ToolUse,
    Usage,
)


def test_message_text_only():
    m = Message(role="user", content="hello")
    assert m.role == "user"
    assert m.content == "hello"
    assert m.tool_uses == []
    assert m.tool_results == []


def test_tool_use_roundtrip():
    t = ToolUse(id="t1", name="bash_probe", input={"command": "ls"})
    assert t.input["command"] == "ls"


def test_tool_result_default_not_error():
    tr = ToolResult(tool_use_id="t1", content="ok")
    assert tr.is_error is False


def test_tool_schema_fields():
    s = ToolSchema(name="x", description="d", input_schema={"type": "object"})
    assert s.name == "x" and s.description == "d"


def test_usage_default_zero():
    u = Usage()
    assert u.input_tokens == 0
    assert u.output_tokens == 0


def test_llm_response_end_turn():
    r = LLMResponse(
        text="done",
        tool_uses=[],
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=5),
    )
    assert r.usage_total == 15
    assert r.is_end_turn is True


def test_llm_response_tool_use_not_end_turn():
    r = LLMResponse(
        text="",
        tool_uses=[ToolUse(id="t1", name="x", input={})],
        stop_reason="tool_use",
        usage=Usage(input_tokens=1, output_tokens=1),
    )
    assert r.is_end_turn is False


def test_re_exports_from_init():
    from mymcp.recorder.llm import (
        LLMClient,
        LLMResponse,
        Message,
        ToolResult,
        ToolSchema,
        ToolUse,
        Usage,
    )

    # Verify all symbols are properly re-exported
    assert Usage().input_tokens == 0
    assert LLMResponse.__name__ == "LLMResponse"
    assert Message.__name__ == "Message"
    assert ToolUse.__name__ == "ToolUse"
    assert ToolResult.__name__ == "ToolResult"
    assert ToolSchema.__name__ == "ToolSchema"
    assert LLMClient.__name__ == "LLMClient"


def test_llm_client_protocol_accepts_json_schema():
    """The Protocol must accept json_schema as a keyword-only argument."""
    import inspect

    from mymcp.recorder.llm.base import LLMClient

    sig = inspect.signature(LLMClient.call)
    assert "json_schema" in sig.parameters
    assert sig.parameters["json_schema"].default is None
