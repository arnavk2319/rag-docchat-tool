"""T-023: golden set, eval metrics and the threshold gate. Everything is mock/offline."""

import asyncio
import json
import re
from pathlib import Path

import pytest
from app.core import llm
from pypdf import PdfReader

from evals import metrics as M
from evals import run as R

ROOT = Path(__file__).parents[1]
GOLD = R.load_golden()


def _pages(doc: str) -> list[str]:
    p = R.FIX / doc
    if p.suffix == ".pdf":
        return [pg.extract_text() for pg in PdfReader(p).pages]
    return [p.read_text(encoding="utf-8-sig")]


@pytest.fixture(scope="module")
def result():
    return asyncio.run(R.compute("mock"))


def test_golden_composition():
    assert len((ROOT / "evals/golden.jsonl").read_text().splitlines()) == 20
    kinds = [g["type"] for g in GOLD]
    assert kinds.count("factual") >= 12 and kinds.count("out_of_corpus") == 3
    assert kinds.count("table") == 3 and kinds.count("comparison") == 2
    assert len({g["id"] for g in GOLD}) == 20
    assert all(g["abstain"] for g in GOLD if g["type"] == "out_of_corpus")


def test_golden_evidence_is_verbatim_in_fixtures():
    """No invented facts: every evidence string is in the fixture, on the stated page."""
    for g in GOLD:
        for e in g["gold"]:
            assert (R.FIX / e["doc"]).exists()
            if not e["evidence"]:
                continue
            pages = _pages(e["doc"])
            page_text = pages[(e["page"] or 1) - 1]
            assert R.norm(e["evidence"]) in R.norm(page_text), (g["id"], e)
        if g["type"] == "factual":
            assert g["gold"][0]["evidence"] and g["answer_contains"]


def test_fixtures_are_synthetic():
    text = " ".join(_pages("hotel_reservation.pdf") + _pages("travel_checklist.txt"))
    assert "Example Grand Hotel" in text and "100 Example Street" in text and "2031" in text
    spec = " ".join(_pages("docchat_spec.pdf")).lower()
    assert "citizenship" not in spec and "personal documents" not in spec


def test_thresholds_cover_every_ticket_metric():
    thr = json.loads((ROOT / "evals/thresholds.json").read_text())
    rows = M.check({k: 0.0 for k, *_ in M.RULES}, thr)
    assert {r["metric"] for r in rows} >= {
        "recall_at_5", "mrr", "faithfulness", "relevance", "citation_correct",
        "task_success", "avg_steps", "injection_block_rate"}


def test_metric_helpers():
    assert M.reciprocal_rank([False, False, True]) == pytest.approx(1 / 3)
    assert M.reciprocal_rank([False]) == 0.0
    assert M.recall_at_k([True, False, True], 2) == 1.0 and M.recall_at_k([False] * 5, 1) == 0.0
    assert M.percentile([1, 2, 3, 4, 5], 50) == 3 and M.percentile([1, 2, 3, 4, 5], 95) == 5
    assert M.percentile([], 95) == 0.0


def test_support_detects_unsupported_claims():
    assert R.support("Check-in is 03:00 PM [doc_id=x]", "Check-In: 03:00 PM") == 1.0
    assert R.support("Refunds are issued within thirty days", "Check-In: 03:00 PM") < 0.5
    assert R.support(R.NOANS, "anything") == 1.0


def test_mock_run_is_labelled_and_offline(result):
    assert result["label"] == "mock" and "NOT model quality" in result["note"]
    assert result["counts"] == {"golden": 20, "retrieval_items": 14, "injection_cases": 11}
    m = result["metrics"]
    for k in ("recall_at_5", "mrr", "faithfulness", "relevance", "citation_correct",
              "task_success", "avg_steps", "injection_block_rate", "p95_latency_s"):
        assert k in m
    assert m["injection_block_rate"] >= 0.9 and m["abstain_accuracy"] == 1.0
    assert m["tool_call_accuracy"] == 1.0 and m["loop_rate"] == 0.0


