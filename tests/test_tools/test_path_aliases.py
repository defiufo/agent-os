from __future__ import annotations

from pathlib import Path, PureWindowsPath

from agentos.tools.path_aliases import resolve_workspace_alias


def test_workspace_alias_accepts_windows_root_relative_path(tmp_path):
    resolved = resolve_workspace_alias(
        PureWindowsPath("/workspace/figure.pdf"),
        tmp_path,
    )

    assert resolved == (tmp_path / "figure.pdf").resolve(strict=False)


def test_alias_does_not_strip_nested_workspace_dir(tmp_path):
    # A real ``workspace/`` folder inside the configured root must stay
    # nested. The last-segment rewrite used to map this to ``root/data.txt``.
    root = tmp_path / "proj"
    nested = root / "workspace" / "data.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text("nested")
    (root / "data.txt").write_text("wrong")

    got = resolve_workspace_alias(nested, root)
    assert got == nested.resolve()


def test_alias_keeps_nested_workspace_under_default_shaped_root(tmp_path):
    root = tmp_path / ".agentos" / "workspace"
    nested = root / "workspace" / "foo.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text("keep")
    (root / "foo.txt").write_text("wrong")

    got = resolve_workspace_alias(nested, root)
    assert got == nested.resolve()


def test_alias_still_remaps_sandbox_workspace_prefix(tmp_path):
    got = resolve_workspace_alias(Path("/workspace/figure.pdf"), tmp_path)
    assert got == (tmp_path / "figure.pdf").resolve(strict=False)


def test_alias_still_remaps_hallucinated_default_home_when_root_is_custom(tmp_path):
    root = tmp_path / "custom"
    root.mkdir()
    hallucinated = Path("/tmp/fake-home/.agentos/workspace/abstract.tex")
    got = resolve_workspace_alias(hallucinated, root)
    assert got == (root / "abstract.tex").resolve(strict=False)
