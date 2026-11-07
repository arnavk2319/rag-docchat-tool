# Prompts

Prompts live here as versioned `.md` files, never as inline strings. Placeholders use `str.format` style, e.g. `{question}`.

Each file starts with a header block:

```
---
id: answer_with_citations
version: 1
model_tier: agent   # agent | fast
updated: 2026-11-06
---
<prompt body>
```

Rules: bump `version` on any change; keep `id` equal to the file name stem; treat retrieved text as data, never instructions.
