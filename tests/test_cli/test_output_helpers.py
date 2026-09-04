from __future__ import annotations

import io
import json
import sys

from agentos.cli.output import emit_error, print_json


def test_print_json_uses_stdout(capsys):
    print_json({"text": "héllo", "value": object()})

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["text"] == "héllo"
    assert captured.err == ""


def test_emit_error_json_uses_stderr(capsys):
    emit_error("bad input", json_output=True, code="INVALID_REQUEST", details={"field": "x"})

    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload == {
        "error": {
            "message": "bad input",
            "code": "INVALID_REQUEST",
            "details": {"field": "x"},
        }
    }


class _RestrictedStream:
    """A text stream with a restricted encoding and no binary ``.buffer``.

    Simulates ``sys.stdout`` on a terminal whose code page cannot represent
    non-ASCII characters: ``write()`` raises ``UnicodeEncodeError`` for any
    character outside ASCII, mirroring Python's behaviour on a real
    restricted stream (e.g. cp437 / legacy code pages).
    """

    encoding = "ascii"

    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, text: str) -> int:
        try:
            text.encode("ascii")
        except UnicodeEncodeError as exc:
            raise UnicodeEncodeError(
                "ascii", text, exc.start, exc.end, "ordinal not in range(128)"
            ) from exc
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        pass


class _BufferStream:
    """A text stream that exposes a writable binary ``.buffer`` (real stdout)."""

    encoding = "cp1252"

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, text: str) -> int:  # pragma: no cover — not called when buffer present
        return self.buffer.write(text.encode("ascii"))

    def flush(self) -> None:
        pass


def test_print_json_survives_restricted_stream_losslessly(monkeypatch):
    """An em-dash on an ASCII stream must come out as ``\\u2014``, not ``?``.

    The fallback uses ``errors="backslashreplace"`` — the data survives as a
    round-trippable escape. ``errors="replace"`` (what a naive fix reaches for)
    would silently turn the em-dash into ``?`` and corrupt the JSON.
    """
    stream = _RestrictedStream()
    monkeypatch.setattr(sys, "stdout", stream)

    print_json({"desc": "wallet — audit"})

    assert stream.written
    out = stream.written[0]
    # No literal em-dash can survive ASCII; it must be escaped, not dropped.
    assert "\u2014" not in out
    assert "\\u2014" in out
    # Still valid, parseable JSON.
    assert json.loads(out) == {"desc": "wallet — audit"}


def test_emit_error_survives_restricted_stderr_losslessly(monkeypatch):
    stream = _RestrictedStream()
    monkeypatch.setattr(sys, "stderr", stream)

    emit_error("boom — broken", json_output=True, code="E_FAIL")

    assert stream.written
    assert "\\u2014" in stream.written[0]
    assert json.loads(stream.written[0]) == {
        "error": {"message": "boom — broken", "code": "E_FAIL"}
    }


def test_print_json_writes_utf8_bytes_to_buffer(monkeypatch):
    """When a binary ``.buffer`` exists, bytes go out as UTF-8, lossless.

    A real ``sys.stdout`` on Windows still has a UTF-8-friendly binary
    ``.buffer`` underneath the cp1252 text layer, so writing the encoded
    bytes there keeps the JSON contract intact regardless of the code page.
    """
    stream = _BufferStream()
    monkeypatch.setattr(sys, "stdout", stream)

    print_json({"msg": "héllo — world"})

    raw = stream.buffer.getvalue()
    assert raw == '{"msg": "héllo — world"}\n'.encode()
    assert b"\xe2\x80\x94" in raw  # em-dash in UTF-8
