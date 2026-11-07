"""Eval runner. `uv run python -m evals.run --mock [--check]` (free, offline, deterministic).

MOCK MODE PROVES WIRING, NOT MODEL QUALITY. The "model" is a deterministic stand-in policy
(extractive answerer, lexical grader/judge) that is scripted into the real agent graph through the
llm wrapper's mock provider. Retrieval, tools, graph, budgets, citations and the injection defences
are the real production code; embedder and reranker are the fake ones (MODELS_FAKE). Every number
is therefore labelled `mock`. `--live` is a guarded stub for a future paid run and is never used by
tests.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from . import metrics as M

ROOT = Path(__file__).resolve().parents[1]
EVALS = Path(__file__).resolve().parent
FIX = EVALS / "fixtures"
OWNER = "eval-user"
LIVE_CAP_USD = 4.0
MOCK_MIN_RERANK = "0.3"  # FakeReranker is a lexical fraction in 0..1; real bge logits differ
MIME = {".txt": "text/plain", ".pdf": "application/pdf", ".csv": "text/csv"}
STOP = {
    "what", "which", "when", "where", "who", "how", "does", "the", "and", "for", "are", "with", "that", "this", "from", "use", "uses", "per", "has", "have", "list", "many", "much", "into", "there", "their", "was", "were", "its", "not", "you", "your", "can", "about", "versus", "total"
}
WORD = re.compile(r"[a-z0-9]{3,}")
NOANS = "I don't know."


def _max_rewrites() -> int:
    from app.services.agent.nodes import MAX_REWRITES

    return MAX_REWRITES


def kw(text: str) -> set[str]:
    ws = (w[:-1] if len(w) > 3 and w.endswith("s") else w for w in WORD.findall(text.lower()))
    return {w for w in ws if w not in STOP}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def load_golden(path: Path = EVALS / "golden.jsonl") -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


class JudgeOut(BaseModel):
    score: float
    reason: str = ""


@contextlib.contextmanager
def eval_env(tmp: Path, mock: bool) -> Iterator[None]:
    """Offline, fake-model environment; restores the process env afterwards."""
    from app.core import config, llm, models

    keys = ["LLM_PROVIDER", "MODELS_FAKE", "MIN_RERANK_SCORE", "CHROMA_DIR", "DB_PATH",
            "ANTHROPIC_API_KEY"]
    saved = {k: os.environ.get(k) for k in keys}
    os.environ.update(MODELS_FAKE="1", MIN_RERANK_SCORE=MOCK_MIN_RERANK,
                      CHROMA_DIR=str(tmp / "chroma"), DB_PATH=str(tmp / "db.sqlite"))
    if mock:
        os.environ["LLM_PROVIDER"] = "mock"
        os.environ.pop("ANTHROPIC_API_KEY", None)
    config.get_settings.cache_clear()
    llm.mock_script()
    models.reset_models()
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        config.get_settings.cache_clear()
        llm.mock_script()
        models.reset_models()


async def build_corpus(tmp: Path) -> tuple[Any, dict[str, str]]:
    """Ingest every fixture through the real IngestionService. Returns (ToolContext, name->doc_id)."""
    from app.core.models import FakeEmbedder, FakeReranker
    from app.services.agent.tools import ToolContext
    from app.services.ingestion import IngestionService
    from app.store.bm25 import Bm25Store
    from app.store.chroma import ChromaStore
    from app.store.parents import SqliteParents
    from app.store.registry import SqliteRegistry

    reg = SqliteRegistry(tmp / "db.sqlite")
    parents = SqliteParents(tmp / "db.sqlite")
    store = ChromaStore(tmp / "chroma")
    bm25 = Bm25Store(tmp / "bm25")
    emb = FakeEmbedder()
    svc = IngestionService(reg, store, emb, parents=parents, bm25=bm25)
    ids: dict[str, str] = {}
    for p in sorted(FIX.iterdir()):
        doc = await svc.ingest(OWNER, str(p), p.name, MIME[p.suffix])
        assert doc.status == "ready", f"{p.name}: {doc.status}"
        ids[p.name] = doc.doc_id
    by_id = {v: FIX / k for k, v in ids.items()}

    def loader(owner: str, doc_id: str) -> bytes | None:
        # Eval-only: the production app does not wire table_loader (originals are not persisted).
        return by_id[doc_id].read_bytes() if owner == OWNER and doc_id in by_id else None

    ctx = ToolContext(OWNER, None, reg, store, parents, bm25, lambda q: emb.encode([q])[0],
                      FakeReranker(), table_loader=loader)
    return ctx, ids


# ---- stand-in "model" policy (mock mode only) ---------------------------------------------------

def J(**kw_: Any) -> str:
    return json.dumps(kw_)


def blocks(result: str) -> list[tuple[str, str]]:
    """Parse a search_documents tool result into [(doc_id, snippet)]."""
    body = result.split("\n", 1)[1].rsplit("\n</tool_result>", 1)[0] if "\n" in result else ""
    out = []
    for b in body.split("\n---\n"):
        m = re.match(r"\[doc_id=(\S+) [^\n]*\]\n(.*)", b, re.DOTALL)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def sentences(text: str) -> list[str]:
    out, buf = [], ""
    for ln in (x.strip() for x in text.splitlines()):
        if not ln:
            continue
        buf = f"{buf} {ln}".strip()
        if len(ln) < 80:  # PDF lines wrap at ~95 chars: a long line continues on the next one
            out.append(buf)
            buf = ""
    out += [buf] if buf else []
    parts = [p for line in out for p in re.split(r"(?<=[.;])\s+(?=[A-Z#-])|\s+(?=\* )", line)]
    return [p for p in parts if not p.startswith("#")] or parts  # headings are not answers


def best_sentence(question: str, bl: list[tuple[str, str]]) -> tuple[str, str]:
    """Extractive pick: the line whose question words are rarest across the retrieved lines."""
    q = kw(question)
    pool = [(doc, s) for doc, text in bl for s in sentences(text)]
    df = {w: sum(w in kw(s) for _, s in pool) or 1 for w in q}
    best, score = ("", ""), -1.0
    for doc, s in pool:
        sc = sum(1 / df[w] for w in q & kw(s))
        if sc > score:
            best, score = (doc, s[:240]), sc
    return best


def has_evidence(result: str) -> bool:
    return 'status="ok"' in result.split("\n", 1)[0] and "no results" not in result


def lex_relevant(question: str, bl: list[tuple[str, str]]) -> bool:
    q = kw(question)
    return bool(q) and len(q & kw(" ".join(t for _, t in bl))) / len(q) >= 0.5


def support(answer: str, evidence: str) -> float:
    """Share of the answer's content words that appear in the evidence (1.0 for an abstention)."""
    a = kw(re.sub(r"\[doc_id=[^\]]*\]", " ", answer))
    return 1.0 if not a or answer.strip() == NOANS else len(a & kw(evidence)) / len(a)


