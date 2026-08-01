from __future__ import annotations

import sqlite3
from pathlib import Path


class SeenProjectStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = NORMAL")
        self.connection.execute("PRAGMA temp_store = MEMORY")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_projects (
                project_id INTEGER PRIMARY KEY,
                seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                notified INTEGER NOT NULL,
                score REAL NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS filter_terms (
                kind TEXT NOT NULL CHECK(kind IN ('skill', 'search_word')),
                value TEXT NOT NULL,
                normalized_value TEXT NOT NULL,
                PRIMARY KEY (kind, normalized_value)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def initialize_preferences(
        self,
        *,
        skills: tuple[str, ...],
        search_words: tuple[str, ...],
        threshold: float,
    ) -> bool:
        """Import legacy env preferences once; afterward SQLite is authoritative."""
        if self.get_setting("preferences_initialized") == "1":
            return False
        with self.connection:
            for skill in skills:
                self._add_term("skill", skill)
            for search_word in search_words:
                self._add_term("search_word", search_word)
            self._set_setting("score_threshold", str(threshold))
            self._set_setting("preferences_initialized", "1")
        return True

    def _add_term(self, kind: str, value: str) -> bool:
        cleaned = value.strip()
        if not cleaned:
            return False
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO filter_terms(kind, value, normalized_value)
            VALUES (?, ?, ?)
            """,
            (kind, cleaned, cleaned.casefold()),
        )
        return cursor.rowcount > 0

    def add_term(self, kind: str, value: str) -> bool:
        with self.connection:
            return self._add_term(kind, value)

    def delete_term(self, kind: str, value: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM filter_terms WHERE kind = ? AND normalized_value = ?",
                (kind, value.strip().casefold()),
            )
        return cursor.rowcount > 0

    def list_terms(self, kind: str) -> tuple[str, ...]:
        rows = self.connection.execute(
            "SELECT value FROM filter_terms WHERE kind = ? ORDER BY normalized_value",
            (kind,),
        ).fetchall()
        return tuple(row[0] for row in rows)

    def _set_setting(self, key: str, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO runtime_settings(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def set_setting(self, key: str, value: str) -> None:
        with self.connection:
            self._set_setting(key, value)

    def get_setting(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM runtime_settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def get_threshold(self) -> float:
        value = self.get_setting("score_threshold")
        if value is None:
            raise RuntimeError("Runtime preferences have not been initialized")
        return float(value)

    def set_threshold(self, threshold: float) -> None:
        self.set_setting("score_threshold", str(threshold))

    def get_telegram_update_offset(self) -> int | None:
        value = self.get_setting("telegram_update_offset")
        return int(value) if value is not None else None

    def set_telegram_update_offset(self, offset: int) -> None:
        self.set_setting("telegram_update_offset", str(offset))

    def has_seen(self, project_id: int) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM seen_projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        return row is not None

    def seen_project_ids(self, project_ids: list[int]) -> set[int]:
        if not project_ids:
            return set()
        seen_ids: set[int] = set()
        for start in range(0, len(project_ids), 900):
            chunk = project_ids[start : start + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = self.connection.execute(
                f"SELECT project_id FROM seen_projects WHERE project_id IN ({placeholders})",
                chunk,
            ).fetchall()
            seen_ids.update(row[0] for row in rows)
        return seen_ids

    def mark_seen(self, project_id: int, *, notified: bool, score: float) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO seen_projects(project_id, notified, score) VALUES (?, ?, ?)",
            (project_id, int(notified), score),
        )
        self.connection.commit()

    def mark_seen_many(self, records: list[tuple[int, bool, float]]) -> None:
        if not records:
            return
        with self.connection:
            self.connection.executemany(
                """
                INSERT OR IGNORE INTO seen_projects(project_id, notified, score)
                VALUES (?, ?, ?)
                """,
                ((project_id, int(notified), score) for project_id, notified, score in records),
            )

    def clear_history(self) -> int:
        """Delete processed-project history without touching preferences or settings."""
        with self.connection:
            cursor = self.connection.execute("DELETE FROM seen_projects")
        return cursor.rowcount

    def close(self) -> None:
        self.connection.close()
