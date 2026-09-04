"""V012 - unique index on project names.

Project-name uniqueness was enforced only by an advisory Python-side check,
so two concurrent creates could both pass it and both commit. The index is
the database-level backstop. ``COLLATE NOCASE`` folds ASCII case only,
narrower than the Python ``casefold`` check that stays the primary guard —
the index exists to close the race, not to redefine uniqueness.

Legacy databases that already carry case-insensitive duplicate names keep
working: index creation is then skipped and uniqueness stays advisory for
that database.
"""

from __future__ import annotations

import sqlite3

from yoyo import step

__depends__: set[str] = {"V011__projects"}

CREATE_UNIQUE_NAME_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_name_nocase ON projects(name COLLATE NOCASE)"
)


def apply_step(conn) -> None:
    cur = conn.cursor()
    try:
        cur.execute(CREATE_UNIQUE_NAME_INDEX)
    except sqlite3.IntegrityError:
        # Pre-existing duplicate names: leave uniqueness advisory here.
        pass


def rollback_step(conn) -> None:
    cur = conn.cursor()
    cur.execute("DROP INDEX IF EXISTS idx_projects_name_nocase")


steps = [step(apply_step, rollback_step)]
