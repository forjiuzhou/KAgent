"""Tests for Telegram adapter helpers."""

from noteweaver.adapters.telegram_adapter import _split_message


def test_split_message_short_unchanged() -> None:
    assert _split_message("hello", 4000) == ["hello"]


def test_split_message_splits_on_newline_when_possible() -> None:
    text = "a\n" * 3000  # long but with newlines
    chunks = _split_message(text, 100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == text


def test_split_message_hard_split_without_newline() -> None:
    text = "x" * 250
    chunks = _split_message(text, 100)
    assert chunks == ["x" * 100, "x" * 100, "x" * 50]
