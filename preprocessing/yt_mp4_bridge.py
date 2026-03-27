#!/usr/bin/env python3
"""
Run a local HTTP service for the fullscreen annotator.

Features:
- YouTube -> mp4 conversion at /api/yt-mp4
- Shared SQLite-backed annotation library at /api/shared-library/*
- Browser-streamable hosted video assets for cross-checking

Usage:
  python preprocessing/yt_mp4_bridge.py --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import mimetypes
import secrets
import subprocess
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from yt_mp4 import convert_yt_mp4


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR
DEFAULT_SHARED_ROOT = PROJECT_ROOT / "shared_library"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_after_seconds_iso(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0, int(seconds)))).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sanitize_filename(value: str, fallback: str = "upload.mp4") -> str:
    name = Path(str(value or "").strip()).name
    if not name:
        return fallback
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", ".", " "} else "_" for ch in name).strip()
    return safe or fallback


def guess_extension(filename: str, fallback: str = ".mp4") -> str:
    suffix = Path(str(filename or "").strip()).suffix.lower()
    return suffix if suffix else fallback


def youtube_thumbnail_url(video_id: str) -> str:
    return "https://i.ytimg.com/vi/{}/hqdefault.jpg".format(video_id)


def youtube_source_from_url(raw_url: str) -> dict[str, str] | None:
    try:
        parsed = urlparse(str(raw_url or "").strip())
    except Exception:  # noqa: BLE001
        return None

    host = parsed.netloc.lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]

    video_id = ""
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif host.endswith("youtube.com"):
        path = parsed.path.strip("/")
        query = parse_qs(parsed.query)
        if path == "watch":
            video_id = (query.get("v") or [""])[0]
        elif path.startswith("shorts/"):
            video_id = path.split("/", 1)[1].split("/", 1)[0]
        elif path.startswith("embed/"):
            video_id = path.split("/", 1)[1].split("/", 1)[0]
        elif path.startswith("live/"):
            video_id = path.split("/", 1)[1].split("/", 1)[0]

    video_id = "".join(ch for ch in video_id if ch.isalnum() or ch in {"-", "_"})
    if len(video_id) < 6:
        return None

    canonical_url = "https://www.youtube.com/watch?v={}".format(video_id)
    return {
        "sourceType": "youtube",
        "sourceKey": video_id,
        "sourceUrl": canonical_url,
        "sourceThumbnailUrl": youtube_thumbnail_url(video_id),
    }


class SharedLibraryStore:
    SESSION_TTL_SECONDS = 60 * 60 * 24 * 30

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir.expanduser().resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.video_dir = self.root_dir / "videos"
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnail_dir = self.root_dir / "thumbnails"
        self.thumbnail_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root_dir / "annotations.sqlite3"
        self._write_lock = threading.Lock()
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_column(self, connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        rows = connection.execute("PRAGMA table_info({})".format(table)).fetchall()
        existing = {str(row["name"]) for row in rows}
        if column not in existing:
            connection.execute(
                "ALTER TABLE {} ADD COLUMN {} {}".format(table, column, definition)
            )

    @staticmethod
    def _entry_sort_key(entry: dict[str, Any]) -> tuple[str, int]:
        return (
            str(entry.get("updatedAt") or entry.get("createdAt") or ""),
            int(entry.get("id") or 0),
        )

    @staticmethod
    def _annotator_dedupe_key(value: Any) -> str:
        text = str(value or "").strip()
        return text.casefold() or "__unknown__"

    def _latest_unique_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        latest_by_annotator: dict[str, dict[str, Any]] = {}
        for entry in sorted(entries, key=self._entry_sort_key, reverse=True):
            dedupe_key = self._annotator_dedupe_key(entry.get("annotator"))
            if dedupe_key not in latest_by_annotator:
                latest_by_annotator[dedupe_key] = entry
        return sorted(latest_by_annotator.values(), key=self._entry_sort_key, reverse=True)

    @staticmethod
    def _account_username_key(username: str) -> str:
        return str(username or "").strip().casefold()

    @staticmethod
    def _validate_account_username(username: str) -> str:
        clean = str(username or "").strip()
        if len(clean) < 3:
            raise ValueError("Annotator username must be at least 3 characters.")
        if len(clean) > 64:
            raise ValueError("Annotator username must be 64 characters or fewer.")
        if not all(ch.isalnum() or ch in {" ", ".", "_", "-"} for ch in clean):
            raise ValueError("Annotator username can only use letters, numbers, spaces, '.', '_' or '-'.")
        return clean

    @staticmethod
    def _validate_account_password(password: str) -> str:
        clean = str(password or "")
        if len(clean) < 8:
            raise ValueError("Password must be at least 8 characters.")
        if len(clean) > 256:
            raise ValueError("Password is too long.")
        return clean

    @staticmethod
    def _hash_session_token(token: str) -> str:
        return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_password(password: str, salt_hex: str) -> str:
        salt = bytes.fromhex(salt_hex)
        return hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt, 200_000).hex()

    @staticmethod
    def _build_account_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "username": str(row["username"]),
            "createdAt": str(row["created_at"]),
            "lastLoginAt": str(row["last_login_at"]) if row["last_login_at"] else None,
        }

    def _fetch_account_row_by_id(self, connection: sqlite3.Connection, account_id: int) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT id, username, username_key, password_salt, password_hash, created_at, last_login_at
            FROM annotator_accounts
            WHERE id = ?
            """,
            (int(account_id),),
        ).fetchone()

    def _fetch_account_row_by_username(self, connection: sqlite3.Connection, username: str) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT id, username, username_key, password_salt, password_hash, created_at, last_login_at
            FROM annotator_accounts
            WHERE username_key = ?
            """,
            (self._account_username_key(username),),
        ).fetchone()

    def _create_auth_session(self, connection: sqlite3.Connection, account_id: int) -> str:
        token = secrets.token_urlsafe(32)
        timestamp = utc_now_iso()
        connection.execute(
            """
            INSERT INTO auth_sessions (
                token_hash,
                account_id,
                created_at,
                last_used_at,
                expires_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                self._hash_session_token(token),
                int(account_id),
                timestamp,
                timestamp,
                utc_after_seconds_iso(self.SESSION_TTL_SECONDS),
            ),
        )
        return token

    def register_account(self, username: str, password: str) -> tuple[dict[str, Any], str]:
        clean_username = self._validate_account_username(username)
        clean_password = self._validate_account_password(password)
        timestamp = utc_now_iso()
        salt_hex = secrets.token_hex(16)
        password_hash = self._hash_password(clean_password, salt_hex)

        with self._connect() as connection:
            existing = self._fetch_account_row_by_username(connection, clean_username)
            if existing:
                raise ValueError("Annotator account already exists.")
            cursor = connection.execute(
                """
                INSERT INTO annotator_accounts (
                    username,
                    username_key,
                    password_salt,
                    password_hash,
                    created_at,
                    last_login_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_username,
                    self._account_username_key(clean_username),
                    salt_hex,
                    password_hash,
                    timestamp,
                    timestamp,
                ),
            )
            account_id = int(cursor.lastrowid)
            token = self._create_auth_session(connection, account_id)
            account_row = self._fetch_account_row_by_id(connection, account_id)
            if not account_row:
                raise RuntimeError("Could not load newly created annotator account.")
            return self._build_account_payload(account_row), token

    def login_account(self, username: str, password: str) -> tuple[dict[str, Any], str]:
        clean_username = self._validate_account_username(username)
        clean_password = self._validate_account_password(password)
        timestamp = utc_now_iso()

        with self._connect() as connection:
            account_row = self._fetch_account_row_by_username(connection, clean_username)
            if not account_row:
                raise ValueError("Invalid username or password.")
            expected_hash = str(account_row["password_hash"])
            actual_hash = self._hash_password(clean_password, str(account_row["password_salt"]))
            if not hmac.compare_digest(actual_hash, expected_hash):
                raise ValueError("Invalid username or password.")
            connection.execute(
                """
                UPDATE annotator_accounts
                SET last_login_at = ?
                WHERE id = ?
                """,
                (timestamp, int(account_row["id"])),
            )
            token = self._create_auth_session(connection, int(account_row["id"]))
            refreshed_row = self._fetch_account_row_by_id(connection, int(account_row["id"]))
            if not refreshed_row:
                raise RuntimeError("Could not load annotator account.")
            return self._build_account_payload(refreshed_row), token

    def get_account_for_token(self, token: str) -> dict[str, Any] | None:
        clean_token = str(token or "").strip()
        if not clean_token:
            return None
        timestamp = utc_now_iso()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    a.id,
                    a.username,
                    a.created_at,
                    a.last_login_at
                FROM auth_sessions AS s
                JOIN annotator_accounts AS a
                  ON a.id = s.account_id
                WHERE s.token_hash = ?
                  AND s.expires_at > ?
                LIMIT 1
                """,
                (self._hash_session_token(clean_token), timestamp),
            ).fetchone()
            if not row:
                return None
            connection.execute(
                """
                UPDATE auth_sessions
                SET last_used_at = ?
                WHERE token_hash = ?
                """,
                (timestamp, self._hash_session_token(clean_token)),
            )
            return self._build_account_payload(row)

    def revoke_session(self, token: str) -> bool:
        clean_token = str(token or "").strip()
        if not clean_token:
            return False
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?",
                (self._hash_session_token(clean_token),),
            )
            return cursor.rowcount > 0

    def _collapse_duplicate_annotation_entries(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT
                id,
                video_asset_id,
                annotator,
                created_at,
                COALESCE(updated_at, created_at) AS activity_at
            FROM annotation_entries
            ORDER BY
                video_asset_id ASC,
                lower(trim(annotator)) ASC,
                COALESCE(updated_at, created_at) DESC,
                id DESC
            """
        ).fetchall()
        duplicate_ids: list[int] = []
        seen: set[tuple[int, str]] = set()
        for row in rows:
            dedupe_key = (
                int(row["video_asset_id"]),
                self._annotator_dedupe_key(row["annotator"]),
            )
            if dedupe_key in seen:
                duplicate_ids.append(int(row["id"]))
                continue
            seen.add(dedupe_key)

        if not duplicate_ids:
            return

        placeholders = ", ".join("?" for _ in duplicate_ids)
        connection.execute(
            "DELETE FROM annotation_entries WHERE id IN ({})".format(placeholders),
            tuple(duplicate_ids),
        )

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS video_assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    storage_name TEXT NOT NULL UNIQUE,
                    original_filename TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL UNIQUE,
                    source_path TEXT,
                    thumbnail_storage_name TEXT,
                    source_type TEXT,
                    source_key TEXT,
                    source_url TEXT,
                    source_thumbnail_url TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS annotation_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_asset_id INTEGER NOT NULL,
                    annotator TEXT NOT NULL,
                    video_title TEXT NOT NULL,
                    video_file_path TEXT,
                    annotation_json TEXT NOT NULL,
                    note_count INTEGER NOT NULL DEFAULT 0,
                    character_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    FOREIGN KEY (video_asset_id) REFERENCES video_assets(id)
                );

                CREATE TABLE IF NOT EXISTS annotator_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    username_key TEXT NOT NULL UNIQUE,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES annotator_accounts(id)
                );
                """
            )
            self._ensure_column(connection, "video_assets", "thumbnail_storage_name", "TEXT")
            self._ensure_column(connection, "video_assets", "source_type", "TEXT")
            self._ensure_column(connection, "video_assets", "source_key", "TEXT")
            self._ensure_column(connection, "video_assets", "source_url", "TEXT")
            self._ensure_column(connection, "video_assets", "source_thumbnail_url", "TEXT")
            self._ensure_column(connection, "annotation_entries", "updated_at", "TEXT")
            self._collapse_duplicate_annotation_entries(connection)
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_annotation_entries_created_at
                ON annotation_entries(created_at DESC, id DESC)
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_annotation_entries_video_annotator_unique
                ON annotation_entries(video_asset_id, lower(trim(annotator)))
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_video_assets_source_key
                ON video_assets(source_type, source_key)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_account_id
                ON auth_sessions(account_id, expires_at DESC)
                """
            )

    def _fetch_asset_by_sha(self, sha256_hex: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    storage_name,
                    original_filename,
                    file_size,
                    sha256,
                    source_path,
                    thumbnail_storage_name,
                    source_type,
                    source_key,
                    source_url,
                    source_thumbnail_url,
                    created_at
                FROM video_assets
                WHERE sha256 = ?
                """,
                (sha256_hex,),
            ).fetchone()
        return self._row_to_asset(row) if row else None

    def _fetch_asset_by_source(self, source_type: str, source_key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    storage_name,
                    original_filename,
                    file_size,
                    sha256,
                    source_path,
                    thumbnail_storage_name,
                    source_type,
                    source_key,
                    source_url,
                    source_thumbnail_url,
                    created_at
                FROM video_assets
                WHERE source_type = ? AND source_key = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (source_type, source_key),
            ).fetchone()
        return self._row_to_asset(row) if row else None

    def _fetch_asset_by_id(self, asset_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    storage_name,
                    original_filename,
                    file_size,
                    sha256,
                    source_path,
                    thumbnail_storage_name,
                    source_type,
                    source_key,
                    source_url,
                    source_thumbnail_url,
                    created_at
                FROM video_assets
                WHERE id = ?
                """,
                (asset_id,),
            ).fetchone()
        return self._row_to_asset(row) if row else None

    def _insert_asset(
        self,
        storage_name: str,
        original_filename: str,
        file_size: int,
        sha256_hex: str,
        source_path: str | None,
        source_type: str | None,
        source_key: str | None,
        source_url: str | None,
        source_thumbnail_url: str | None,
    ) -> dict[str, Any]:
        created_at = utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO video_assets (
                    storage_name,
                    original_filename,
                    file_size,
                    sha256,
                    source_path,
                    thumbnail_storage_name,
                    source_type,
                    source_key,
                    source_url,
                    source_thumbnail_url,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    storage_name,
                    original_filename,
                    file_size,
                    sha256_hex,
                    source_path,
                    None,
                    source_type,
                    source_key,
                    source_url,
                    source_thumbnail_url,
                    created_at,
                ),
            )
            asset_id = int(cursor.lastrowid)
        return {
            "id": asset_id,
            "storageName": storage_name,
            "originalFilename": original_filename,
            "fileSize": file_size,
            "sha256": sha256_hex,
            "sourcePath": source_path,
            "thumbnailStorageName": None,
            "sourceType": source_type,
            "sourceKey": source_key,
            "sourceUrl": source_url,
            "sourceThumbnailUrl": source_thumbnail_url,
            "createdAt": created_at,
        }

    def _row_to_asset(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "storageName": str(row["storage_name"]),
            "originalFilename": str(row["original_filename"]),
            "fileSize": int(row["file_size"]),
            "sha256": str(row["sha256"]),
            "sourcePath": row["source_path"],
            "thumbnailStorageName": row["thumbnail_storage_name"],
            "sourceType": row["source_type"],
            "sourceKey": row["source_key"],
            "sourceUrl": row["source_url"],
            "sourceThumbnailUrl": row["source_thumbnail_url"],
            "createdAt": str(row["created_at"]),
        }

    def _update_asset_metadata(
        self,
        asset_id: int,
        *,
        source_type: str | None = None,
        source_key: str | None = None,
        source_url: str | None = None,
        source_thumbnail_url: str | None = None,
        thumbnail_storage_name: str | None = None,
    ) -> dict[str, Any]:
        assignments = []
        values: list[Any] = []

        if source_type:
            assignments.append("source_type = COALESCE(source_type, ?)")
            values.append(source_type)
        if source_key:
            assignments.append("source_key = COALESCE(source_key, ?)")
            values.append(source_key)
        if source_url:
            assignments.append("source_url = COALESCE(source_url, ?)")
            values.append(source_url)
        if source_thumbnail_url:
            assignments.append("source_thumbnail_url = COALESCE(source_thumbnail_url, ?)")
            values.append(source_thumbnail_url)
        if thumbnail_storage_name:
            assignments.append("thumbnail_storage_name = COALESCE(thumbnail_storage_name, ?)")
            values.append(thumbnail_storage_name)

        if assignments:
            values.append(int(asset_id))
            with self._connect() as connection:
                connection.execute(
                    "UPDATE video_assets SET {} WHERE id = ?".format(", ".join(assignments)),
                    tuple(values),
                )

        refreshed = self._fetch_asset_by_id(int(asset_id))
        if refreshed is None:
            raise ValueError("Unknown asset id.")
        return refreshed

    def _ensure_asset_media_metadata(self, asset: dict[str, Any] | None) -> dict[str, Any] | None:
        if asset is None:
            return None

        source_type = str(asset.get("sourceType") or "").strip().lower()
        source_key = str(asset.get("sourceKey") or "").strip()
        source_thumbnail_url = str(asset.get("sourceThumbnailUrl") or "").strip()
        if source_type == "youtube" and source_key and not source_thumbnail_url:
            asset = self._update_asset_metadata(
                int(asset["id"]),
                source_thumbnail_url=youtube_thumbnail_url(source_key),
            )

        video_path = self.video_dir / asset["storageName"]
        return self._ensure_thumbnail_for_asset(asset, video_path)

    def _ensure_thumbnail_for_asset(self, asset: dict[str, Any], video_path: Path) -> dict[str, Any]:
        existing_name = str(asset.get("thumbnailStorageName") or "").strip()
        if existing_name:
            existing_path = (self.thumbnail_dir / existing_name).resolve()
            if existing_path.is_file():
                return asset

        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is None or not video_path.is_file():
            return asset

        thumbnail_name = "{}.jpg".format(asset["id"])
        thumbnail_path = self.thumbnail_dir / thumbnail_name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as handle:
            temp_thumbnail_path = Path(handle.name)
        commands = [
            [
                ffmpeg_path,
                "-y",
                "-ss",
                "00:00:01.000",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                "scale=480:-1",
                str(temp_thumbnail_path),
            ],
            [
                ffmpeg_path,
                "-y",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                "scale=480:-1",
                str(temp_thumbnail_path),
            ],
        ]
        try:
            for command in commands:
                temp_thumbnail_path.unlink(missing_ok=True)
                result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                if result.returncode == 0 and temp_thumbnail_path.is_file() and temp_thumbnail_path.stat().st_size > 0:
                    shutil.move(str(temp_thumbnail_path), str(thumbnail_path))
                    return self._update_asset_metadata(asset["id"], thumbnail_storage_name=thumbnail_name)
            return asset
        finally:
            temp_thumbnail_path.unlink(missing_ok=True)

    def _copy_or_reuse_asset(
        self,
        temp_path: Path,
        original_filename: str,
        file_size: int,
        sha256_hex: str,
        source_path: str | None,
        source_type: str | None = None,
        source_key: str | None = None,
        source_url: str | None = None,
        source_thumbnail_url: str | None = None,
    ) -> dict[str, Any]:
        existing = self._fetch_asset_by_sha(sha256_hex)
        if existing:
            temp_path.unlink(missing_ok=True)
            existing = self._update_asset_metadata(
                existing["id"],
                source_type=source_type,
                source_key=source_key,
                source_url=source_url,
                source_thumbnail_url=source_thumbnail_url,
            )
            return self._ensure_thumbnail_for_asset(existing, self.video_dir / existing["storageName"])

        extension = guess_extension(original_filename, fallback=".mp4")
        storage_name = sha256_hex + extension
        destination = self.video_dir / storage_name

        with self._write_lock:
            existing = self._fetch_asset_by_sha(sha256_hex)
            if existing:
                temp_path.unlink(missing_ok=True)
                existing = self._update_asset_metadata(
                    existing["id"],
                    source_type=source_type,
                    source_key=source_key,
                    source_url=source_url,
                    source_thumbnail_url=source_thumbnail_url,
                )
                return self._ensure_thumbnail_for_asset(existing, self.video_dir / existing["storageName"])

            if destination.exists():
                temp_path.unlink(missing_ok=True)
            else:
                temp_path.replace(destination)

            try:
                return self._insert_asset(
                    storage_name=storage_name,
                    original_filename=original_filename,
                    file_size=file_size,
                    sha256_hex=sha256_hex,
                    source_path=source_path,
                    source_type=source_type,
                    source_key=source_key,
                    source_url=source_url,
                    source_thumbnail_url=source_thumbnail_url,
                )
                return self._ensure_thumbnail_for_asset(asset, destination)
            except sqlite3.IntegrityError:
                temp_path.unlink(missing_ok=True)
                existing = self._fetch_asset_by_sha(sha256_hex)
                if existing is None and source_type and source_key:
                    existing = self._fetch_asset_by_source(source_type, source_key)
                if existing:
                    existing = self._update_asset_metadata(
                        existing["id"],
                        source_type=source_type,
                        source_key=source_key,
                        source_url=source_url,
                        source_thumbnail_url=source_thumbnail_url,
                    )
                    return self._ensure_thumbnail_for_asset(existing, self.video_dir / existing["storageName"])
                raise

    def save_upload(self, stream, content_length: int, original_filename: str) -> dict[str, Any]:
        safe_name = sanitize_filename(original_filename)
        hasher = hashlib.sha256()
        written = 0

        with tempfile.NamedTemporaryFile(delete=False, dir=self.root_dir, prefix="upload-", suffix=".bin") as handle:
            temp_path = Path(handle.name)
            remaining = max(0, int(content_length))
            while remaining > 0:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                handle.write(chunk)
                hasher.update(chunk)
                written += len(chunk)
                remaining -= len(chunk)

        if written <= 0:
            temp_path.unlink(missing_ok=True)
            raise ValueError("Upload body was empty.")

        return self._copy_or_reuse_asset(
            temp_path=temp_path,
            original_filename=safe_name,
            file_size=written,
            sha256_hex=hasher.hexdigest(),
            source_path=None,
        )

    def register_existing_file(
        self,
        source_path: Path,
        original_filename: str | None = None,
        *,
        source_type: str | None = None,
        source_key: str | None = None,
        source_url: str | None = None,
        source_thumbnail_url: str | None = None,
    ) -> dict[str, Any]:
        source = source_path.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(str(source))

        safe_name = sanitize_filename(original_filename or source.name)
        hasher = hashlib.sha256()
        file_size = 0
        with source.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
                file_size += len(chunk)

        sha256_hex = hasher.hexdigest()
        existing = self._fetch_asset_by_sha(sha256_hex)
        if existing:
            return existing

        temp_copy = self.root_dir / ("copy-" + sha256_hex + guess_extension(safe_name))
        shutil.copy2(source, temp_copy)
        return self._copy_or_reuse_asset(
            temp_path=temp_copy,
            original_filename=safe_name,
            file_size=file_size,
            sha256_hex=sha256_hex,
            source_path=str(source),
            source_type=source_type,
            source_key=source_key,
            source_url=source_url,
            source_thumbnail_url=source_thumbnail_url,
        )

    def register_source_reference(
        self,
        *,
        original_filename: str,
        source_type: str,
        source_key: str | None = None,
        source_url: str | None = None,
        source_thumbnail_url: str | None = None,
    ) -> dict[str, Any]:
        clean_source_type = str(source_type or "").strip().lower()
        clean_source_key = str(source_key or "").strip() or None
        clean_source_url = str(source_url or "").strip() or None
        clean_source_thumbnail_url = str(source_thumbnail_url or "").strip() or None
        safe_name = sanitize_filename(original_filename or "remote-video.mp4")

        if not clean_source_type:
            raise ValueError("sourceType is required for source-backed videos.")
        if clean_source_type == "youtube" and clean_source_key and not clean_source_thumbnail_url:
            clean_source_thumbnail_url = youtube_thumbnail_url(clean_source_key)
        if not clean_source_key and not clean_source_url:
            raise ValueError("sourceKey or sourceUrl is required for source-backed videos.")

        existing = self.find_asset_by_source(clean_source_type, clean_source_key or "")
        if existing:
            return existing

        digest = hashlib.sha256(
            "{}:{}".format(clean_source_type, clean_source_key or clean_source_url or safe_name).encode("utf-8")
        ).hexdigest()
        storage_name = "external-{}-{}.mp4".format(clean_source_type, digest)

        with self._write_lock:
            existing = self.find_asset_by_source(clean_source_type, clean_source_key or "")
            if existing:
                return existing
            try:
                return self._insert_asset(
                    storage_name=storage_name,
                    original_filename=safe_name,
                    file_size=0,
                    sha256_hex=digest,
                    source_path=None,
                    source_type=clean_source_type,
                    source_key=clean_source_key,
                    source_url=clean_source_url,
                    source_thumbnail_url=clean_source_thumbnail_url,
                )
            except sqlite3.IntegrityError:
                existing = self.find_asset_by_source(clean_source_type, clean_source_key or "")
                if existing:
                    return existing
                existing = self._fetch_asset_by_sha(digest)
                if existing:
                    return existing
                raise

    def get_asset(self, asset_id: int) -> dict[str, Any] | None:
        return self._ensure_asset_media_metadata(self._fetch_asset_by_id(int(asset_id)))

    def get_asset_path(self, asset_id: int) -> Path | None:
        asset = self._fetch_asset_by_id(asset_id)
        if not asset:
            return None
        path = (self.video_dir / asset["storageName"]).resolve()
        return path if path.is_file() else None

    def get_thumbnail_path(self, asset_id: int) -> Path | None:
        asset = self._fetch_asset_by_id(asset_id)
        if not asset:
            return None
        thumbnail_name = str(asset.get("thumbnailStorageName") or "").strip()
        if not thumbnail_name:
            return None
        path = (self.thumbnail_dir / thumbnail_name).resolve()
        return path if path.is_file() else None

    def find_asset_by_source(self, source_type: str, source_key: str) -> dict[str, Any] | None:
        if not source_type or not source_key:
            return None
        asset = self._fetch_asset_by_source(source_type, source_key)
        return self._ensure_asset_media_metadata(asset)

    def create_entry(self, annotator: str, video_ref: int, annotation: dict[str, Any]) -> dict[str, Any]:
        clean_annotator = str(annotator or "").strip()
        if not clean_annotator:
            raise ValueError("Annotator name is required.")

        asset = self.get_asset(int(video_ref))
        if not asset:
            raise ValueError("Unknown videoRef.")

        normalized_annotation = json.loads(json.dumps(annotation if isinstance(annotation, dict) else {}))
        notes = annotation.get("notes") if isinstance(annotation, dict) else None
        characters = annotation.get("characters") if isinstance(annotation, dict) else None
        video_meta = normalized_annotation.get("video") if isinstance(normalized_annotation, dict) else None

        note_count = len(notes) if isinstance(notes, list) else 0
        character_count = len(characters) if isinstance(characters, list) else 0
        video_title = ""
        video_file_path = None
        if isinstance(video_meta, dict):
            title_value = str(video_meta.get("title") or "").strip()
            file_path_value = str(video_meta.get("filePath") or "").strip()
            video_title = title_value or sanitize_filename(asset["originalFilename"])
            video_file_path = file_path_value or None
            source_type = str(asset.get("sourceType") or "").strip().lower()
            if source_type == "youtube":
                video_meta["filePath"] = None
                video_meta["sourceType"] = asset.get("sourceType")
                video_meta["sourceKey"] = asset.get("sourceKey")
                video_meta["sourceUrl"] = asset.get("sourceUrl")
                video_meta["sourceThumbnailUrl"] = asset.get("sourceThumbnailUrl")
                video_file_path = None
        else:
            video_title = sanitize_filename(asset["originalFilename"])

        timestamp = utc_now_iso()
        annotation_json = json.dumps(normalized_annotation, ensure_ascii=False)

        with self._connect() as connection:
            existing_rows = connection.execute(
                """
                SELECT id, created_at
                FROM annotation_entries
                WHERE video_asset_id = ? AND lower(trim(annotator)) = lower(trim(?))
                ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
                """,
                (int(video_ref), clean_annotator),
            ).fetchall()

            if existing_rows:
                existing = existing_rows[0]
                entry_id = int(existing["id"])
                created_at = str(existing["created_at"])
                duplicate_ids = [int(row["id"]) for row in existing_rows[1:]]
                if duplicate_ids:
                    placeholders = ", ".join("?" for _ in duplicate_ids)
                    connection.execute(
                        "DELETE FROM annotation_entries WHERE id IN ({})".format(placeholders),
                        tuple(duplicate_ids),
                    )
                connection.execute(
                    """
                    UPDATE annotation_entries
                    SET
                        annotator = ?,
                        video_title = ?,
                        video_file_path = ?,
                        annotation_json = ?,
                        note_count = ?,
                        character_count = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        clean_annotator,
                        video_title,
                        video_file_path,
                        annotation_json,
                        note_count,
                        character_count,
                        timestamp,
                        entry_id,
                    ),
                )
                updated_at = timestamp
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO annotation_entries (
                        video_asset_id,
                        annotator,
                        video_title,
                        video_file_path,
                        annotation_json,
                        note_count,
                        character_count,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(video_ref),
                        clean_annotator,
                        video_title,
                        video_file_path,
                        annotation_json,
                        note_count,
                        character_count,
                        timestamp,
                        timestamp,
                    ),
                )
                entry_id = int(cursor.lastrowid)
                created_at = timestamp
                updated_at = timestamp

        return {
            "id": entry_id,
            "videoRef": int(video_ref),
            "annotator": clean_annotator,
            "videoTitle": video_title,
            "videoFilePath": video_file_path,
            "videoOriginalFilename": asset["originalFilename"],
            "noteCount": note_count,
            "characterCount": character_count,
            "createdAt": created_at,
            "updatedAt": updated_at,
        }

    def list_entries(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    e.id,
                    e.annotator,
                    e.video_title,
                    e.video_file_path,
                    e.note_count,
                    e.character_count,
                    e.created_at,
                    COALESCE(e.updated_at, e.created_at) AS updated_at,
                    v.id AS video_ref,
                    v.original_filename,
                    v.thumbnail_storage_name,
                    v.source_type,
                    v.source_key,
                    v.source_url,
                    v.source_thumbnail_url
                FROM annotation_entries AS e
                JOIN video_assets AS v
                  ON v.id = e.video_asset_id
                ORDER BY COALESCE(e.updated_at, e.created_at) DESC, e.id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()

        return [
            {
                "id": int(row["id"]),
                "videoRef": int(row["video_ref"]),
                "annotator": str(row["annotator"]),
                "videoTitle": str(row["video_title"]),
                "videoFilePath": row["video_file_path"],
                "videoOriginalFilename": str(row["original_filename"]),
                "noteCount": int(row["note_count"]),
                "characterCount": int(row["character_count"]),
                "createdAt": str(row["created_at"]),
                "updatedAt": str(row["updated_at"]),
                "thumbnailStorageName": row["thumbnail_storage_name"],
                "sourceType": row["source_type"],
                "sourceKey": row["source_key"],
                "sourceUrl": row["source_url"],
                "sourceThumbnailUrl": row["source_thumbnail_url"],
            }
            for row in rows
        ]

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    e.id,
                    e.annotator,
                    e.video_title,
                    e.video_file_path,
                    e.annotation_json,
                    e.note_count,
                    e.character_count,
                    e.created_at,
                    COALESCE(e.updated_at, e.created_at) AS updated_at,
                    v.id AS video_ref,
                    v.original_filename,
                    v.thumbnail_storage_name,
                    v.source_type,
                    v.source_key,
                    v.source_url,
                    v.source_thumbnail_url
                FROM annotation_entries AS e
                JOIN video_assets AS v
                  ON v.id = e.video_asset_id
                WHERE e.id = ?
                """,
                (int(entry_id),),
            ).fetchone()

        if not row:
            return None

        try:
            annotation = json.loads(str(row["annotation_json"]))
        except json.JSONDecodeError:
            annotation = {}

        return {
            "id": int(row["id"]),
            "videoRef": int(row["video_ref"]),
            "annotator": str(row["annotator"]),
            "videoTitle": str(row["video_title"]),
            "videoFilePath": row["video_file_path"],
            "videoOriginalFilename": str(row["original_filename"]),
            "noteCount": int(row["note_count"]),
            "characterCount": int(row["character_count"]),
            "createdAt": str(row["created_at"]),
            "updatedAt": str(row["updated_at"]),
            "thumbnailStorageName": row["thumbnail_storage_name"],
            "sourceType": row["source_type"],
            "sourceKey": row["source_key"],
            "sourceUrl": row["source_url"],
            "sourceThumbnailUrl": row["source_thumbnail_url"],
            "annotation": annotation,
        }

    def get_video_group(self, video_ref: int) -> dict[str, Any] | None:
        asset = self._fetch_asset_by_id(int(video_ref))
        if not asset:
            return None

        asset = self._ensure_asset_media_metadata(asset)
        if asset is None:
            return None
        entries = self._latest_unique_entries(
            [item for item in self.list_entries(limit=1000) if int(item["videoRef"]) == int(video_ref)]
        )
        latest_title = ""
        latest_activity = asset["createdAt"]
        if entries:
            latest_entry = max(entries, key=lambda item: (str(item.get("updatedAt") or item["createdAt"]), int(item["id"])))
            latest_title = str(latest_entry.get("videoTitle") or "").strip()
            latest_activity = str(latest_entry.get("updatedAt") or latest_entry["createdAt"])

        return {
            "videoRef": int(asset["id"]),
            "videoTitle": latest_title or sanitize_filename(asset["originalFilename"]),
            "videoOriginalFilename": asset["originalFilename"],
            "createdAt": asset["createdAt"],
            "updatedAt": latest_activity,
            "sourceType": asset.get("sourceType"),
            "sourceKey": asset.get("sourceKey"),
            "sourceUrl": asset.get("sourceUrl"),
            "sourceThumbnailUrl": asset.get("sourceThumbnailUrl"),
            "thumbnailStorageName": asset.get("thumbnailStorageName"),
            "annotations": entries,
        }

    def list_video_groups(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        with self._connect() as connection:
            video_rows = connection.execute(
                """
                SELECT
                    v.id AS video_ref,
                    v.original_filename,
                    v.created_at AS asset_created_at,
                    v.thumbnail_storage_name,
                    v.source_type,
                    v.source_key,
                    v.source_url,
                    v.source_thumbnail_url,
                    COALESCE(MAX(e.video_title), v.original_filename) AS video_title,
                    COALESCE(MAX(COALESCE(e.updated_at, e.created_at)), v.created_at) AS latest_activity_at,
                    COUNT(e.id) AS reviewer_count
                FROM video_assets AS v
                LEFT JOIN annotation_entries AS e
                  ON e.video_asset_id = v.id
                GROUP BY
                    v.id,
                    v.original_filename,
                    v.created_at,
                    v.thumbnail_storage_name,
                    v.source_type,
                    v.source_key,
                    v.source_url,
                    v.source_thumbnail_url
                HAVING COUNT(e.id) > 0
                ORDER BY latest_activity_at DESC, v.id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()

        video_refs = [int(row["video_ref"]) for row in video_rows]
        if not video_refs:
            return []

        placeholders = ", ".join("?" for _ in video_refs)
        with self._connect() as connection:
            entry_rows = connection.execute(
                """
                SELECT
                    e.id,
                    e.video_asset_id AS video_ref,
                    e.annotator,
                    e.video_title,
                    e.video_file_path,
                    e.note_count,
                    e.character_count,
                    e.created_at,
                    COALESCE(e.updated_at, e.created_at) AS updated_at,
                    v.original_filename,
                    v.thumbnail_storage_name,
                    v.source_type,
                    v.source_key,
                    v.source_url,
                    v.source_thumbnail_url
                FROM annotation_entries AS e
                JOIN video_assets AS v
                  ON v.id = e.video_asset_id
                WHERE e.video_asset_id IN ({})
                ORDER BY COALESCE(e.updated_at, e.created_at) DESC, e.id DESC
                """.format(placeholders),
                tuple(video_refs),
            ).fetchall()

        entries_by_video: dict[int, list[dict[str, Any]]] = {}
        for row in entry_rows:
            video_ref = int(row["video_ref"])
            entries_by_video.setdefault(video_ref, []).append(
                {
                    "id": int(row["id"]),
                    "videoRef": video_ref,
                    "annotator": str(row["annotator"]),
                    "videoTitle": str(row["video_title"]),
                    "videoFilePath": row["video_file_path"],
                    "videoOriginalFilename": str(row["original_filename"]),
                    "noteCount": int(row["note_count"]),
                    "characterCount": int(row["character_count"]),
                    "createdAt": str(row["created_at"]),
                    "updatedAt": str(row["updated_at"]),
                    "thumbnailStorageName": row["thumbnail_storage_name"],
                    "sourceType": row["source_type"],
                    "sourceKey": row["source_key"],
                    "sourceUrl": row["source_url"],
                    "sourceThumbnailUrl": row["source_thumbnail_url"],
                }
            )

        entries_by_video = {
            video_ref: self._latest_unique_entries(entries)
            for video_ref, entries in entries_by_video.items()
        }

        groups = []
        for row in video_rows:
            video_ref = int(row["video_ref"])
            asset = self._ensure_asset_media_metadata(self._fetch_asset_by_id(video_ref))
            if asset is None:
                continue
            video_entries = entries_by_video.get(video_ref, [])
            latest_title = str(row["video_title"])
            if video_entries:
                latest_title = str(video_entries[0].get("videoTitle") or "").strip() or latest_title
            groups.append(
                {
                    "videoRef": video_ref,
                    "videoTitle": latest_title,
                    "videoOriginalFilename": str(asset["originalFilename"]),
                    "createdAt": str(asset["createdAt"]),
                    "updatedAt": str(video_entries[0]["updatedAt"]) if video_entries else str(row["latest_activity_at"]),
                    "reviewerCount": len(video_entries),
                    "thumbnailStorageName": asset.get("thumbnailStorageName"),
                    "sourceType": asset.get("sourceType"),
                    "sourceKey": asset.get("sourceKey"),
                    "sourceUrl": asset.get("sourceUrl"),
                    "sourceThumbnailUrl": asset.get("sourceThumbnailUrl"),
                    "annotations": video_entries,
                }
            )

        return groups


def latest_mp4(output_dir: Path, started_at: float) -> Path | None:
    candidates = [path for path in output_dir.glob("*.mp4") if path.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        if path.stat().st_mtime >= started_at - 1.0:
            return path
    return candidates[0]


def cached_source_video_path(
    output_dir: Path,
    *,
    source_type: str,
    source_key: str | None = None,
    source_url: str | None = None,
    original_filename: str | None = None,
) -> Path:
    source_label = str(source_type or "remote").strip().lower() or "remote"
    stable_key = str(source_key or source_url or original_filename or "video").strip()
    digest = hashlib.sha256("{}:{}".format(source_label, stable_key).encode("utf-8")).hexdigest()
    extension = guess_extension(str(original_filename or "").strip(), fallback=".mp4")
    return output_dir / ("source-{}-{}{}".format(source_label, digest, extension))


class YtBridgeHandler(BaseHTTPRequestHandler):
    output_dir: Path = Path(".").resolve()
    shared_store: SharedLibraryStore | None = None

    def _base_url(self) -> str:
        host = self.headers.get("Host") or "{}:{}".format(*self.server.server_address)
        return "http://" + host

    def _allow_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Range, X-Filename")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges")

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self._allow_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_no_content(self) -> None:
        self.send_response(204)
        self._allow_cors()
        self.end_headers()

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        raw_body = self.rfile.read(content_length)
        try:
            return json.loads(raw_body.decode("utf-8") or "{}")
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Body must be valid JSON.") from exc

    def _read_auth_token(self) -> str:
        authorization = str(self.headers.get("Authorization") or "").strip()
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return str(self.headers.get("X-Auth-Token") or "").strip()

    def _require_authenticated_account(self) -> dict[str, Any] | None:
        token = self._read_auth_token()
        if not token:
            self._send_json(401, {"ok": False, "error": "Login required."})
            return None
        account = self._shared_store().get_account_for_token(token)
        if not account:
            self._send_json(401, {"ok": False, "error": "Session expired or invalid. Please log in again."})
            return None
        return account

    @staticmethod
    def _normalize_owned_annotation(annotation: dict[str, Any], annotator: str) -> dict[str, Any]:
        normalized = json.loads(json.dumps(annotation))
        normalized["annotator"] = str(annotator or "").strip()
        notes = normalized.get("notes")
        if isinstance(notes, list):
            for note in notes:
                if isinstance(note, dict):
                    note["annotator"] = str(annotator or "").strip()
        return normalized

    def _shared_store(self) -> SharedLibraryStore:
        if self.shared_store is None:
            raise RuntimeError("Shared library store is not configured.")
        return self.shared_store

    def _resolve_output_video_path(self, encoded_name: str) -> Path | None:
        raw_name = Path(unquote(str(encoded_name or ""))).name
        if not raw_name:
            return None
        output_root = self.output_dir.resolve()
        candidate = (output_root / raw_name).resolve()
        if candidate.parent != output_root or not candidate.is_file():
            return None
        return candidate

    def _build_import_video_payload(
        self,
        file_path: Path,
        *,
        file_name: str | None = None,
        video_url: str | None = None,
        source_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_path = file_path.expanduser().resolve()
        payload = {
            "filePath": str(resolved_path),
            "fileName": str(file_name or resolved_path.name),
            "videoUrl": str(video_url or (self._base_url() + "/api/yt-mp4/files/" + quote(resolved_path.name, safe=""))),
        }
        if source_info:
            payload.update(
                {
                    "sourceType": source_info.get("sourceType"),
                    "sourceKey": source_info.get("sourceKey"),
                    "sourceUrl": source_info.get("sourceUrl"),
                    "sourceThumbnailUrl": source_info.get("sourceThumbnailUrl"),
                }
            )
        return payload

    def _ensure_source_backed_asset_path(self, asset: dict[str, Any]) -> Path | None:
        source_type = str(asset.get("sourceType") or "").strip().lower()
        source_url = str(asset.get("sourceUrl") or "").strip()
        if source_type != "youtube" or not source_url:
            return None

        cached_path = cached_source_video_path(
            self.output_dir,
            source_type=source_type,
            source_key=str(asset.get("sourceKey") or "").strip() or None,
            source_url=source_url,
            original_filename=str(asset.get("originalFilename") or "").strip() or None,
        ).resolve()
        if cached_path.is_file() and cached_path.stat().st_size > 0:
            return cached_path

        self.output_dir.mkdir(parents=True, exist_ok=True)
        try:
            convert_yt_mp4(source_url, output_template=str(cached_path))
        except SystemExit as exc:
            print("[yt_mp4_bridge] source-backed conversion error:", str(exc))
            return None
        except Exception as exc:  # pragma: no cover
            print("[yt_mp4_bridge] source-backed unexpected error:", repr(exc))
            print(traceback.format_exc())
            return None

        if cached_path.is_file() and cached_path.stat().st_size > 0:
            return cached_path
        return None

    def _resolve_streamable_asset_path(self, asset_id: int) -> Path | None:
        asset = self._shared_store().get_asset(int(asset_id))
        if not asset:
            return None
        asset_path = self._shared_store().get_asset_path(int(asset_id))
        if asset_path:
            return asset_path
        return self._ensure_source_backed_asset_path(asset)

    def _build_asset_payload(self, asset: dict[str, Any]) -> dict[str, Any]:
        video_path = "/api/shared-library/videos/{}".format(asset["id"])
        thumbnail_path = None
        thumbnail_url = None
        if str(asset.get("thumbnailStorageName") or "").strip():
            thumbnail_path = "/api/shared-library/thumbnails/{}".format(asset["id"])
            thumbnail_url = self._base_url() + thumbnail_path
        elif str(asset.get("sourceThumbnailUrl") or "").strip():
            thumbnail_url = str(asset["sourceThumbnailUrl"]).strip()
        return {
            "videoRef": asset["id"],
            "fileName": asset["originalFilename"],
            "fileSize": asset["fileSize"],
            "sha256": asset["sha256"],
            "videoPath": video_path,
            "videoUrl": self._base_url() + video_path,
            "thumbnailPath": thumbnail_path,
            "thumbnailUrl": thumbnail_url,
            "sourceType": asset.get("sourceType"),
            "sourceKey": asset.get("sourceKey"),
            "sourceUrl": asset.get("sourceUrl"),
        }

    def _build_entry_payload(self, entry: dict[str, Any]) -> dict[str, Any]:
        video_path = "/api/shared-library/videos/{}".format(entry["videoRef"])
        thumbnail_path = None
        thumbnail_url = None
        if str(entry.get("thumbnailStorageName") or "").strip():
            thumbnail_path = "/api/shared-library/thumbnails/{}".format(entry["videoRef"])
            thumbnail_url = self._base_url() + thumbnail_path
        elif str(entry.get("sourceThumbnailUrl") or "").strip():
            thumbnail_url = str(entry["sourceThumbnailUrl"]).strip()
        return {
            **entry,
            "videoPath": video_path,
            "videoUrl": self._base_url() + video_path,
            "thumbnailPath": thumbnail_path,
            "thumbnailUrl": thumbnail_url,
        }

    def _build_video_payload(self, video: dict[str, Any]) -> dict[str, Any]:
        video_path = "/api/shared-library/videos/{}".format(video["videoRef"])
        thumbnail_path = None
        thumbnail_url = None
        if str(video.get("thumbnailStorageName") or "").strip():
            thumbnail_path = "/api/shared-library/thumbnails/{}".format(video["videoRef"])
            thumbnail_url = self._base_url() + thumbnail_path
        elif str(video.get("sourceThumbnailUrl") or "").strip():
            thumbnail_url = str(video["sourceThumbnailUrl"]).strip()
        return {
            **video,
            "videoPath": video_path,
            "videoUrl": self._base_url() + video_path,
            "thumbnailPath": thumbnail_path,
            "thumbnailUrl": thumbnail_url,
            "annotations": [self._build_entry_payload(item) for item in video.get("annotations", [])],
        }

    def _serve_file(self, file_path: Path, head_only: bool = False) -> None:
        total_size = file_path.stat().st_size
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        range_header = self.headers.get("Range") or ""
        start = 0
        end = total_size - 1
        status_code = 200

        if range_header:
            try:
                unit, value = range_header.split("=", 1)
                if unit.strip().lower() != "bytes":
                    raise ValueError
                range_value = value.split(",", 1)[0].strip()
                start_str, end_str = range_value.split("-", 1)
                if start_str:
                    start = int(start_str)
                    end = int(end_str) if end_str else total_size - 1
                else:
                    suffix_length = int(end_str)
                    start = max(0, total_size - suffix_length)
                    end = total_size - 1
                if start < 0 or end >= total_size or start > end:
                    raise ValueError
                status_code = 206
            except ValueError:
                self.send_response(416)
                self._allow_cors()
                self.send_header("Content-Range", "bytes */{}".format(total_size))
                self.end_headers()
                return

        content_length = end - start + 1
        self.send_response(status_code)
        self._allow_cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(content_length))
        if status_code == 206:
            self.send_header("Content-Range", "bytes {}-{}/{}".format(start, end, total_size))
        self.end_headers()

        if head_only:
            return

        with file_path.open("rb") as handle:
            handle.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_no_content()

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.startswith("/api/yt-mp4/files/"):
            imported_video_path = self._resolve_output_video_path(path.rsplit("/", 1)[-1])
            if not imported_video_path:
                self.send_error(404, "Imported video not found.")
                return

            self._serve_file(imported_video_path, head_only=True)
            return

        if path.startswith("/api/shared-library/thumbnails/"):
            try:
                asset_id = int(path.rsplit("/", 1)[-1])
            except ValueError:
                self.send_error(400, "videoRef must be an integer.")
                return

            thumbnail_path = self._shared_store().get_thumbnail_path(asset_id)
            if not thumbnail_path:
                self.send_error(404, "Thumbnail not found.")
                return

            self._serve_file(thumbnail_path, head_only=True)
            return

        if path.startswith("/api/shared-library/videos/"):
            try:
                asset_id = int(path.rsplit("/", 1)[-1])
            except ValueError:
                self.send_error(400, "videoRef must be an integer.")
                return

            asset_path = self._resolve_streamable_asset_path(asset_id)
            if not asset_path:
                self.send_error(404, "Video asset not found.")
                return

            self._serve_file(asset_path, head_only=True)
            return

        self.send_error(501, "Unsupported method ('HEAD')")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/favicon.ico":
            self._send_no_content()
            return

        if path in ("", "/api/yt-mp4"):
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "yt_mp4_bridge",
                    "endpoint": "/api/yt-mp4",
                    "method": "POST",
                    "outputDir": str(self.output_dir),
                    "outputVideoPrefix": "/api/yt-mp4/files/<filename>",
                    "sharedLibraryRoot": str(self._shared_store().root_dir),
                },
            )
            return

        if path.startswith("/api/yt-mp4/files/"):
            imported_video_path = self._resolve_output_video_path(path.rsplit("/", 1)[-1])
            if not imported_video_path:
                self._send_json(404, {"ok": False, "error": "Imported video not found."})
                return

            self._serve_file(imported_video_path)
            return

        if path == "/api/shared-library":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "shared_annotation_library",
                    "authRegisterEndpoint": "/api/shared-library/auth/register",
                    "authLoginEndpoint": "/api/shared-library/auth/login",
                    "authMeEndpoint": "/api/shared-library/auth/me",
                    "authLogoutEndpoint": "/api/shared-library/auth/logout",
                    "videosEndpoint": "/api/shared-library/videos",
                    "entriesEndpoint": "/api/shared-library/entries",
                    "videoAssetsEndpoint": "/api/shared-library/video-assets",
                    "videoStreamPrefix": "/api/shared-library/videos/<videoRef>",
                    "thumbnailPrefix": "/api/shared-library/thumbnails/<videoRef>",
                    "sharedLibraryRoot": str(self._shared_store().root_dir),
                },
            )
            return

        if path == "/api/shared-library/auth/me":
            account = self._require_authenticated_account()
            if not account:
                return
            self._send_json(200, {"ok": True, "account": account})
            return

        if path == "/api/shared-library/videos":
            videos = [self._build_video_payload(item) for item in self._shared_store().list_video_groups()]
            self._send_json(200, {"ok": True, "videos": videos})
            return

        if path == "/api/shared-library/entries":
            entries = [self._build_entry_payload(item) for item in self._shared_store().list_entries()]
            self._send_json(200, {"ok": True, "entries": entries})
            return

        if path.startswith("/api/shared-library/entries/"):
            try:
                entry_id = int(path.rsplit("/", 1)[-1])
            except ValueError:
                self._send_json(400, {"ok": False, "error": "Entry id must be an integer."})
                return

            entry = self._shared_store().get_entry(entry_id)
            if not entry:
                self._send_json(404, {"ok": False, "error": "Shared entry not found."})
                return

            self._send_json(200, {"ok": True, "entry": self._build_entry_payload(entry)})
            return

        if path.startswith("/api/shared-library/thumbnails/"):
            try:
                asset_id = int(path.rsplit("/", 1)[-1])
            except ValueError:
                self._send_json(400, {"ok": False, "error": "videoRef must be an integer."})
                return

            thumbnail_path = self._shared_store().get_thumbnail_path(asset_id)
            if not thumbnail_path:
                self._send_json(404, {"ok": False, "error": "Thumbnail not found."})
                return

            self._serve_file(thumbnail_path)
            return

        if path.startswith("/api/shared-library/videos/"):
            try:
                asset_id = int(path.rsplit("/", 1)[-1])
            except ValueError:
                self._send_json(400, {"ok": False, "error": "videoRef must be an integer."})
                return

            asset_path = self._resolve_streamable_asset_path(asset_id)
            if not asset_path:
                self._send_json(404, {"ok": False, "error": "Video asset not found."})
                return

            self._serve_file(asset_path)
            return

        self._send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/yt-mp4":
            self._handle_yt_import()
            return

        if path == "/api/shared-library/video-assets":
            self._handle_video_upload()
            return

        if path == "/api/shared-library/register-local-video":
            self._handle_register_local_video()
            return

        if path == "/api/shared-library/auth/register":
            self._handle_auth_register()
            return

        if path == "/api/shared-library/auth/login":
            self._handle_auth_login()
            return

        if path == "/api/shared-library/auth/logout":
            self._handle_auth_logout()
            return

        if path == "/api/shared-library/entries":
            self._handle_create_entry()
            return

        self._send_json(404, {"ok": False, "error": "Not found"})

    def _handle_auth_register(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        username = str(payload.get("username") or payload.get("annotator") or "").strip()
        password = str(payload.get("password") or "")
        try:
            account, token = self._shared_store().register_account(username, password)
        except ValueError as exc:
            message = str(exc)
            status_code = 409 if "already exists" in message.lower() else 400
            self._send_json(status_code, {"ok": False, "error": message})
            return
        except Exception as exc:  # pragma: no cover
            self._send_json(500, {"ok": False, "error": "Could not register annotator: " + str(exc)})
            return

        self._send_json(200, {"ok": True, "account": account, "token": token})

    def _handle_auth_login(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        username = str(payload.get("username") or payload.get("annotator") or "").strip()
        password = str(payload.get("password") or "")
        try:
            account, token = self._shared_store().login_account(username, password)
        except ValueError as exc:
            self._send_json(401, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:  # pragma: no cover
            self._send_json(500, {"ok": False, "error": "Could not log in: " + str(exc)})
            return

        self._send_json(200, {"ok": True, "account": account, "token": token})

    def _handle_auth_logout(self) -> None:
        token = self._read_auth_token()
        if not token:
            self._send_json(200, {"ok": True, "loggedOut": False})
            return
        revoked = self._shared_store().revoke_session(token)
        self._send_json(200, {"ok": True, "loggedOut": revoked})

    def _handle_yt_import(self) -> None:
        account = self._require_authenticated_account()
        if not account:
            return
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        link = str(payload.get("url") or payload.get("link") or "").strip()
        if not link:
            self._send_json(400, {"ok": False, "error": "Missing 'url' in request body."})
            return

        source_info = youtube_source_from_url(link)
        if source_info:
            existing_asset = self._shared_store().find_asset_by_source(
                source_info["sourceType"],
                source_info["sourceKey"],
            )
            if existing_asset:
                duplicate_video = self._shared_store().get_video_group(existing_asset["id"])
                if duplicate_video and duplicate_video.get("annotations"):
                    self._send_json(
                        409,
                        {
                            "ok": False,
                            "error": "This YouTube video already exists in the shared library.",
                            "duplicate": self._build_video_payload(duplicate_video),
                        },
                    )
                    return

                asset_path = self._resolve_streamable_asset_path(existing_asset["id"])
                if asset_path:
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "reusedExisting": True,
                            **self._build_import_video_payload(
                                asset_path,
                                file_name=str(existing_asset.get("originalFilename") or asset_path.name),
                                video_url=self._base_url() + "/api/shared-library/videos/{}".format(existing_asset["id"]),
                                source_info=existing_asset,
                            ),
                        },
                    )
                    return

                self._send_json(
                    200,
                    {
                        "ok": True,
                        "reusedExisting": True,
                        "filePath": str(existing_asset.get("sourcePath") or ""),
                        "fileName": str(existing_asset.get("originalFilename") or "imported-video.mp4"),
                        "sourceType": existing_asset.get("sourceType"),
                        "sourceKey": existing_asset.get("sourceKey"),
                        "sourceUrl": existing_asset.get("sourceUrl"),
                        "sourceThumbnailUrl": existing_asset.get("sourceThumbnailUrl"),
                    },
                )
                return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        started_at = time.time()
        output_template = str((self.output_dir / "%(title)s.%(ext)s").resolve())

        try:
            convert_yt_mp4(link, output_template=output_template)
        except SystemExit as exc:
            print("[yt_mp4_bridge] conversion error:", str(exc))
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:  # pragma: no cover
            print("[yt_mp4_bridge] unexpected error:", repr(exc))
            print(traceback.format_exc())
            self._send_json(500, {"ok": False, "error": str(exc)})
            return

        video_path = latest_mp4(self.output_dir, started_at)
        if video_path is None:
            self._send_json(500, {"ok": False, "error": "Conversion finished but no mp4 was found."})
            return

        self._send_json(
            200,
            {
                "ok": True,
                **self._build_import_video_payload(video_path, source_info=source_info),
            },
        )

    def _handle_video_upload(self) -> None:
        account = self._require_authenticated_account()
        if not account:
            return
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        if content_length <= 0:
            self._send_json(400, {"ok": False, "error": "Upload body is empty."})
            return

        filename_header = self.headers.get("X-Filename") or "upload.mp4"
        filename = sanitize_filename(filename_header)

        try:
            asset = self._shared_store().save_upload(
                stream=self.rfile,
                content_length=content_length,
                original_filename=filename,
            )
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:  # pragma: no cover
            self._send_json(500, {"ok": False, "error": "Upload failed: " + str(exc)})
            return

        self._send_json(200, {"ok": True, **self._build_asset_payload(asset)})

    def _handle_create_entry(self) -> None:
        account = self._require_authenticated_account()
        if not account:
            return
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        annotator = str(account["username"]).strip()
        video_ref_raw = payload.get("videoRef")
        annotation = payload.get("annotation")

        try:
            video_ref = int(video_ref_raw)
        except (TypeError, ValueError):
            self._send_json(400, {"ok": False, "error": "videoRef must be an integer."})
            return

        if not isinstance(annotation, dict):
            self._send_json(400, {"ok": False, "error": "annotation must be an object."})
            return

        try:
            entry = self._shared_store().create_entry(
                annotator=annotator,
                video_ref=video_ref,
                annotation=self._normalize_owned_annotation(annotation, annotator),
            )
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:  # pragma: no cover
            self._send_json(500, {"ok": False, "error": "Could not create entry: " + str(exc)})
            return

        self._send_json(200, {"ok": True, "entry": self._build_entry_payload(entry)})

    def _handle_register_local_video(self) -> None:
        account = self._require_authenticated_account()
        if not account:
            return
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        raw_path = str(payload.get("filePath") or "").strip()
        if not raw_path:
            self._send_json(400, {"ok": False, "error": "filePath is required."})
            return

        normalized_path = raw_path
        if normalized_path.startswith("file://"):
            normalized_path = normalized_path[7:]
        source_path = Path(normalized_path).expanduser().resolve(strict=False)
        if not source_path.is_file():
            self._send_json(400, {"ok": False, "error": "Local video path does not exist on the server machine."})
            return

        source_type = str(payload.get("sourceType") or "").strip() or None
        source_key = str(payload.get("sourceKey") or "").strip() or None
        source_url = str(payload.get("sourceUrl") or "").strip() or None
        source_thumbnail_url = str(payload.get("sourceThumbnailUrl") or "").strip() or None

        try:
            if source_type == "youtube" and (source_key or source_url):
                asset = self._shared_store().register_source_reference(
                    original_filename=source_path.name,
                    source_type=source_type,
                    source_key=source_key,
                    source_url=source_url,
                    source_thumbnail_url=source_thumbnail_url,
                )
            else:
                asset = self._shared_store().register_existing_file(
                    source_path,
                    original_filename=source_path.name,
                    source_type=source_type,
                    source_key=source_key,
                    source_url=source_url,
                    source_thumbnail_url=source_thumbnail_url,
                )
        except FileNotFoundError:
            self._send_json(400, {"ok": False, "error": "Local video path does not exist on the server machine."})
            return
        except Exception as exc:  # pragma: no cover
            self._send_json(500, {"ok": False, "error": "Could not register local video: " + str(exc)})
            return

        self._send_json(200, {"ok": True, **self._build_asset_payload(asset)})

    def log_message(self, fmt: str, *args) -> None:
        sys.stdout.write("[yt_mp4_bridge] " + (fmt % args) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Shared annotator server for YouTube import and shared video review.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory to save downloaded and cached mp4 files (default: preprocessing/ next to this script).",
    )
    parser.add_argument(
        "--shared-root",
        default=str(DEFAULT_SHARED_ROOT),
        help="Directory for the SQLite database and hosted video assets (default: repo-root shared_library next to preprocessing/).",
    )
    args = parser.parse_args()

    YtBridgeHandler.output_dir = Path(args.output_dir).expanduser().resolve()
    YtBridgeHandler.shared_store = SharedLibraryStore(Path(args.shared_root))

    legacy_shared_root = (SCRIPT_DIR / "shared_library").resolve()
    if legacy_shared_root.exists() and legacy_shared_root != YtBridgeHandler.shared_store.root_dir:
        print(
            "note: found legacy shared library at {} but using {}".format(
                legacy_shared_root,
                YtBridgeHandler.shared_store.root_dir,
            )
        )

    server = ThreadingHTTPServer((args.host, args.port), YtBridgeHandler)
    print(
        "annotator server running at http://{}:{} | yt import: /api/yt-mp4 | shared library: {}".format(
            args.host,
            args.port,
            YtBridgeHandler.shared_store.root_dir,
        )
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down bridge...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
