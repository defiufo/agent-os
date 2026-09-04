from __future__ import annotations

import json

from agentos.tools.builtin import code_exec


def test_execution_result_json_redacts_secrets_in_output() -> None:
    # execute_code must apply the same egress redaction as shell.py, or a
    # script that prints os.environ / a credential file leaks it raw.
    stdout = (
        "OPENAI_API_KEY=sk-pro...5pqr\n"
        "MY_CUSTOM_SECRET=supersecretvalue123\n"
        "token=eyJhbG...wIn0.abc\n"
    )
    payload = json.loads(
        code_exec._execution_result_json(
            returncode=0,
            stdout=stdout,
            stderr="",
            timed_out=False,
            elapsed_ms=12,
        )
    )
    out = payload["stdout"]
    # The known-prefix + named-credential passes must have masked the values.
    assert "sk-pro...i789" not in out
    assert "supersecretvalue123" not in out
    assert "eyJhbG...NiJ9" not in out
    # Structure-preserving mask, not a total drop.
    assert "OPENAI_API_KEY=" in out
    assert "MY_CUSTOM_SECRET=" in out


def test_execution_result_json_redacts_before_truncating() -> None:
    # A credential straddling the output cap must still be masked: redaction
    # runs before truncation, so the shape pattern sees the whole token.
    # If truncation ran first, the surviving prefix would no longer match
    # and the partial key would leak raw.
    key = "sk-ant" + "A" * 80
    stdout = "x" * (code_exec._MAX_OUTPUT_CHARS - 21) + "\n" + key
    payload = json.loads(
        code_exec._execution_result_json(
            returncode=0,
            stdout=stdout,
            stderr="",
            timed_out=False,
            elapsed_ms=12,
        )
    )
    out = payload["stdout"]
    # The raw key must not survive in any form, masked or partial.
    assert key not in out
    assert "A" * 40 not in out
    # The masked sentinel must be present instead.
    assert "sk-ant***" in out
    # Output is still capped at the limit.
    assert len(out) <= code_exec._MAX_OUTPUT_CHARS
