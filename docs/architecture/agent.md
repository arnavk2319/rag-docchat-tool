# Agent core (T-016)

Code: `services/agent/graph.py` (LangGraph `StateGraph`, `run_agent`), `services/agent/nodes.py` (plain async nodes),
`prompts/agent_{router,plan,answer}.md`. Tools: see tools.md.

Flow (T-016 core): `router` (fast) -> direct reply (END, 0 tools) or `plan` (agent) -> `tool` -> `plan` ... -> `answer` (agent) -> END.
Model outputs for router/plan are JSON validated by the llm wrapper (`schema=`); there is no native tool-calling.
The graph is built per run (closures over that run's `Deps`), so there is no module-level loop-bound state.

Bounds
- Steps: `recursion_limit = AGENT_MAX_STEPS` (default 16 since T-017; was 8) node executions. Overrun raises `GraphRecursionError`, caught and
  returned as `AgentResult(stop_reason="max_steps")`; `steps <= 8`.
- Budget (`core.llm.Budget`): tokens (`AGENT_MAX_TOKENS` constant 30000 in graph.py; no env var yet) and USD
  (`AGENT_MAX_USD`, 0.05). Before each call: `max_tokens` is clamped to the remaining tokens; the call is skipped
  when the remaining USD is below the estimated cost (prompt/4 tokens in + max_tokens out at the price table).
- If `Budget.charge` trips after a paid call, `BudgetExceeded.result` carries the `LLMResult`; the agent uses its text
  and stops with `stop_reason="budget"`.
- LLM calls inside `summarize_document` / `compare_documents` are charged to the same budget via `ToolContext.budget`
  (summary gets `max_usd = remaining`; its calls, tokens, USD are added after it returns).

Safety
- Allow-list: names not in `tools.TOOLS` are rejected without running anything and still count as a step.
- The model never sets `owner_id`: it comes only from `ToolContext`; args models are `extra="forbid"`.
- Tool results are escaped and wrapped by `run_tool`; prompts mark them as data. `run_tool` is sync and runs via
  `asyncio.to_thread`.
- Citations = doc ids the answer names that also appear in a tool-result header (`[doc_id=... ]` at line start).
  No evidence (`no results`, errors) -> "I don't know." with no answer LLM call.

Not wired: `query_table`'s `table_loader` (originals are not persisted) so the tool returns "not found" for now.

# Grade, rewrite, groundedness (T-017)

Code: `nodes.grade|rewrite|groundedness`, wired in `build_graph(d, verify)`; `graph.VERIFY` (default True) and
`run_agent(verify=)` switch it off. Prompts: `agent_grade`, `agent_rewrite`, `agent_grounded` (fast tier), `agent_answer` v2.

Flow: `plan` -final-> `grade` -> relevant: `answer` -> `ground` -> END
`grade` irrelevant (or no evidence after tools ran) -> `rewrite` -> `plan` (fresh history/citations), max
`MAX_REWRITES=2`; a third irrelevant verdict abstains "I don't know." (no answer call).
`ground`: draft fails -> ONE regenerate (`MAX_REGENERATE=1`, stricter prompt) -> second fail -> abstain. Max 2 ground checks.

Rules
- Every call goes through `_call`, i.e. the same per-run Budget (tokens/USD) and each node counts as a graph step
  against `AGENT_MAX_STEPS`. A happy path is 7 steps; each rewrite loop adds ~5, hence the code default is now 16
  (cost stays bounded by the USD/token budget). If `.env.example` defines `AGENT_MAX_STEPS`, set it to 16 too.
- Grader/groundedness/rewrite output is JSON validated by the llm wrapper (`schema=`, one repair). Still invalid
  (`LLMValidationError`) or schema-valid-but-negative -> fail safe: not relevant / not grounded / abstain. A broken
  grader abstains directly instead of burning rewrites; nothing loops on bad output.
- The draft answer, question and tool text are escaped and labelled DATA in the prompts. The rewritten query is
  model output: escaped and passed to `plan` as a suggestion only.
- A malformed groundedness reply discards a possibly good draft (after the regenerate): deliberate fail-safe.
- Abstain answers have no citations (citations come only from `surfaced`, which a rewrite resets).
- A budget trip mid-verification stops the run (`stop_reason="budget"`); the unverified draft is not returned.