def script_for(item: dict[str, Any], ctx: Any, ids: dict[str, str]) -> list[str]:
    """Build the mock reply queue for one item, pre-running the same tools the graph will run
    (read-only, no LLM) so the stand-in grader/answerer reacts to the real tool output."""
    from app.services.agent.tools import run_tool

    q = item["question"]
    route = J(route="retrieve", answer="")
    final = J(action="final", tool="", args={})
    yes, no, good = J(relevant=True), J(relevant=False), J(grounded=True)

    from app.core.config import get_settings

    min_score = get_settings().min_rerank_score

    def call(tool: str, args: dict[str, Any], floor: float | None = None) -> str:
        m = min_score if floor is None else floor
        return run_tool(tool, args, replace(ctx, surfaced={}, min_rerank_score=m))

    def plan(tool: str, args: dict[str, Any]) -> str:
        return J(action="tool", tool=tool, args=args)

    typ = item["type"]
    if typ == "table":
        args = {"doc_id": ids[item["gold"][0]["doc"]], **item["tool_args"]}
        res = call("query_table", args)
        ok = has_evidence(res) and "[no rows]" not in res
        body = res.split("\n", 1)[1].rsplit("\n</tool_result>", 1)[0].replace("\n", "; ")
        ans = f"{body} [doc_id={args['doc_id']}]"
        return [route, plan("query_table", args), final] + ([yes, ans, good] if ok else [no])
    if typ == "comparison":
        docs = [ids[g["doc"]] for g in item["gold"]]
        lines = [best_sentence(q, blocks(call("search_documents", {"query": q, "doc_ids": [d]}, floor=0.0)))
                 for d in docs]
        ans = " ".join(f"{s} [doc_id={d}]" for d, s in lines)
        cmp_text = " ".join(f"{d}: {s}" for d, s in lines)
        return [route, plan("compare_documents", {"doc_ids": docs, "focus": q}), cmp_text,
                final, yes, ans, good]
    script, cur, rewrites = [route], q, 0
    while True:
        res = call("search_documents", {"query": cur})
        bl = blocks(res)
        script += [plan("search_documents", {"query": cur}), final]
        rel = has_evidence(res) and lex_relevant(q, bl)
        if has_evidence(res):  # grade makes no LLM call when there is no evidence
            script.append(yes if rel else no)
        if rel:
            doc, sent = best_sentence(q, bl)
            return script + [f"{sent} [doc_id={doc}]", good]
        if rewrites >= _max_rewrites():  # grade abstains at the rewrite cap
            return script
        nq = " ".join(sorted(kw(q)))
        script.append(J(query="" if nq == cur or rewrites else nq))  # 2nd time: gives up
        if nq == cur or rewrites:
            return script
        cur, rewrites = nq, rewrites + 1


