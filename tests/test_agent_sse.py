"""T-037: stream event schema for citations/final, and the empty-citation abstain path."""

import json

from app.api import sse
from app.api.schemas import Citation, Span


def test_wire_citation_matches_shared_model_and_escapes():
    c = Citation(doc_id="d1", filename="<i>a</i>.txt", chunk_id="d1:1:0", sheet="S<1>",
                 span=Span(char_start=2, char_end=9))
    w = sse.wire_citation(c)
    assert set(w) == set(Citation.model_fields)
    Citation.model_validate({**w, "filename": "x", "sheet": "x"})
    assert "<" not in json.dumps(w) and w["span"] == {"char_start": 2, "char_end": 9}


def test_no_ready_docs_final_has_empty_citations():
    import asyncio

    from test_sse_extra import Req, collect, ctx, strict_frames

    ev = strict_frames(asyncio.run(collect(sse.stream_agent(Req(), "q", ctx(), {}))))
    assert ev[-1][1]["citations"] == [] and ev[-1][1]["stopped_reason"] == "abstained"
