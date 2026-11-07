import pytest
from app.core.prompts import PROMPTS_DIR, PromptError, load_prompt, parse_prompt


@pytest.mark.parametrize("name,tier", [("answer", "agent"), ("summarize", "fast")])
def test_shipped_prompts_load(name, tier):
    p = load_prompt(name)
    assert p.id == name and p.version >= 1 and p.model_tier == tier
    assert "DATA" in p.body


def test_render_inserts_values_verbatim():
    p = load_prompt("answer")
    out = p.render(context="{x} {{y}}", question="q")
    assert "{x} {{y}}" in out


def test_render_missing_placeholder():
    with pytest.raises(PromptError):
        load_prompt("answer").render(context="c")


@pytest.mark.parametrize("name", ["../x", "a/b", "A", "", "no_such_prompt"])
def test_bad_or_missing_names(name):
    with pytest.raises(PromptError):
        load_prompt(name)


@pytest.mark.parametrize("text", [
    "no header",
    "---\nid: t\nversion: 1\nmodel_tier: agent\nbody",
    "---\nid: other\nversion: 1\nmodel_tier: agent\n---\nbody",
    "---\nid: t\nversion: x\nmodel_tier: agent\n---\nbody",
    "---\nid: t\nversion: 1\nmodel_tier: big\n---\nbody",
    "---\nid: t\nversion: 1\nmodel_tier: agent\n---\n  ",
])
def test_parse_rejects_malformed(text):
    with pytest.raises(PromptError):
        parse_prompt(text, "t")


def test_parse_ok_with_comment_in_header():
    p = parse_prompt("---\nid: t\nversion: 2\nmodel_tier: fast   # c\nupdated: 2026-01-01\n---\nhi {a}", "t")
    assert (p.version, p.model_tier, p.updated, p.render(a="z")) == (2, "fast", "2026-01-01", "hi z")


def test_every_prompt_file_has_valid_header():
    for f in PROMPTS_DIR.glob("*.md"):
        if f.stem != "README":
            assert load_prompt(f.stem).id == f.stem
