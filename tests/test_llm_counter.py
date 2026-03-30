# -*- coding: utf-8 -*-
from types import SimpleNamespace

from src.services import llm_counter


def test_increment_llm_counter_invokes_callback():
    calls = []

    def cb(name, preview, prompt_name=None):
        calls.append((name, preview, prompt_name))

    llm_counter.set_llm_counter_callback(cb)
    llm_counter.set_llm_response_callback(None)
    llm_counter.increment_llm_counter("classify_intent", "user: hello", "classify_intent")
    assert calls == [("classify_intent", "user: hello", "classify_intent")]


def test_update_llm_response_invokes_callback():
    calls = []

    def cb(preview, pt=None, ct=None):
        calls.append((preview, pt, ct))

    llm_counter.set_llm_counter_callback(None)
    llm_counter.set_llm_response_callback(cb)
    llm_counter.update_llm_response("да", prompt_tokens=5, completion_tokens=2)
    assert calls == [("да", 5, 2)]


def test_callbacks_swallow_errors():
    def bad_cb(*_args, **_kwargs):
        raise RuntimeError("boom")

    llm_counter.set_llm_counter_callback(bad_cb)
    llm_counter.increment_llm_counter("x")  # не пробрасывает

    llm_counter.set_llm_response_callback(bad_cb)
    llm_counter.update_llm_response("ok")  # не пробрасывает


def test_extract_usage_tokens_from_object():
    resp = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3)
    )
    assert llm_counter.extract_usage_tokens(resp) == (10, 3)


def test_extract_usage_tokens_from_dict_usage():
    resp = SimpleNamespace(usage={"prompt_tokens": 1, "completion_tokens": 2})
    assert llm_counter.extract_usage_tokens(resp) == (1, 2)


def test_extract_usage_tokens_input_output_aliases():
    resp = SimpleNamespace(usage=SimpleNamespace(input_tokens=5, output_tokens=7))
    assert llm_counter.extract_usage_tokens(resp) == (5, 7)


def test_extract_usage_tokens_missing():
    assert llm_counter.extract_usage_tokens(SimpleNamespace(usage=None)) == (None, None)
