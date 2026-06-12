"""Tests for ScreenCast restore-token persistence."""

from arc_helper.screen.token_store import clear_token
from arc_helper.screen.token_store import load_token
from arc_helper.screen.token_store import save_token


def test_load_missing_returns_none(tmp_path):
    assert load_token(tmp_path / "token") is None


def test_round_trip(tmp_path):
    path = tmp_path / "token"
    save_token("abc123", path)
    assert load_token(path) == "abc123"


def test_whitespace_stripped(tmp_path):
    path = tmp_path / "token"
    path.write_text("  abc123\n", encoding="utf-8")
    assert load_token(path) == "abc123"


def test_empty_file_returns_none(tmp_path):
    path = tmp_path / "token"
    path.write_text("", encoding="utf-8")
    assert load_token(path) is None


def test_clear(tmp_path):
    path = tmp_path / "token"
    save_token("abc123", path)
    clear_token(path)
    assert load_token(path) is None
    clear_token(path)  # idempotent
