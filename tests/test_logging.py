import logging

from app.core.logging import setup_logging


def test_single_handler_idempotent():
    root = logging.getLogger()
    before = len(root.handlers)
    setup_logging()
    setup_logging()
    assert len(root.handlers) <= before + 1
