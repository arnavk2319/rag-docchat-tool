from app.store.parents import Parent, SqliteParents


def P(owner, doc, pid, page=None, sheet=None, start=0):
    return Parent(owner_id=owner, doc_id=doc, parent_id=pid, page=page, sheet=sheet,
                  char_start=start, char_end=start + 1, text=pid)


def test_document_order_and_scoping(tmp_path):
    s = SqliteParents(tmp_path / "p.db")
    s.replace_doc("o", "d", [
        P("o", "d", "p2b", page=2, start=50),
        P("o", "d", "p2a", page=2, start=0),
        P("o", "d", "p1", page=1, start=900),
        P("o", "d", "sB", sheet="B"),
        P("o", "d", "sA", sheet="A"),
    ])
    s.replace_doc("o", "other", [P("o", "other", "x")])
    s.replace_doc("z", "d", [P("z", "d", "foreign")])
    assert [p.parent_id for p in s.list_doc_parents("o", "d")] == ["sA", "sB", "p1", "p2a", "p2b"]
    assert [p.parent_id for p in s.list_doc_parents("z", "d")] == ["foreign"]
    assert s.list_doc_parents("o", "missing") == []
    assert s.list_doc_parents("o'; --", "d") == []
