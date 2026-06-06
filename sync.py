import os
import sqlite3
import threading
import time
import json
import psycopg2
from psycopg2.extras import execute_values

SYNC_INTERVAL = 30  # seconds

# PostgreSQL schema (mirrors SQLite, minus Settings which is local-only)
PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    file VARCHAR(255) PRIMARY KEY,
    downloaded_link VARCHAR(255),
    title VARCHAR(255) NOT NULL,
    date_download TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    artist VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS playlists (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS song_playlist (
    id SERIAL PRIMARY KEY,
    song_file VARCHAR(255) NOT NULL,
    playlist_id BIGINT NOT NULL,
    date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lyrics (
    id SERIAL PRIMARY KEY,
    song_file VARCHAR(255) NOT NULL,
    lyrics TEXT
);

CREATE TABLE IF NOT EXISTS music_history (
    id SERIAL PRIMARY KEY,
    song_file VARCHAR(255) NOT NULL,
    date_played TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS playlist_history (
    id SERIAL PRIMARY KEY,
    playlist_id BIGINT NOT NULL,
    date_played TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class DatabaseSync:
    def __init__(self, sqlite_path, pg_url):
        self.sqlite_path = sqlite_path
        self.pg_url = pg_url
        self._stop_event = threading.Event()
        self._thread = None
        self._pg_conn = None

    def _get_pg_conn(self):
        """Get or reconnect the PostgreSQL connection."""
        if self._pg_conn is None or self._pg_conn.closed:
            self._pg_conn = psycopg2.connect(self.pg_url)
        return self._pg_conn

    def _init_pg_schema(self):
        """Create PostgreSQL tables if they don't exist."""
        try:
            conn = self._get_pg_conn()
            with conn.cursor() as cur:
                cur.execute(PG_SCHEMA)
            conn.commit()
            print(" [Sync] PostgreSQL schema initialized.")
        except Exception as e:
            print(f" [Sync] Failed to init PG schema: {e}")

    def _sync_songs(self, sqlite_conn, pg_conn):
        """Upsert all songs from SQLite → PostgreSQL."""
        rows = sqlite_conn.execute("SELECT file, downloaded_link, title, date_download, artist FROM Songs").fetchall()
        if not rows:
            return
        with pg_conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO songs (file, downloaded_link, title, date_download, artist)
                VALUES %s
                ON CONFLICT (file) DO UPDATE SET
                    downloaded_link = EXCLUDED.downloaded_link,
                    title = EXCLUDED.title,
                    date_download = EXCLUDED.date_download,
                    artist = EXCLUDED.artist
                """,
                rows
            )
            pg_conn.commit()

    def _sync_playlists(self, sqlite_conn, pg_conn):
        """Upsert all playlists (excluding thumbnail blob)."""
        rows = sqlite_conn.execute("SELECT id, title, description FROM Playlists").fetchall()
        if not rows:
            return
        with pg_conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    """
                    INSERT INTO playlists (id, title, description)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        description = EXCLUDED.description
                    """,
                    row
                )
            # Keep the serial sequence in sync with explicit IDs
            cur.execute("SELECT setval('playlists_id_seq', COALESCE((SELECT MAX(id) FROM playlists), 1), true)")
            pg_conn.commit()

    def _sync_song_playlist(self, sqlite_conn, pg_conn):
        """Full replace of song-playlist associations."""
        rows = sqlite_conn.execute("SELECT id, song_file, playlist_id, date_added FROM Song_Playlist").fetchall()
        with pg_conn.cursor() as cur:
            cur.execute("DELETE FROM song_playlist")
            if rows:
                execute_values(
                    cur,
                    "INSERT INTO song_playlist (id, song_file, playlist_id, date_added) VALUES %s",
                    rows
                )
                cur.execute("SELECT setval('song_playlist_id_seq', COALESCE((SELECT MAX(id) FROM song_playlist), 1), true)")
            pg_conn.commit()

    def _sync_lyrics(self, sqlite_conn, pg_conn):
        """Upsert lyrics."""
        rows = sqlite_conn.execute("SELECT id, song_file, lyrics FROM Lyrics").fetchall()
        if not rows:
            return
        with pg_conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    """
                    INSERT INTO lyrics (id, song_file, lyrics)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        song_file = EXCLUDED.song_file,
                        lyrics = EXCLUDED.lyrics
                    """,
                    row
                )
            cur.execute("SELECT setval('lyrics_id_seq', COALESCE((SELECT MAX(id) FROM lyrics), 1), true)")
            pg_conn.commit()

    def _sync_history(self, sqlite_conn, pg_conn, table_sqlite, table_pg, columns):
        """Incremental sync for append-only history tables using max ID."""
        with pg_conn.cursor() as cur:
            cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table_pg}")
            last_synced_id = cur.fetchone()[0]

        col_list = ", ".join(columns)
        rows = sqlite_conn.execute(
            f"SELECT {col_list} FROM {table_sqlite} WHERE id > ? ORDER BY id",
            (last_synced_id,)
        ).fetchall()

        if not rows:
            return

        with pg_conn.cursor() as cur:
            placeholders = ", ".join(["%s"] * len(columns))
            execute_values(
                cur,
                f"INSERT INTO {table_pg} ({col_list}) VALUES %s ON CONFLICT (id) DO NOTHING",
                rows
            )
            cur.execute(f"SELECT setval('{table_pg}_id_seq', COALESCE((SELECT MAX(id) FROM {table_pg}), 1), true)")
            pg_conn.commit()

    def _run_sync(self):
        """Execute a full sync cycle."""
        try:
            sqlite_conn = sqlite3.connect(self.sqlite_path)
            pg_conn = self._get_pg_conn()

            self._sync_songs(sqlite_conn, pg_conn)
            self._sync_playlists(sqlite_conn, pg_conn)
            self._sync_song_playlist(sqlite_conn, pg_conn)
            self._sync_lyrics(sqlite_conn, pg_conn)
            self._sync_history(
                sqlite_conn, pg_conn,
                "Music_History", "music_history",
                ["id", "song_file", "date_played"]
            )
            self._sync_history(
                sqlite_conn, pg_conn,
                "Playlist_History", "playlist_history",
                ["id", "playlist_id", "date_played"]
            )

            sqlite_conn.close()
        except Exception as e:
            print(f" [Sync] Sync error: {e}")
            # Reset connection on failure so it reconnects next cycle
            try:
                if self._pg_conn and not self._pg_conn.closed:
                    self._pg_conn.close()
            except Exception:
                pass
            self._pg_conn = None

    def _loop(self):
        """Background loop that runs sync every SYNC_INTERVAL seconds."""
        # Wait a few seconds on startup for the app to settle
        time.sleep(5)

        while not self._stop_event.is_set():
            self._run_sync()
            self._stop_event.wait(SYNC_INTERVAL)

        # Final sync before shutdown
        try:
            self._run_sync()
        except Exception:
            pass

        if self._pg_conn and not self._pg_conn.closed:
            self._pg_conn.close()
        print(" [Sync] Sync thread stopped.")

    def start(self):
        """Initialize PG schema and start the background sync thread."""
        try:
            self._init_pg_schema()
        except Exception as e:
            print(f" [Sync] Could not initialize PG, sync disabled: {e}")
            return

        self._thread = threading.Thread(target=self._loop, daemon=True, name="db-sync")
        self._thread.start()
        print(f" [Sync] Background sync started (every {SYNC_INTERVAL}s).")

    def stop(self):
        """Signal the sync thread to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
