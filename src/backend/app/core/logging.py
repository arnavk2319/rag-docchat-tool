"""Stdlib logging: one JSON-line handler on the root logger, idempotent.

Call sites log ids, sizes, timings, tokens and USD only; never prompts or document text.
Extra fields passed via `extra={...}` are emitted as JSON keys.
"""

import json
import logging
import sys

_STD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_MARK = "_docchat_handler"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        out.update({k: v for k, v in record.__dict__.items() if k not in _STD})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging(level: int | str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers:
        if getattr(h, _MARK, False):
            return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _MARK, True)
    root.addHandler(handler)
