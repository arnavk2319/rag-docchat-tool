def test_app_imports() -> None:
    from app.main import app

    assert app.title
