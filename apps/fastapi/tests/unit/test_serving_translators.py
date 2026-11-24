"""
Unit tests for the pure translators: think-tag splitting, Anthropic
request/response/stream mapping details, Ollama and Responses mappings,
compaction / context editing, media engine bodies (stable-diffusion.cpp),
multipart parsing and SSE helpers.
"""

from __future__ import annotations

import json

import pytest
from app.services.serving import compaction as comp
from app.services.serving.errors import ServingError, error_body, stream_error_frame
from app.services.serving.handlers_media import (
    multipart_boundary,
    multipart_fields,
    sd_cpp_response_to_openai,
    sd_cpp_txt2img_body,
)
from app.services.serving.sse import (
    compile_model_rewrite,
    iter_ndjson_lines,
    iter_sse_frames,
    parse_sse_json,
    rewrite_model_bytes,
)
from app.services.serving.thinking import ThinkTagSplitter, split_think_tags
from app.services.serving.translate import anthropic as tr_a
from app.services.serving.translate import ollama as tr_o
from app.services.serving.translate import responses as tr_r

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------ thinking


@pytest.mark.parametrize("size", [1, 2, 3, 7, 100])
def test_think_splitter_any_chunking(size):
    text = "<think>\nplan it\n</think>\n\nAnswer: 42"
    s = ThinkTagSplitter()
    reasoning, visible = "", ""
    for i in range(0, len(text), size):
        r, v = s.feed(text[i : i + size])
        reasoning += r
        visible += v
    r, v = s.flush()
    reasoning += r
    visible += v
    assert reasoning.strip() == "plan it" and visible == "Answer: 42"


def test_think_splitter_plain_text_and_unclosed():
    assert split_think_tags("no tags here") == ("", "no tags here")
    assert split_think_tags("<think>never closed") == ("never closed", "")
    assert split_think_tags("a < b") == ("", "a < b")


# ----------------------------------------------------------------- anthropic


def test_anthropic_request_mapping_details():
    out = tr_a.anthropic_request_to_openai(
        {
            "model": "m",
            "max_tokens": 10,
            "system": "sys",
            "messages": [
                {"role": "user", "content": "q"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "hmm", "signature": "x"},
                        {"type": "text", "text": "calling"},
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "f",
                            "input": {"a": 1},
                        },
                        {"type": "tool_use", "id": "t2", "name": "g", "input": {}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": [{"type": "text", "text": "r1"}],
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "t2",
                            "content": "r2",
                            "is_error": True,
                        },
                        {"type": "text", "text": "go on"},
                    ],
                },
            ],
            "tools": [
                {"name": "f", "input_schema": {"type": "object"}, "strict": True},
                {"type": "web_search_20260209", "name": "web_search"},
            ],
            "tool_choice": {"type": "tool", "name": "f"},
            "output_config": {
                "effort": "xhigh",
                "format": {"type": "json_schema", "schema": {"type": "object"}},
            },
            "thinking": {"type": "disabled"},
        }
    )
    msgs = out["messages"]
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[2]["content"] == "calling" and len(msgs[2]["tool_calls"]) == 2
    assert msgs[3] == {"role": "tool", "tool_call_id": "t1", "content": "r1"}
    assert msgs[4] == {"role": "tool", "tool_call_id": "t2", "content": "r2"}
    assert msgs[5] == {"role": "user", "content": "go on"}
    assert out["tool_choice"] == {"type": "function", "function": {"name": "f"}}
    assert out["tools"][0]["function"]["strict"] is True
    assert out["tools"][1]["function"]["name"] == "web_search"
    assert out["reasoning_effort"] == "high"
    assert out["chat_template_kwargs"] == {"enable_thinking": False}
    assert out["response_format"]["type"] == "json_schema"


def test_anthropic_requires_max_tokens():
    with pytest.raises(ServingError):
        tr_a.anthropic_request_to_openai(
            {"model": "m", "messages": [{"role": "user", "content": "x"}]}
        )


def test_anthropic_response_length_and_usage():
    out = tr_a.openai_response_to_anthropic(
        {
            "choices": [{"message": {"content": "partial"}, "finish_reason": "length"}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 4},
            },
        },
        "req-model",
    )
    assert out["stop_reason"] == "max_tokens" and out["model"] == "req-model"
    assert out["usage"]["input_tokens"] == 6 and out["usage"]["cache_read_input_tokens"] == 4
    assert out["id"].startswith("msg_")


