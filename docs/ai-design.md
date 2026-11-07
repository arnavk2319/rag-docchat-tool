# AI design

| Decision | Choice | Why (one line) | Cost impact |
| -------- | ------ | -------------- | ----------- |
| Generation model | Hosted `claude-sonnet-5-5` (agent/answer), `claude-haiku-5-5` (map/grade/compress/groundedness); Ollama optional; ids in config only | Best tool calling; cheap tier for sub-steps | <= $0.05/agent request, <= $0.02/20-page summary; tests $0 (mock) |
| Embedding model | `all-MiniLM-L6-v2` via sentence-transformers, local, loaded once | Free, fast on CPU, good enough for recall@5 >= 0.8 | $0 API; ~0.1 GB RAM |
| Vector store | Embedded Chroma PersistentClient + `rank-bm25` index | No server, metadata filter by doc_id/owner | $0; single-process only |
| Chunking | Parent-child: child ~300 tok embedded, parent ~1200 tok as context; page/sheet/section + char offsets | Precise match, rich context, exact citation spans | $0; slightly larger index |
| Retrieval (top-k / hybrid / rerank) | BM25 + dense top 20 each, RRF fuse, `bge-reranker-base` to top 5; abstain if top score < `MIN_RERANK_SCORE` | Hybrid catches keywords and semantics; rerank lifts precision; abstention prevents hallucination | $0 API; ~1.1 GB RAM, +200-500 ms CPU |
| Agent loop / tools | LangGraph: router, plan, tool, grade, rewrite (max 2), answer, groundedness; 6 read-only allow-listed tools; SSE | Spec P3; structured, streamable, bounded | Max 8 steps and $0.05 per run; Haiku for grade/groundedness |
| Guardrails | Retrieved text and tool output as delimited data; doc_id scoping; per-run step/token/USD caps; rate limit + daily USD cap; PII-free logs; groundedness check | Mitigates indirect injection and runaway spend | Small extra Haiku call per answer (~$0.001) |
| Wrapper limits | One `llm.complete()`: 30 s timeout, 3 retries (429/5xx/timeout, backoff + jitter), per-run `Budget` (steps/tokens/USD), 1 JSON repair retry; details in `docs/architecture/llm.md` | Single choke point for cost, safety and mock tests | Caps runaway spend; no retry on 4xx |
| Price table | `config.prices` USD/1M tokens in/out: sonnet-5-5 3.00/15.00, haiku-5-5 1.00/5.00; unknown ids cost 0 | Per-call USD logging and budget enforcement; update when prices change | Logging only; no extra cost |

## Prompts
## Failure modes and fallbacks
