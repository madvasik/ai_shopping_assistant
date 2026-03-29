# -*- coding: utf-8 -*-
from src.services import llm_counter


def test_increment_llm_counter_invokes_callback():
    calls = []

    def cb(name, preview):
        calls.append((name, preview))

    llm_counter.set_llm_counter_callback(cb)
    llm_counter.set_llm_response_callback(None)
    llm_counter.increment_llm_counter("classify_intent", "user: hello")
    assert calls == [("classify_intent", "user: hello")]


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
