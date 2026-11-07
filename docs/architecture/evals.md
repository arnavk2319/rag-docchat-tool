# Evals (T-023)

`uv run python -m evals.run --mock [--check]` runs the golden set offline in about 3 s, writes `evals/results.json` and prints a markdown table. `--check` exits 1 if any thresholded metric misses `evals/thresholds.json` (override with `--thresholds PATH`).

## Every number here is `mock`
The model is a deterministic stand-in policy scripted into the real agent graph through the llm wrapper's mock provider, and embeddings and reranker are the fakes (`MODELS_FAKE`). The stand-in is an extractive answerer with lexical grader, groundedness and judge verdicts. So the run proves the pipeline is wired and measured end to end (ingestion, hybrid retrieval, rerank, tools, graph, budgets, citations, injection defences, metric code, threshold gate). It does NOT measure model quality. `results.json` carries `label: "mock"` and a note saying so. A real quality number needs a `--live` run with real models (human-approved spend).

## Dataset
- `evals/golden.jsonl`: exactly 20 items: 12 factual (expected doc and page), 3 out-of-corpus (must abstain), 3 table, 2 comparison. Fields: `question`, `gold[{doc,page,evidence}]`, `abstain`, `answer_contains`, `expected_tool`, `tool_args` (table only).
- `evals/fixtures/` (regenerate with `python -m evals.make_fixtures`; do not, golden pins these snapshots): `travel_checklist.txt` and `hotel_reservation.pdf` (2 pages) are fully synthetic (fictional traveller, Example Grand Hotel, made-up address and 2031 dates, so no personal data), `docchat_spec.pdf` (docs/spec.md, one page per section, personal-data bullet removed), `travel_tasks.csv` (the checklist's to-dos as rows: task_no, action, item). The generator reads only docs/spec.md.
- `tests/test_evals.py` asserts every evidence string is verbatim in its fixture on the stated page (no invented facts).
- Questions use words that appear in the documents because the fake reranker is lexical; mock retrieval numbers are therefore optimistic for real embeddings.

## Metrics
| Group | Metric | Definition (mock) |
| --- | --- | --- |
| Retrieval | recall@5, MRR, hit@1 | 14 items with evidence; same pipeline as `search_documents` (hybrid top 20, parents, rerank top 5); a parent is relevant if doc, page and the evidence text match. Recall is the share of gold docs found. |
| Answer | faithfulness | judge score: share of answer content words supported by tool evidence; abstentions excluded |
| Answer | relevance | judge score: question keywords covered by the answer (table: plus the query_table op); correct abstention on out-of-corpus = 1 |
| Answer | citation_correct | deterministic: cited doc ids equal the gold docs (no extras); factual items also need the cited span to be the gold page |
| Agent | task_success | deterministic: no bounded stop, `answer_contains` present and gold docs cited; out-of-corpus: "I don't know." with no citations |
| Agent | avg steps, tool-call accuracy, loop rate | graph steps per run; share of tool calls with the expected tool and status ok; share of runs that hit a bound or repeated an identical call |
| Perf | p50/p95 latency, tokens, USD per request | wall time of `run_agent`; mock token counts and price table (not real) |
| Safety | injection block rate | reuses `tests/test_injection.py` (10 payload files plus a poisoned session) |

Judge calls go through `llm.complete(model_role="fast")` with the fixed rubric in `prompts/eval_judge.md`. In mock mode the harness queues a lexical verdict; in live mode the real fast model scores, and verdicts are cached in `evals/.cache/judge.json` (git-ignored) so reruns are free.

## Live mode
`--live` exists for a future paid run and tests never use it. It refuses to start without `ANTHROPIC_API_KEY` (exit 2), prints the cost cap ($4.00) first and aborts when the running total reaches it. It is unverified.

## Known gaps
- The app does not wire `ToolContext.table_loader` (original files are not persisted), so `query_table` returns "not found" in production. The harness injects a loader reading `evals/fixtures` for the 3 table items. Production needs the original bytes stored at upload.
- Safety coverage is the injection suite only; jailbreak, PII and off-topic prompts are covered by the out-of-corpus items and by the PII-free logging tests, not by dedicated eval items.
- `AGENT_MAX_STEPS` defaults to 16 in code (docs said 8); the threshold is on the average, measured as is (7.85 after `MAX_REWRITES` went 2 -> 1).
- Remaining mock misses (recall@5, citation_correct) come from the lexical fake reranker, see the T-023 Findings trace.