async def test_anthropic_stream_event_sequence():
    async def frames():
        for chunk in (
            {"choices": [{"delta": {"content": "<thi"}}]},
            {"choices": [{"delta": {"content": "nk>why</think>Hi"}}]},
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "function": {"name": "f", "arguments": "{}"},
                                }
                            ]
                        }
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
        ):
            yield f"data: {json.dumps(chunk)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    builder = tr_a.AnthropicStreamBuilder("m")
    events = []
    async for raw in tr_a.openai_stream_to_anthropic(frames(), builder):
        lines = raw.decode().splitlines()
        events.append((lines[0][len("event: ") :], json.loads(lines[1][len("data: ") :])))
    names = [e for e, _ in events]
    assert names[:2] == ["message_start", "ping"]
    starts = [d["content_block"]["type"] for e, d in events if e == "content_block_start"]
    assert starts == ["thinking", "text", "tool_use"]
    sig = [
        d
        for e, d in events
        if e == "content_block_delta" and d["delta"]["type"] == "signature_delta"
    ]
    assert sig and sig[0]["delta"]["signature"].startswith("gd1:")
    delta = [d for e, d in events if e == "message_delta"][0]
    assert delta["delta"]["stop_reason"] == "tool_use" and delta["usage"]["output_tokens"] == 2
    assert names[-1] == "message_stop"


# -------------------------------------------------------------------- ollama


def test_ollama_mappings():
    out = tr_o.ollama_chat_to_openai(
        {
            "model": "m",
            "messages": [
                {"role": "user", "content": "look", "images": ["aGk="]},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "f", "arguments": {"x": 1}}}],
                },
                {"role": "tool", "content": "res", "tool_name": "f"},
            ],
            "format": {"type": "object"},
            "options": {"repeat_penalty": 1.1, "seed": 7, "stop": "END"},
            "think": "high",
            "keep_alive": "5m",
        }
    )
    assert out["messages"][0]["content"][1]["image_url"]["url"] == "data:image/png;base64,aGk="
    assert out["messages"][1]["tool_calls"][0]["function"]["arguments"] == '{"x": 1}'
    assert out["messages"][2]["role"] == "tool"
    assert out["response_format"]["type"] == "json_schema"
    assert out["repetition_penalty"] == 1.1 and out["seed"] == 7 and out["stop"] == ["END"]
    assert out["reasoning_effort"] == "high" and out["stream"] is True
    path, body = tr_o.ollama_generate_to_openai(
        {"model": "m", "prompt": "p", "raw": True, "suffix": "s"}
    )
    assert path == "v1/completions" and body["suffix"] == "s"
    legacy = tr_o.ollama_embed_to_openai({"model": "m", "prompt": "x"}, legacy=True)
    assert legacy["input"] == "x"


# ----------------------------------------------------------------- responses


def test_responses_mapping_rejects_state_and_maps_formats():
    with pytest.raises(ServingError):
        tr_r.responses_request_to_chat({"model": "m", "input": "x", "previous_response_id": "r"})
    out = tr_r.responses_request_to_chat(
        {
            "model": "m",
            "input": [
                {"role": "developer", "content": "rules"},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "see"},
                        {
                            "type": "input_image",
                            "image_url": "https://x/y.png",
                            "detail": "low",
                        },
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "r",
                    "schema": {"type": "object"},
                }
            },
            "tool_choice": {"type": "function", "name": "f"},
            "top_logprobs": 2,
        }
    )
    assert out["messages"][0] == {"role": "system", "content": "rules"}
    assert out["messages"][1]["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "https://x/y.png", "detail": "low"},
    }
    assert out["response_format"]["json_schema"]["name"] == "r"
    assert out["tool_choice"] == {"type": "function", "function": {"name": "f"}}
    resp = tr_r.chat_response_to_responses(
        {
            "choices": [{"message": {"content": "x"}, "finish_reason": "length"}],
            "usage": {},
        },
        {"model": "m"},
    )
    assert (
        resp["status"] == "incomplete"
        and resp["incomplete_details"]["reason"] == "max_output_tokens"
    )


# ---------------------------------------------------------------- compaction


def test_compaction_signature_and_placement():
    block = comp.make_compaction_block("summary text")
    assert (
        comp.validate_incoming_compaction([{"role": "user", "content": [block]}]) == "summary text"
    )
    with pytest.raises(ServingError) as exc:
        comp.validate_incoming_compaction(
            [{"role": "user", "content": "x"}, {"role": "user", "content": [block]}]
        )
    assert exc.value.code == "compaction_block_misplaced"
    with pytest.raises(ServingError):
        comp.validate_incoming_compaction(
            [{"role": "user", "content": [dict(block, signature="gd1:forged")]}]
        )


def test_compaction_request_validation():
    with pytest.raises(ServingError):
        comp.validate_compaction_request(
            {"compaction": {"type": "summarize"}, "context_management": {"edits": []}}
        )
    with pytest.raises(ServingError):
        comp.validate_compaction_request(
            {
                "context_management": {
                    "edits": [
                        {
                            "type": comp.COMPACT_EDIT_TYPE,
                            "trigger": {"type": "input_tokens", "value": 10},
                        }
                    ]
                }
            }
        )
    edit = {
        "type": comp.COMPACT_EDIT_TYPE,
        "trigger": {"type": "input_tokens", "value": 60000},
    }
    comp.validate_compaction_request({"context_management": {"edits": [edit]}})
    assert comp.trigger_tokens(edit) == 60000
    assert comp.trigger_tokens({"type": comp.COMPACT_EDIT_TYPE}) == comp.DEFAULT_TRIGGER_TOKENS