# ---- measurement ---------------------------------------------------------------------------------

def retrieval_row(item: dict[str, Any], ctx: Any, ids: dict[str, str]) -> dict[str, Any] | None:
    from app.services.parent_lookup import expand_to_parents
    from app.services.rerank import rerank_parents
    from app.services.retrieval import TOP_N, hybrid_search

    gold = [g for g in item["gold"] if g["evidence"]]
    if not gold:
        return None
    q = item["question"]
    allowed = [d.doc_id for d in ctx.registry.list_by_owner(OWNER) if d.status == "ready"]
    hits = hybrid_search(OWNER, q, ctx.embed(q), ctx.vectors, ctx.bm25, allowed, TOP_N)
    ranked = rerank_parents(ctx.reranker, q, expand_to_parents(OWNER, hits, ctx.parents), 5)

    def matches(parent: Any, g: dict[str, Any]) -> bool:
        return (parent.doc_id == ids[g["doc"]] and (g["page"] is None or parent.page == g["page"])
                and norm(g["evidence"]) in norm(parent.text))

    flags = [any(matches(ph.parent, g) for g in gold) for ph, _ in ranked]
    found = sum(any(matches(ph.parent, g) for ph, _ in ranked) for g in gold)
    return {"recall": found / len(gold), "rr": M.reciprocal_rank(flags), "hit1": bool(flags and flags[0])}


async def judge(metric: str, q: str, evidence: str, answer: str, mock_score: float,
                scripted: bool, cache: dict[str, float], spend: list[float]) -> float:
    """One judge verdict through the llm wrapper `fast` role (mock: scripted lexical verdict)."""
    from app.core import llm
    from app.core.prompts import load_prompt
    from app.core.security import escape

    prompt = load_prompt("eval_judge").render(
        metric=metric, question=escape(q), evidence=escape(evidence[:3000]), answer=escape(answer))
    key = hashlib.sha256(prompt.encode()).hexdigest()
    if not scripted and key in cache:  # live reruns are free
        return cache[key]
    if scripted:
        llm.mock_script(J(score=mock_score, reason="mock lexical rubric"))
    res = await llm.complete([{"role": "user", "content": prompt}], model_role="fast",
                             schema=JudgeOut, max_tokens=150)
    spend.append(res.usd)
    score = res.parsed.score if isinstance(res.parsed, JudgeOut) else 0.0
    score = min(1.0, max(0.0, score))
    cache[key] = score
    return score


