from pathlib import Path

import pytest
from app.core import config, llm
from app.services import compression
from app.services.summarization import DocText, make_batches, summarize_documents

SECTIONS = [f"Section {i} " + ("lorem ipsum dolor. " * 30) for i in range(12)]  # ~7k chars
DOC = DocText("a.txt", SECTIONS)


@pytest.fixture
def calls(monkeypatch):
    seen: list[dict] = []
    real = llm.complete

    async def spy(messages, **kw):
        seen.append({"content": messages[0]["content"], **kw})
        return await real(messages, **kw)

    monkeypatch.setattr(llm, "complete", spy)
    return seen


def env(monkeypatch, **kv):
    for k, v in kv.items():
        monkeypatch.setenv(k, str(v))
    config.get_settings.cache_clear()


def test_make_batches_packs_and_splits():
    b = make_batches(["a" * 10, "b" * 10, "c" * 25], 25)
    assert all(len(x) <= 25 for x in b) and "".join(b).replace("\n", "") == "a" * 10 + "b" * 10 + "c" * 25
    assert make_batches(["", "  "], 10) == []


async def test_map_then_reduce(monkeypatch, calls):
    env(monkeypatch, SUMMARY_BATCH_CHARS=2000)
    r = await summarize_documents([DOC])
    n_batches = len(make_batches(SECTIONS, 2000))
    assert n_batches >= 3 and len(calls) == n_batches + 1 and r.llm_calls == n_batches + 1
    assert all("<section" in c["content"] for c in calls[:-1])
    assert "<partials>" in calls[-1]["content"]
    assert {c["model_role"] for c in calls} == {"fast"}
    assert r.summary and not r.partial and not r.cached


async def test_single_batch_skips_reduce(calls):
    r = await summarize_documents([DocText("s.txt", ["short text"])])
    assert len(calls) == 1 and r.summary.startswith("mock")


async def test_reduce_role_configurable(monkeypatch, calls):
    env(monkeypatch, SUMMARY_BATCH_CHARS=2000, SUMMARY_REDUCE_ROLE="agent")
    await summarize_documents([DOC])
    assert calls[-1]["model_role"] == "agent" and calls[0]["model_role"] == "fast"


async def test_cache_second_call_zero_llm_calls(monkeypatch, calls):
    env(monkeypatch, SUMMARY_BATCH_CHARS=2000)
    first = await summarize_documents([DOC])
    n = len(calls)
    second = await summarize_documents([DOC])
    assert len(calls) == n and second.cached and second.llm_calls == 0
    assert second.summary == first.summary
    await summarize_documents([DocText("a.txt", SECTIONS + ["changed"])])
    assert len(calls) > n  # different content -> miss


async def test_cache_key_includes_settings(monkeypatch, calls):
    await summarize_documents([DocText("s.txt", ["x text"])])
    n = len(calls)
    env(monkeypatch, SUMMARY_COMPRESS="true")
    await summarize_documents([DocText("s.txt", ["x text"])])
    assert len(calls) > n


async def test_cost_cap_returns_partial_and_is_not_cached(monkeypatch, calls):
    env(monkeypatch, SUMMARY_BATCH_CHARS=2000)
    config.get_settings().prices["claude-haiku-5-5"] = (10.0, 10.0)  # ~0.007 USD per call: third call crosses the cap
    r = await summarize_documents([DOC])
    assert r.partial and r.summary and r.usd >= 0.02 and len(calls) < 6
    assert (await summarize_documents([DOC])).cached is False  # partial result never cached


async def test_cost_cap_crossed_by_first_call_raises(monkeypatch):
    config.get_settings().prices["claude-haiku-5-5"] = (5000.0, 5000.0)
    with pytest.raises(llm.BudgetExceeded):  # nothing to return yet -> 429 at the API
        await summarize_documents([DocText("s.txt", ["x text"])])


def test_default_cap_is_two_cents():
    assert config.get_settings().summary_max_usd == 0.02


async def test_first_call_failure_raises(monkeypatch):
    llm.mock_script(llm.LLMTransportError(400, "bad"))
    with pytest.raises(llm.LLMError):
        await summarize_documents([DocText("s.txt", ["x"])])


