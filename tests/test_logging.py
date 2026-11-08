import logging

import pytest
from app.core.logging import setup_logging


def test_single_handler_idempotent():
    root = logging.getLogger()
    before = len(root.handlers)
    setup_logging()
    setup_logging()
    assert len(root.handlers) <= before + 1


# ---- T-022: PII-safe logging ----
import ast
import json
import re
from pathlib import Path

from app.core import llm
from app.main import app
from authutil import bearer
from fastapi.testclient import TestClient

SENT_DOC = "ZEBRAFINCH-DOC-7731"
SENT_Q = "QUOKKA-QUESTION-4412"
SENT_FILE = "NARWHAL-FILENAME-9023"
SENT_ERR = "OCELOT-ERRTEXT-5518"
SENTINELS = (SENT_DOC, SENT_Q, SENT_FILE, SENT_ERR)
H = bearer("alice")


def _logs(caplog, capsys) -> str:
    out = capsys.readouterr()
    recs = " ".join(
        r.getMessage() + " " + json.dumps(r.__dict__, default=str) + " " + (r.exc_text or "")
        for r in caplog.records
    )
    return recs + out.err + out.out


def test_no_user_content_in_logs(caplog, capsys):
    caplog.set_level(logging.DEBUG)
    text = f"{SENT_DOC} the sky is blue. Grass is green.".encode()
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/v1/documents", files=[("files", (f"{SENT_FILE}.txt", text))], headers=H)
        did = r.json()["results"][0]["doc_id"]
        # also an unparsable upload with a sentinel name
        c.post("/v1/documents", files=[("files", (f"{SENT_FILE}.pdf", SENT_DOC.encode()))], headers=H)
        assert c.post("/v1/query", json={"question": SENT_Q}, headers=H).status_code == 200
        assert c.post("/v1/query", json={"question": SENT_Q, "stream": True}, headers=H).status_code == 200
        assert c.post("/v1/summarize", json={"doc_id": did}, headers=H).status_code == 200
        llm.mock_script(llm.LLMTransportError(500, SENT_ERR), llm.LLMTransportError(500, SENT_ERR))
        c.post("/v1/query", json={"question": SENT_Q}, headers=H)
        llm.mock_script(llm.LLMTransportError(400, SENT_ERR))
        c.post("/v1/query", json={"question": SENT_Q, "stream": True}, headers=H)
        c.post("/v1/query", json={"question": SENT_Q, "session_id": "nope"}, headers=H)
    blob = _logs(caplog, capsys)
    assert "llm call" in blob or len(caplog.records) > 0  # the capture actually saw logs
    for s in SENTINELS:
        assert s not in blob, s


_BAD = re.compile(
    r"\b(question|answer|prompt|messages?|text|content|filename|file_name|query|args|summary|"
    r"excerpt|body)\b"
)
_METHODS = {"debug", "info", "warning", "error", "critical", "exception", "log"}
_RECEIVERS = {"log", "logger", "_log", "_logger", "logging"}


def _arg_offenders(a: ast.AST, where: str) -> list[str]:
    out: list[str] = []
    if isinstance(a, ast.Constant):
        return out
    src = ast.unparse(a)
    if isinstance(a, ast.JoinedStr):
        out.append(f"{where} f-string log call")
    if isinstance(a, ast.Call) and ast.unparse(a.func) == "type":
        return out  # type(e).__name__ is fine
    if src in {"e", "exc", "err", "error"} or re.match(r"(str|repr)\(", src):
        out.append(f"{where} raw exception text: {src}")
    if _BAD.search(src) and "__name__" not in src:
        out.append(f"{where} sensitive arg: {src}")
    return out


def scan_source(source: str, name: str = "x.py") -> list[str]:
    """Flag log/print calls that may carry user content (args and `extra=` keys/values)."""
    offenders: list[str] = []
    for n in ast.walk(ast.parse(source)):
        if not isinstance(n, ast.Call):
            continue
        where = f"{name}:{n.lineno}"
        f = n.func
        is_print = isinstance(f, ast.Name) and f.id == "print"
        is_log = (
            isinstance(f, ast.Attribute) and f.attr in _METHODS
            and isinstance(f.value, ast.Name) and f.value.id in _RECEIVERS
        )
        if not (is_print or is_log):
            continue
        if is_print:
            offenders.append(f"{where} print() in src")
        if is_log and f.attr == "exception":
            offenders.append(f"{where} log.exception (traceback carries message text)")
        for a in n.args:
            offenders += _arg_offenders(a, where)
        for k in n.keywords:
            if k.arg != "extra":
                offenders += _arg_offenders(k.value, where)
            elif isinstance(k.value, ast.Dict):
                for key, v in zip(k.value.keys, k.value.values, strict=True):
                    if isinstance(key, ast.Constant) and isinstance(key.value, str) and _BAD.search(key.value):
                        offenders.append(f"{where} sensitive extra key {key.value}")
                    offenders += _arg_offenders(v, where)
            else:
                offenders.append(f"{where} non-literal extra")
    return offenders


def test_src_has_no_suspicious_log_calls():
    root = Path(__file__).resolve().parents[1] / "src"
    offenders: list[str] = []
    for f in root.rglob("*.py"):
        offenders += scan_source(f.read_text(encoding="utf-8"), f.name)
    assert not offenders, offenders


@pytest.mark.parametrize("snippet", [
    'log.info(f"{question}")',
    'log.info("x", extra={"question": q})',
    'log.info("x", extra={"k": question})',
    '_log.warning("x %s", str(e))',
    'logging.error("x %s", filename)',
    'log.exception("boom")',
    'print(answer)',
    'logger.info("a %s", body.question)',
])
def test_scanner_flags_leaks(snippet):
    assert scan_source(snippet)


def test_scanner_allows_safe_calls():
    ok = 'log.warning("x", extra={"run_id": rid, "kind": type(e).__name__, "usd": round(u, 6)})'
    assert scan_source(ok) == []
