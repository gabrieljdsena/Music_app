import os
import sqlite3
import threading
import time

import pymysql

SYNC_INTERVAL = 30  # seconds

# MySQL/TiDB schema (mirrors SQLite, minus Settings which is local-only)
REMOTE_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS songs (
        file VARCHAR(255) PRIMARY KEY,
        downloaded_link VARCHAR(255),
        title VARCHAR(255) NOT NULL,
        date_download TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        artist VARCHAR(255)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS playlists (
        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        description TEXT,
        thumbnail TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS song_playlist (
        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        song_file VARCHAR(255) NOT NULL,
        playlist_id BIGINT NOT NULL,
        date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lyrics (
        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        song_file VARCHAR(255) NOT NULL,
        lyrics TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS music_history (
        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        song_file VARCHAR(255) NOT NULL,
        date_played TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS playlist_history (
        id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        playlist_id BIGINT NOT NULL,
        date_played TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
]


def get_mysql_config():
    return {
        "host": os.getenv("DB_HOST"),
        "port": int(os.getenv("DB_PORT", "4000")),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "database": os.getenv("DB_NAME"),
        "ssl_ca": os.getenv("DB_SSL_CA", ""),
    }


def get_mysql_connection():
    cfg = get_mysql_config()
    conn_kwargs = {
        "host": cfg["host"],
        "port": cfg["port"],
        "user": cfg["user"],
        "password": cfg["password"],
        "database": cfg["database"],
        "charset": "utf8mb4",
        "connect_timeout": 15,
    }
    ssl_ca = cfg["ssl_ca"]
    if ssl_ca and os.path.exists(ssl_ca):
        conn_kwargs["ssl"] = {"ca": ssl_ca}
    else:
        try:
            import certifi
            conn_kwargs["ssl"] = {"ca": certifi.where()}
        except ImportError:
            print(" [Sync] No CA certificate available, connecting without TLS verification.")
    return pymysql.connect(**conn_kwargs)


class DatabaseSync:
    def __init__(self, sqlite_path):
        self.sqlite_path = sqlite_path
        self._stop_event = threading.Event()
        self._thread = None
        self._remote_conn = None

    def _get_conn(self):
        """Get or reconnect the MySQL/TiDB connection."""
        if self._remote_conn is None or not self._remote_conn.open:
            self._remote_conn = get_mysql_connection()
        return self._remote_conn

    def _init_schema(self):
        """Create MySQL tables if they don't exist."""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                for statement in REMOTE_SCHEMA:
                    cur.execute(statement)
            conn.commit()

            # Migrate existing tables created before the thumbnail column existed
            try:
                with conn.cursor() as cur:
                    cur.execute("ALTER TABLE playlists ADD COLUMN thumbnail TEXT")
                conn.commit()
            except Exception:
                conn.rollback()

            print(" [Sync] MySQL schema initialized.")
        except Exception as e:
            print(f" [Sync] Failed to init MySQL schema: {e}")
            raise

    @staticmethod
    def _align_auto_increment(remote_conn, table):
        """Keep AUTO_INCREMENT aligned after inserting explicit IDs."""
        with remote_conn.cursor() as cur:
            cur.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table}")
            next_id = cur.fetchone()[0]
        if next_id and next_id > 1:
            with remote_conn.cursor() as cur:
                cur.execute(f"ALTER TABLE {table} AUTO_INCREMENT = {int(next_id)}")
            remote_conn.commit()

    def _sync_songs(self, sqlite_conn, remote_conn):
        """Upsert all songs from SQLite -> MySQL."""
        rows = sqlite_conn.execute("SELECT file, downloaded_link, title, date_download, artist FROM Songs").fetchall()
        if not rows:
            return
        with remote_conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO songs (file, downloaded_link, title, date_download, artist)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    downloaded_link = VALUES(downloaded_link),
                    title = VALUES(title),
                    date_download = VALUES(date_download),
                    artist = VALUES(artist)
                """,
                rows
            )
            remote_conn.commit()

    def _sync_playlists(self, sqlite_conn, remote_conn):
        """Upsert all playlists (including thumbnail)."""
        rows = sqlite_conn.execute("SELECT id, title, description, thumbnail FROM Playlists").fetchall()
        if not rows:
            return
        # Normalize thumbnail bytes from sqlite BLOB storage to text
        rows = [tuple(v.decode('utf-8', 'ignore') if isinstance(v, bytes) else v for v in row) for row in rows]
        with remote_conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO playlists (id, title, description, thumbnail)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    title = VALUES(title),
                    description = VALUES(description),
                    thumbnail = VALUES(thumbnail)
                """,
                rows
            )
            remote_conn.commit()
        self._align_auto_increment(remote_conn, "playlists")

    def _sync_song_playlist(self, sqlite_conn, remote_conn):
        """Full replace of song-playlist associations."""
        rows = sqlite_conn.execute("SELECT id, song_file, playlist_id, date_added FROM Song_Playlist").fetchall()
        with remote_conn.cursor() as cur:
            cur.execute("DELETE FROM song_playlist")
            if rows:
                cur.executemany(
                    "INSERT INTO song_playlist (id, song_file, playlist_id, date_added) VALUES (%s, %s, %s, %s)",
                    rows
                )
            remote_conn.commit()
        self._align_auto_increment(remote_conn, "song_playlist")

    def _sync_lyrics(self, sqlite_conn, remote_conn):
        """Upsert lyrics."""
        rows = sqlite_conn.execute("SELECT id, song_file, lyrics FROM Lyrics").fetchall()
        if not rows:
            return
        with remote_conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO lyrics (id, song_file, lyrics)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    song_file = VALUES(song_file),
                    lyrics = VALUES(lyrics)
                """,
                rows
            )
            remote_conn.commit()
        self._align_auto_increment(remote_conn, "lyrics")

    def _sync_history(self, sqlite_conn, remote_conn, table_sqlite, table_pg, columns):
        """Incremental sync for append-only history tables using max ID."""
        with remote_conn.cursor() as cur:
            cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table_pg}")
            last_synced_id = cur.fetchone()[0]
        if last_synced_id is None:
            last_synced_id = 0

        col_list = ", ".join(columns)
        rows = sqlite_conn.execute(
            f"SELECT {col_list} FROM {table_sqlite} WHERE id > ? ORDER BY id",
            (last_synced_id,)
        ).fetchall()

        if not rows:
            return

        placeholders = ", ".join(["%s"] * len(columns))
        with remote_conn.cursor() as cur:
            cur.executemany(
                f"INSERT IGNORE INTO {table_pg} ({col_list}) VALUES ({placeholders})",
                rows
            )
            remote_conn.commit()
        self._align_auto_increment(remote_conn, table_pg)

    def _apply_deletions(self, sqlite_conn, remote_conn):
        """Propagate locally-deleted rows to the remote DB via tombstones."""
        REMOTE_DELETE_COLUMNS = {
            'songs': 'file',
            'playlists': 'id',
            'lyrics': 'song_file',
            'playlist_history': 'playlist_id',
        }
        rows = sqlite_conn.execute("SELECT table_name, row_key FROM Sync_Deletions").fetchall()
        if not rows:
            return
        with remote_conn.cursor() as cur:
            for table, row_key in rows:
                col = REMOTE_DELETE_COLUMNS.get(table)
                if not col:
                    continue
                cur.execute(f"DELETE FROM {table} WHERE {col} = %s", (row_key,))
            remote_conn.commit()
        # Only clear tombstones once they were applied successfully
        with sqlite_conn:
            sqlite_conn.execute("DELETE FROM Sync_Deletions")

    def _run_sync(self):
        """Execute a full sync cycle."""
        try:
            sqlite_conn = sqlite3.connect(self.sqlite_path)
            remote_conn = self._get_conn()

            self._apply_deletions(sqlite_conn, remote_conn)
            self._sync_songs(sqlite_conn, remote_conn)
            self._sync_playlists(sqlite_conn, remote_conn)
            self._sync_song_playlist(sqlite_conn, remote_conn)
            self._sync_lyrics(sqlite_conn, remote_conn)
            self._sync_history(
                sqlite_conn, remote_conn,
                "Music_History", "music_history",
                ["id", "song_file", "date_played"]
            )
            self._sync_history(
                sqlite_conn, remote_conn,
                "Playlist_History", "playlist_history",
                ["id", "playlist_id", "date_played"]
            )

            sqlite_conn.close()
        except Exception as e:
            print(f" [Sync] Sync error: {e}")
            # Reset connection on failure so it reconnects next cycle
            try:
                if self._remote_conn and self._remote_conn.open:
                    self._remote_conn.close()
            except Exception:
                pass
            self._remote_conn = None

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

        if self._remote_conn and self._remote_conn.open:
            self._remote_conn.close()
        print(" [Sync] Sync thread stopped.")

    def start(self):
        """Initialize MySQL schema and start the background sync thread."""
        try:
            self._init_schema()
        except Exception as e:
            print(f" [Sync] Could not initialize MySQL, sync disabled: {e}")
            return

        self._thread = threading.Thread(target=self._loop, daemon=True, name="db-sync")
        self._thread.start()
        print(f" [Sync] Background sync started (every {SYNC_INTERVAL}s).")

    def stop(self):
        """Signal the sync thread to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)