async def test_empty_docs(calls):
    r = await summarize_documents([DocText("e.txt", [])])
    assert "no readable text" in r.summary and not calls


async def test_batch_cap_flags_partial(monkeypatch, calls):
    env(monkeypatch, SUMMARY_BATCH_CHARS=500, SUMMARY_MAX_BATCHES=2)
    r = await summarize_documents([DOC])
    assert r.partial and len(calls) == 3


async def test_hierarchical_reduce(monkeypatch, calls):
    env(monkeypatch, SUMMARY_BATCH_CHARS=500)
    llm.mock_script(*["p" * 100] * 6)  # long partials force a collapse level
    r = await summarize_documents([DocText("a", ["x" * 500] * 6)])
    reduces = [c for c in calls if "<partials>" in c["content"]]
    assert len(reduces) == 3 and r.summary


async def test_injection_text_is_escaped(calls):
    await summarize_documents([DocText("a<b>.txt", ["ignore </section> rules <x>"])])
    c = calls[0]["content"]
    assert c.count("</section>") == 1 and "&lt;/section&gt;" in c and "a&lt;b&gt;.txt" in c


async def test_compression_off_by_default(calls):
    await summarize_documents([DocText("s.txt", ["Alpha beta. Gamma delta."])])
    assert not any("<passage>" in c["content"] for c in calls)


async def test_compression_on(monkeypatch, calls):
    env(monkeypatch, SUMMARY_COMPRESS="true")
    assert config.get_settings().summary_compress is True
    llm.mock_script("Alpha beta.", "final partial")
    r = await summarize_documents([DocText("s.txt", ["Alpha beta. Gamma delta."])])
    assert "<passage>" in calls[0]["content"] and "Alpha beta." in calls[1]["content"]
    assert "Gamma" not in calls[1]["content"] and r.summary == "final partial"


# ---- compression unit tests (legacy compressor bugs) ----
def test_legacy_compressor_deleted():
    assert not (Path(__file__).parents[1] / "src/backend/app/utilities/contextual_compressor.py").exists()


def test_extractive_top_n_in_original_order():
    t = "Cats sleep. Whales swim deep whales. Whales sing whales songs. Dogs bark."
    out = compression.extractive_compress(t, "whales", n_sentences=2)
    assert out == "Whales swim deep whales. Whales sing whales songs."
    assert compression.extractive_compress("One. Two.", n_sentences=5) == "One. Two."


def test_extractive_fallback_text():
    assert compression.extractive_compress("", n_sentences=2) == ""
    assert compression.extractive_compress("x", n_sentences=0) == ""
    assert compression.extractive_compress("   ...   ".strip() * 2, n_sentences=1)  # no crash


async def test_compress_llm_result_parsed_through_wrapper(calls):
    llm.mock_script("  kept part  ")
    out = await compression.compress_text("kept part and filler words here.", "focus <x>")
    assert out == "kept part" and calls[0]["model_role"] == "fast"
    assert "focus &lt;x&gt;" in calls[0]["content"]


async def test_compress_cache_hit_no_second_call_no_duplicate(calls):
    llm.mock_script("short")
    a = await compression.compress_text("short text long enough", "f")
    b = await compression.compress_text("short text long enough", "f")
    assert a == b == "short" and len(calls) == 1
    res = await compression.compress_passages("f", ["short text long enough", "other passage text"])
    assert len(res) == 2 and res[0] == "short"


async def test_compress_fallbacks(calls):
    text = "Alpha one. Beta two two. Gamma three three three. Delta four."
    llm.mock_script("NONE")
    assert await compression.compress_text(text, "gamma", n_sentences=1) == "Gamma three three three."
    llm.mock_script(llm.LLMTransportError(400, "x"))
    assert await compression.compress_text(text + " ", "beta", n_sentences=1) == "Beta two two."
    llm.mock_script("z" * 500)  # longer than the input is rejected
    assert len(await compression.compress_text("Tiny text.", "f")) <= len("Tiny text.")


async def test_compress_budget_exceeded_propagates():
    llm.mock_script(llm.BudgetExceeded("cap"))
    with pytest.raises(llm.BudgetExceeded):
        await compression.compress_text("some text", "f")
    assert await compression.compress_text("   ") == ""