async def run_items(ctx: Any, ids: dict[str, str], golden: list[dict[str, Any]], scripted: bool,
                    cache: dict[str, float], spend: list[float]) -> list[dict[str, Any]]:
    from app.core import llm
    from app.services.agent.graph import run_agent

    names = {v: k for k, v in ids.items()}
    rows = []
    for item in golden:
        q = item["question"]
        events: list[tuple[str, Any]] = []
        llm.mock_script(*(script_for(item, ctx, ids) if scripted else []))
        t0 = time.perf_counter()
        r = await run_agent(q, ctx, on_event=lambda n, p, ev=events: ev.append((n, p)),
                            run_id=item["id"])
        latency = time.perf_counter() - t0
        llm.mock_script()
        calls = [(p["tool"], json.dumps(p["args"], sort_keys=True, default=str))
                 for n, p in events if n == "tool_call"]
        results = [p["result"] for n, p in events if n == "tool_result"]
        evidence = "\n".join(results)
        ans, gold = r.answer.strip(), item["gold"]
        abstained = ans == NOANS
        cited = {names[c["doc_id"]] for c in r.citations}
        gold_docs = {g["doc"] for g in gold}
        done = not r.bounded_stop
        if item["abstain"]:
            success = abstained and not r.citations and done
            cite_ok = None
        else:
            success = (done and not abstained and cited >= gold_docs and
                       all(norm(s) in norm(ans) for s in item["answer_contains"]))
            cite_ok = cited == gold_docs
            if cite_ok and item["type"] == "factual" and gold[0]["page"] is not None:
                cite_ok = all(c["span"] == f"page {gold[0]['page']}" for c in r.citations)
        sup = support(ans, evidence)
        qk = kw(q)
        shown = ans + " " + " ".join(a for t, a in calls if t == "query_table")  # table op = the answer's basis
        overlap = len(qk & kw(shown)) / len(qk) if qk else 0.0
        rel_mock = (1.0 if abstained else 0.0) if item["abstain"] else (0.0 if abstained else min(1.0, overlap / 0.3))
        row = {
            "id": item["id"], "type": item["type"], "success": success, "citation_ok": cite_ok,
            "abstained": abstained, "steps": r.steps, "tool_calls": r.tool_calls,
            "stop_reason": r.stop_reason, "latency_s": latency, "tokens": r.tokens, "usd": r.usd,
            "tool_ok": [t == item["expected_tool"] and 'status="ok"' in res.split("\n", 1)[0]
                        for (t, _), res in zip(calls, results, strict=False)],
            "loop": bool(r.stop_reason) or len(set(calls)) < len(calls),
            "faithfulness": None if abstained else await judge(
                "faithfulness", q, evidence, ans, sup, scripted, cache, spend),
            "relevance": await judge("relevance", q, evidence, ans, rel_mock, scripted, cache, spend),
        }
        rows.append(row)
        if not scripted and sum(spend) + sum(x["usd"] for x in rows) > LIVE_CAP_USD:
            raise SystemExit(f"live cost cap ${LIVE_CAP_USD} reached; aborting")
    return rows


