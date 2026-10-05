"""Unit tests for the acquisition side: fetching a real HTTP source and mapping it.

Offline by design (see conftest.py): ``urlopen`` is stubbed, so these pin the **parsing and
mapping** logic. The live network path is exercised by the demo commands documented in README.md
(`ingest_docs.py --url ...` / `collect_orders.py --url ...`).

NOTE: the Chinese strings below are test data -- do not translate.
"""

import importlib.util
import json
import pathlib

import pytest

SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SERVER_DIR.parent / "scripts"


def _load_script(name: str):
    """Import ``scripts/<name>.py`` by path -- the scripts are not a package."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ingest_module():
    return _load_script("ingest_docs")


@pytest.fixture(scope="module")
def collect_module():
    return _load_script("collect_orders")


class _Headers:
    def __init__(self, content_type: str, charset: str | None):
        self._content_type = content_type
        self._charset = charset

    def get_content_type(self) -> str:
        return self._content_type

    def get_content_charset(self) -> str | None:
        return self._charset


def _patch_urlopen(module, monkeypatch, body: str, content_type: str, charset: str | None = "utf-8"):
    """Replace urlopen with a stub returning ``body`` -- keeps these tests off the network."""

    class _Response:
        def __init__(self) -> None:
            self.headers = _Headers(content_type, charset)

        def read(self) -> bytes:
            return body.encode(charset or "utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(module, "urlopen", lambda *args, **kwargs: _Response())


# ---------------------------------------------------------------- HTML -> text
def test_html_to_text_keeps_visible_text_and_drops_scripts(ingest_module):
    html = (
        "<html><head><style>b{color:red}</style><script>var x=1;</script></head>"
        "<body><h1>标题</h1><p>正文 A</p></body></html>"
    )
    text = ingest_module.html_to_text(html)
    assert "标题" in text and "正文 A" in text
    assert "var x=1" not in text and "color:red" not in text


# ---------------------------------------------------------------- fetch shapes
def test_fetch_url_accepts_an_html_page(ingest_module, monkeypatch):
    _patch_urlopen(ingest_module, monkeypatch, "<p>你好</p>", "text/html")
    text, content_type = ingest_module.fetch_url("https://example.com/doc")
    assert text == "你好"
    assert content_type == "text/html"


def test_fetch_url_uses_json_extract_field(ingest_module, monkeypatch):
    """Wikipedia-REST-style payloads carry the readable text in ``extract``."""
    payload = json.dumps({"title": "杭州", "extract": "浙江省省会"}, ensure_ascii=False)
    _patch_urlopen(ingest_module, monkeypatch, payload, "application/json")
    text, _ = ingest_module.fetch_url("https://example.com/api")
    assert "杭州" in text and "浙江省省会" in text


def test_fetch_url_rejects_json_without_extract(ingest_module, monkeypatch):
    _patch_urlopen(ingest_module, monkeypatch, '{"a": 1}', "application/json")
    with pytest.raises(ValueError):
        ingest_module.fetch_url("https://example.com/api")


def test_fetch_url_rejects_non_http_scheme(ingest_module):
    """Only http(s): a local path must not become a "fetch"."""
    with pytest.raises(ValueError):
        ingest_module.fetch_url("file:///etc/passwd")


def test_title_for_prefers_the_first_line(ingest_module):
    assert ingest_module.title_for("https://x/y", "标题行\n正文") == "标题行"
    assert ingest_module.title_for("https://x/some-path", "x" * 200) == "some-path"


# ---------------------------------------------------------------- CLI flow
def test_should_list_treats_url_as_ingest(ingest_module):
    """Regression: ``ingest_docs.py --url ...`` used to fall into list mode, print the document
    list and **silently skip the fetch**."""

    class Args:
        list = False
        paths: list = []
        dir = None
        url = ["https://example.com/doc"]

    class ListArgs(Args):
        url = None

    assert ingest_module.should_list(Args()) is False
    assert ingest_module.should_list(ListArgs()) is True


# ---------------------------------------------------------------- field mapping
def test_map_api_item_maps_fields_explicitly(collect_module):
    """The mapping is the single place a real source is plugged in, so pin its shape."""
    published = {"tag_name": "v1.2.3", "author": {"login": "someone"}, "prerelease": False}
    row = collect_module.map_api_item(published)
    assert row == {
        "order_id": "v1.2.3",
        "customer": "someone",
        "status": "已发布",
        "amount": 0.0,
    }

    prerelease = {"tag_name": "v2.0.0-rc1", "author": {"login": "other"}, "prerelease": True}
    assert collect_module.map_api_item(prerelease)["status"] == "预发布"


# ---------------------------------------------------------------- db hardening
def test_knowledge_db_is_wal_and_sound(tmp_path, monkeypatch):
    """Locked in after a real corruption event: WAL + a busy timeout on every connection, and a
    direct integrity check so a damaged file is reported clearly instead of surfacing as
    "database disk image is malformed" from an unrelated call."""
    import knowledge

    monkeypatch.setattr(knowledge, "DB_PATH", tmp_path / "kb.db")
    knowledge.init_db()
    assert knowledge.integrity_check() == "ok"
    with knowledge._conn() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