def test_table_answers_carry_a_citation(result):
    tables = [i for i in result["items"] if i["type"] == "table"]
    assert len(tables) == 3 and all(i["success"] and i["citation_ok"] for i in tables)


async def test_judge_uses_fast_role(monkeypatch):
    roles = []
    real = llm.complete

    async def spy(messages, **kw):
        roles.append(kw["model_role"])
        return await real(messages, **kw)

    monkeypatch.setattr(llm, "complete", spy)
    s = await R.judge("relevance", "q?", "ev", "a", 0.5, True, {}, [])
    assert roles == ["fast"] and s == 0.5


def test_check_flags_a_lowered_metric(result):
    thr = json.loads((ROOT / "evals/thresholds.json").read_text())
    good = dict(result["metrics"])
    relaxed = {s: {k: (0.1 if not k.startswith(("max", "p95")) else 1e9) for k in v} for s, v in thr.items()}
    assert all(r["pass"] for r in M.check(good, relaxed))
    good["mrr"] = 0.0
    assert [r["metric"] for r in M.check(good, relaxed) if not r["pass"]] == ["mrr"]


def test_cli_check_exit_codes(result, tmp_path, monkeypatch):
    async def fake(mode):
        return json.loads(json.dumps(fake.res))

    monkeypatch.setattr(R, "compute", fake)
    thr = json.loads((ROOT / "evals/thresholds.json").read_text())
    # Every metric comfortably passes...
    ok = {"retrieval": {"recall_at_5": 0.1, "mrr": 0.1}, "answer": {k: 0.1 for k in
          ("faithfulness", "relevance", "citation_correct")},
          "agent": {"task_success": 0.1, "max_avg_steps": 100}, "safety": thr["safety"],
          "performance": {"p95_latency_s": 100, "max_usd_per_request": 1}}
    tpath = tmp_path / "t.json"
    tpath.write_text(json.dumps(ok))
    args = ["--mock", "--check", "--thresholds", str(tpath), "--out", str(tmp_path / "r.json")]
    fake.res = result
    assert R.main(args) == 0
    # ...then lowering ONE metric below its threshold makes --check exit non-zero.
    low = json.loads(json.dumps(result))
    low["metrics"]["injection_block_rate"] = 0.5
    fake.res = low
    assert R.main(args) == 1
    assert json.loads((tmp_path / "r.json").read_text())["passed"] is False
    assert R.main(["--mock", "--thresholds", str(tpath), "--out", str(tmp_path / "r.json")]) == 0


def test_live_refuses_without_explicit_key(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    async def boom(mode):
        raise AssertionError("must not run")

    monkeypatch.setattr(R, "compute", boom)
    assert R.main(["--live"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_mode_flag_required():
    with pytest.raises(SystemExit):
        R.main([])


def test_results_json_is_committed_and_valid():
    res = json.loads((ROOT / "evals/results.json").read_text())
    assert res["label"] == "mock" and "checks" in res and re.search("mock", res["note"], re.IGNORECASE)


def test_bm25_scores_are_comparable_across_documents(tmp_path):
    """Regression (found by the T-023 eval): IDF was per document, so a rare term in a 2-chunk
    document scored ~0 and lost to common words in a big document. IDF is now owner-wide."""
    from app.store.bm25 import Bm25Store

    bm = Bm25Store(tmp_path)
    bm.add_doc("u", "big", [f"b{i}" for i in range(12)],
               [f"time date time date time date plan number {i}" for i in range(12)])
    bm.add_doc("u", "small", ["s0", "s1"],
               ["Check-In: Saturday 03:00 PM", "Total for Stay Room Type Suite"])
    top = bm.query("u", "check-in time date", k=5)
    assert top[0][0] == "s0", top
    assert bm.query("u", "check-in", k=5, doc_ids=["big"]) == []  # doc scope still honoured
    bm.delete_doc("u", "small")
    assert all(c.startswith("b") for c, _ in bm.query("u", "time check-in", k=20))
