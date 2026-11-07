"""T-001 acceptance checks. The .env.example criterion is verified by the human (not tested here)."""
import fnmatch
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
BANNED = ["langchain", "llama-index", "openai", "faiss", "sqlalchemy", "psycopg2", "asyncio", "unstructured"]


def _all_specs() -> list[str]:
    specs = list(PYPROJECT["project"]["dependencies"])
    for group in PYPROJECT.get("dependency-groups", {}).values():
        specs += [s for s in group if isinstance(s, str)]
    return specs


def _assert_pinned(spec: str) -> None:
    assert "==" in spec, spec
    assert not re.search(r">=|<=|~=|\^|\*|>|<", spec), spec


def test_all_dependencies_pinned() -> None:
    specs = _all_specs()
    assert specs
    for spec in specs:
        _assert_pinned(spec)


def test_build_system_pinned() -> None:
    reqs = PYPROJECT["build-system"]["requires"]
    assert reqs
    for spec in reqs:
        _assert_pinned(spec)


def test_no_banned_dependencies() -> None:
    names = [re.split(r"[=<>~\[ ]", s, maxsplit=1)[0].lower() for s in _all_specs()]
    for name in names:
        assert not any(name.startswith(b) for b in BANNED), name


def test_legacy_files_removed() -> None:
    assert not (ROOT / "requirements.txt").exists()
    assert not (ROOT / "__init__.py").exists()


def test_layer_packages_and_app_import() -> None:
    for layer in ("api", "services", "core", "store"):
        assert (ROOT / "src/backend/app" / layer / "__init__.py").is_file(), layer
    import app.main

    assert hasattr(app.main, "app")


def test_prompts_readme_documents_version_header() -> None:
    path = ROOT / "prompts/README.md"
    assert path.is_file()
    assert "version" in path.read_text().lower()


def test_architecture_overview_mentions_layers() -> None:
    path = ROOT / "docs/architecture/overview.md"
    assert path.is_file()
    text = path.read_text().lower()
    for layer in ("api", "services", "core", "store"):
        assert layer in text, layer


def test_gitignore_entries() -> None:
    lines = {ln.strip() for ln in (ROOT / ".gitignore").read_text().splitlines()}
    for entry in (".env", "chroma_db/", "logs/runs/", ".venv/"):
        assert entry in lines, entry
    # "*.sqlite*" may be expressed as several globs; require every variant to be covered.
    for name in ("db.sqlite", "db.sqlite-wal", "db.sqlite-shm", "db.sqlite3"):
        assert any(fnmatch.fnmatch(name, ln) for ln in lines if ln and not ln.startswith("#")), name
