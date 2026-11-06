import json

import httpx
import pytest

from data_platform.model_gateway.deepseek import (
    DeepSeekChatProvider,
    ModelProtocolError,
    OllamaChatProvider,
)


def test_native_tool_response_and_history_shape():
    captured = {}

    def handle(request):
        captured["request"] = request
        return httpx.Response(200, json={
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {"content": None, "tool_calls": [{
                    "id": "call-1", "type": "function",
                    "function": {"name": "lookup", "arguments": '{"id": 7}'},
                }]},
            }],
            "usage": {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23},
        })

    client = httpx.Client(transport=httpx.MockTransport(handle))
    provider = DeepSeekChatProvider(api_key="test-only", client=client)
    result = provider.complete(
        [{"role": "user", "content": "Lookup 7"}],
        [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
    )
    assert result.tool_calls[0].arguments == {"id": 7}
    assert result.usage["total_tokens"] == 23
    assert captured["request"].url.path == "/chat/completions"
    assert b'"tool_choice":"auto"' in captured["request"].content


def test_ollama_provider_uses_openai_compatible_endpoint_without_cloud_key():
    captured = {}

    def handle(request):
        captured["request"] = request
        return httpx.Response(200, json={
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "本地模型回答", "tool_calls": []},
            }],
        })

    provider = OllamaChatProvider(
        model="qwen2.5:14b",
        base_url="http://127.0.0.1:11434/v1",
        client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    result = provider.complete([{"role": "user", "content": "x"}], [])
    assert result.content == "本地模型回答"
    assert captured["request"].url.path == "/v1/chat/completions"


def test_bad_tool_json_is_returned_as_invalid_call():
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
        "choices": [{"finish_reason": "tool_calls", "message": {
            "content": "", "tool_calls": [{"id": "c", "function": {"name": "lookup", "arguments": "oops"}}],
        }}],
    })))
    result = DeepSeekChatProvider(api_key="test-only", client=client).complete([{"role": "user", "content": "x"}], [])
    assert result.tool_calls[0].invalid_arguments is True


def test_truncated_response_fails_instead_of_becoming_answer():
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
        "choices": [{"finish_reason": "length", "message": {"content": "partial"}}],
    })))
    with pytest.raises(ModelProtocolError, match="did not finish"):
        DeepSeekChatProvider(api_key="test-only", client=client).complete([{"role": "user", "content": "x"}], [])


def stream_response(chunks):
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    lines.append("data: [DONE]\n\n")
    return httpx.Response(200, text="".join(lines), headers={"content-type": "text/event-stream"})


def test_streamed_text_is_incremental_and_usage_is_preserved():
    captured = {}

    def handle(request):
        captured["payload"] = json.loads(request.content)
        return stream_response([
            {"choices": [{"delta": {"content": "你"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "好"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
        ])

    provider = DeepSeekChatProvider(api_key="test-only", client=httpx.Client(transport=httpx.MockTransport(handle)))
    events = list(provider.stream_complete([{"role": "user", "content": "x"}], []))
    assert [event.text for event in events[:-1]] == ["你", "好"]
    assert events[-1].reply.content == "你好"
    assert events[-1].reply.usage["total_tokens"] == 5
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["stream_options"] == {"include_usage": True}


def test_streamed_tool_arguments_are_joined_before_execution():
    client = httpx.Client(transport=httpx.MockTransport(lambda _: stream_response([
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "type": "function",
                           "function": {"name": "query_database", "arguments": '{"sql":'}}]}, "finish_reason": None}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0,
                           "function": {"arguments": '"SELECT 1"}'}}]}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ])))
    provider = DeepSeekChatProvider(api_key="test-only", client=client)
    events = list(provider.stream_complete([{"role": "user", "content": "x"}], []))
    assert len(events) == 1
    assert events[0].reply.tool_calls[0].arguments == {"sql": "SELECT 1"}


def test_stream_without_done_is_not_accepted_as_complete():
    body = 'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"stop"}]}\n\n'
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body)))
    provider = DeepSeekChatProvider(api_key="test-only", client=client)
    iterator = provider.stream_complete([{"role": "user", "content": "x"}], [])
    assert next(iterator).text == "partial"
    with pytest.raises(ModelProtocolError, match="without a valid finish"):
        list(iterator)
