"""Tiny loader for versioned prompt files in `prompts/*.md` (see prompts/README.md)."""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"
_NAME = re.compile(r"^[a-z0-9_]+$")
_TIERS = {"agent", "fast"}


class PromptError(Exception):
    """Prompt file missing or malformed."""


@dataclass(frozen=True)
class Prompt:
    id: str
    version: int
    model_tier: str
    updated: str
    body: str

    def render(self, **values: str) -> str:
        """str.format substitution; values are inserted verbatim (never re-interpreted)."""
        try:
            return self.body.format(**values)
        except (KeyError, IndexError, ValueError) as e:
            raise PromptError(f"prompt {self.id}: bad placeholders ({type(e).__name__})") from e


def parse_prompt(text: str, name: str) -> Prompt:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise PromptError(f"prompt {name}: missing header")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise PromptError(f"prompt {name}: unterminated header") from None
    meta: dict[str, str] = {}
    for ln in lines[1:end]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            meta[k.strip()] = v.split("#", 1)[0].strip()
    if meta.get("id") != name:
        raise PromptError(f"prompt {name}: id must equal the file name stem")
    if not meta.get("version", "").isdigit():
        raise PromptError(f"prompt {name}: version must be an integer")
    if meta.get("model_tier") not in _TIERS:
        raise PromptError(f"prompt {name}: model_tier must be agent or fast")
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise PromptError(f"prompt {name}: empty body")
    return Prompt(name, int(meta["version"]), meta["model_tier"], meta.get("updated", ""), body)


@lru_cache(maxsize=32)
def load_prompt(name: str) -> Prompt:
    if not _NAME.fullmatch(name):
        raise PromptError("invalid prompt name")
    path = PROMPTS_DIR / f"{name}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise PromptError(f"prompt {name}: not found") from None
    return parse_prompt(text, name)