def test_context_editing_clears_old_tool_results_and_thinking():
    msgs = []
    for i in range(4):
        msgs.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "t" * 50, "signature": "s"},
                    {
                        "type": "tool_use",
                        "id": f"t{i}",
                        "name": "keep" if i == 0 else "f",
                        "input": {"q": i},
                    },
                ],
            }
        )
        msgs.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"t{i}",
                        "content": "r" * 100,
                    }
                ],
            }
        )
    edits = [
        {
            "type": comp.CLEAR_TOOL_USES,
            "keep": {"type": "tool_uses", "value": 1},
            "exclude_tools": ["keep"],
            "clear_tool_inputs": True,
        },
        {"type": comp.CLEAR_THINKING, "keep": {"type": "thinking_turns", "value": 1}},
    ]
    new, applied = comp.apply_clearing_edits(msgs, edits, input_tokens=100000)
    results = [m["content"][0]["content"] for m in new if m["role"] == "user"]
    assert results[0] == "r" * 100  # excluded tool kept
    assert results[1] == [{"type": "text", "text": "[cleared]"}]
    assert results[3] == "r" * 100  # most recent kept
    assert applied[0]["cleared_tool_uses"] == 2 and applied[1]["cleared_thinking_turns"] == 3
    assert new[2]["content"][0]["input"] == {}  # tool input cleared
    assert msgs[1]["content"][0]["content"] == "r" * 100  # original untouched


def test_summary_request_and_parse():
    req = comp.build_summary_request(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "system": "s",
            "max_tokens": 100,
        },
        "served",
        None,
    )
    assert req["model"] == "served" and req["max_tokens"] == 256
    assert "hello" in req["messages"][1]["content"]
    summary, stop = comp.summary_from_openai_response(
        {
            "choices": [
                {
                    "message": {"content": "<think>x</think>The summary"},
                    "finish_reason": "stop",
                }
            ]
        }
    )
    assert (summary, stop) == ("The summary", "compaction")


# --------------------------------------------------------------------- media


def test_sd_cpp_bodies():
    body = sd_cpp_txt2img_body({"prompt": "cat", "size": "512x768", "n": 2, "seed": 3})
    assert (body["width"], body["height"], body["batch_count"], body["seed"]) == (
        512,
        768,
        2,
        3,
    )
    out = sd_cpp_response_to_openai([{"data": "AAA"}, {"data": "BBB"}], "b64_json")
    assert [d["b64_json"] for d in out["data"]] == ["AAA", "BBB"]
    url = sd_cpp_response_to_openai([{"data": "AAA"}], "url")
    assert url["data"][0]["url"].startswith("data:image/png;base64,")


def test_multipart_parsing():
    boundary = "XyZ"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\nstt-test\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\nRIFFDATA\r\n--{boundary}--\r\n"
    ).encode()
    assert multipart_boundary(f"multipart/form-data; boundary={boundary}") == b"XyZ"
    parts = multipart_fields(body, b"XyZ")
    assert parts["model"][1] == b"stt-test" and parts["file"][1] == b"RIFFDATA"


# ----------------------------------------------------------------- sse/errors


async def test_sse_and_ndjson_framing_across_chunks():
    async def chunks():
        for piece in (b'data: {"a"', b": 1}\n", b"\ndata: [DONE]\n\n"):
            yield piece

    frames = [f async for f in iter_sse_frames(chunks())]
    assert parse_sse_json(frames[0])[1] == {"a": 1} and parse_sse_json(frames[1])[2] is True

    async def lines():
        for piece in (b'{"x":1}\n{"y"', b":2}\n"):
            yield piece

    assert [json.loads(line) async for line in iter_ndjson_lines(lines())] == [
        {"x": 1},
        {"y": 2},
    ]


def test_model_rewrite_only_exact_field():
    rw = compile_model_rewrite("served-q4", "public")
    out = rewrite_model_bytes(b'{"model": "served-q4", "text": "served-q4"}', rw)
    assert out == b'{"model":"public", "text": "served-q4"}'
    assert compile_model_rewrite("same", "same") is None
    mlx = compile_model_rewrite("gemma--1", "gemma", ("default_model",))
    assert rewrite_model_bytes(b'{"model":"default_model"}', mlx) == b'{"model":"gemma"}'
    assert rewrite_model_bytes(b'{"model":"gemma--1"}', mlx) == b'{"model":"gemma"}'


def test_error_shapes():
    assert (
        error_body("openai", 404, "model_not_found", "m")["error"]["type"]
        == "invalid_request_error"
    )
    anth = error_body("anthropic", 503, "model_not_ready", "m")
    assert (
        anth["error"]["type"] == "overloaded_error"
        and "[code=model_not_ready]" in anth["error"]["message"]
    )
    assert error_body("ollama", 400, "x", "bad") == {"error": "bad"}
    err = ServingError.upstream_timeout()
    assert stream_error_frame("openai", err).endswith(b"data: [DONE]\n\n")
    assert stream_error_frame("anthropic", err).startswith(b"event: error\n")
    assert json.loads(stream_error_frame("ollama", err))["done"] is True