async def injection_rate(tmp: Path) -> tuple[float, int]:
    """Reuse the T-021 payload suite (tests/test_injection.py): 10 payloads + poisoned session."""
    import pytest

    spec = importlib.util.spec_from_file_location("eval_injection_suite", ROOT / "tests/test_injection.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cases = [(p, None) for p in mod.PAYLOADS]
    sess = mod.FIX / "session_injection.txt"
    cases.append((sess, mod.poisoned_history(sess)))
    blocked = 0
    with pytest.MonkeyPatch.context() as mp:
        for i, (path, hist) in enumerate(cases):
            sub = tmp / f"inj{i}"
            sub.mkdir()
            blocked += all(mod.judge(*await mod.run_payload(path, sub, mp, hist)).values())
    return blocked / len(cases), len(cases)


async def compute(mode: str) -> dict[str, Any]:
    scripted = mode == "mock"
    golden = load_golden()
    cache_path = EVALS / ".cache" / "judge.json"
    cache = json.loads(cache_path.read_text()) if not scripted and cache_path.exists() else {}
    spend: list[float] = []
    with tempfile.TemporaryDirectory() as t, eval_env(Path(t), scripted):
        tmp = Path(t)
        ctx, ids = await build_corpus(tmp)
        ret = [x for x in (retrieval_row(i, ctx, ids) for i in golden) if x]
        rows = await run_items(ctx, ids, golden, scripted, cache, spend)
        inj, n_inj = await injection_rate(tmp)
    if not scripted:
        cache_path.parent.mkdir(exist_ok=True)
        cache_path.write_text(json.dumps(cache))
    cit = [r["citation_ok"] for r in rows if r["citation_ok"] is not None]
    faith = [r["faithfulness"] for r in rows if r["faithfulness"] is not None]
    toolok = [x for r in rows for x in r["tool_ok"]]
    ooc = [r for r in rows if r["type"] == "out_of_corpus"]
    metrics = {
        "recall_at_5": M.mean([x["recall"] for x in ret]),
        "mrr": M.mean([x["rr"] for x in ret]),
        "hit_rate_at_1": M.mean([float(x["hit1"]) for x in ret]),
        "faithfulness": M.mean(faith),
        "relevance": M.mean([r["relevance"] for r in rows]),
        "citation_correct": M.mean([float(c) for c in cit]),
        "abstain_accuracy": M.mean([float(r["success"]) for r in ooc]),
        "task_success": M.mean([float(r["success"]) for r in rows]),
        "tool_call_accuracy": M.mean([float(x) for x in toolok]),
        "avg_steps": M.mean([r["steps"] for r in rows]),
        "loop_rate": M.mean([float(r["loop"]) for r in rows]),
        "p50_latency_s": M.percentile([r["latency_s"] for r in rows], 50),
        "p95_latency_s": M.percentile([r["latency_s"] for r in rows], 95),
        "avg_tokens_per_request": M.mean([r["tokens"] for r in rows]),
        "avg_usd_per_request": M.mean([r["usd"] for r in rows]),
        "max_usd_per_request": max(r["usd"] for r in rows),
        "injection_block_rate": inj,
    }
    return {
        "label": mode,
        "note": ("MOCK: scripted stand-in model + fake embedder/reranker. Proves the pipeline is "
                 "wired and measured, NOT model quality." if scripted else "LIVE run"),
        "counts": {"golden": len(golden), "retrieval_items": len(ret), "injection_cases": n_inj},
        "judge_usd": round(sum(spend), 6),
        "metrics": {k: round(v, 4) for k, v in metrics.items()},
        "items": rows,
    }


def finish(res: dict[str, Any], thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    rows = M.check(res["metrics"], thresholds)
    res["checks"] = rows
    res["passed"] = all(r["pass"] for r in rows)
    return rows


def stable(res: dict[str, Any]) -> dict[str, Any]:
    """Committed copy without wall-clock timings, so reruns do not dirty the tree."""
    out = json.loads(json.dumps(res, default=str))
    for k in ("p50_latency_s", "p95_latency_s"):
        out["metrics"].pop(k, None)
    out["checks"] = [c for c in out["checks"] if c["metric"] != "p95_latency_s"]
    for r in out["items"]:
        r.pop("latency_s", None)
    out["passed"] = all(c["pass"] for c in out["checks"])
    out["note"] += " Latency is printed by the run but not stored (non-deterministic)."
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.run")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--mock", action="store_true", help="offline deterministic run (free)")
    g.add_argument("--live", action="store_true", help="paid run; needs ANTHROPIC_API_KEY")
    ap.add_argument("--check", action="store_true", help="exit 1 if any metric misses its threshold")
    ap.add_argument("--thresholds", type=Path, default=EVALS / "thresholds.json")
    ap.add_argument("--out", type=Path, default=EVALS / "results.json")
    a = ap.parse_args(argv)
    if a.live:
        print(f"live run: estimated cost cap ${LIVE_CAP_USD:.2f} (aborts when reached)")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("refusing --live: set ANTHROPIC_API_KEY explicitly (no key, no spend)", file=sys.stderr)
            return 2
        os.environ["LLM_PROVIDER"] = "anthropic"
    res = asyncio.run(compute("mock" if a.mock else "live"))
    rows = finish(res, json.loads(a.thresholds.read_text()))
    a.out.write_text(json.dumps(stable(res), indent=2, default=str) + "\n")
    print(M.markdown_table(rows, res["label"]))
    print(res["note"])
    if a.check and not res["passed"]:
        print("FAILED: " + ", ".join(r["metric"] for r in rows if not r["pass"]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
