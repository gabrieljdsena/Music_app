# Database Schema & Remote Sync

Hathor stores everything locally in **SQLite** (`music_player.db`). When configured, a background sync mirrors the local data into a **MySQL/TiDB** database.

## Local schema (`database.sql`)

```sql
Songs(
  file            VARCHAR(255) PRIMARY KEY,   -- filename in the songs folder
  downloaded_link VARCHAR(255),               -- source URL (YouTube)
  title           VARCHAR(255) NOT NULL,
  date_download   DATETIME   DEFAULT CURRENT_TIMESTAMP,
  artist          VARCHAR(255)
)

Playlists(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  title       VARCHAR(255) NOT NULL,
  description TEXT,
  thumbnail   BLOB                       -- cover image bytes
)

Song_Playlist(                              -- many-to-many songs <-> playlists
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  song_file   VARCHAR(255) NOT NULL -> Songs(file),
  playlist_id BIGINT       NOT NULL -> Playlists(id),
  date_added  DATETIME     DEFAULT CURRENT_TIMESTAMP
)

Settings(                                   -- single row (id = 1)
  id                 INT NOT NULL PRIMARY KEY,
  current_song       VARCHAR(255) -> Songs(file),
  current_playlist   INTEGER,
  current_volume     FLOAT,
  limit_downloads    INT,
  standardize_volume BOOLEAN,
  current_tab        VARCHAR(255),
  window_width       INT,
  window_height      INT,
  background_path    VARCHAR(255),
  songs_path         VARCHAR(255),
  browser            VARCHAR(50)
)

Lyrics(
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  song_file VARCHAR(255) NOT NULL -> Songs(file),
  lyrics    TEXT           -- JSON: { synced: [...], plain: "..." }
)

Music_History(                              -- every time a song is played
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  song_file   VARCHAR(255) NOT NULL -> Songs(file),
  date_played DATETIME     DEFAULT CURRENT_TIMESTAMP
)

Playlist_History(                           -- every time a playlist is played
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  playlist_id BIGINT       NOT NULL,
  date_played DATETIME     DEFAULT CURRENT_TIMESTAMP
)

Sync_Deletions(                             -- tombstones for remote deletion sync
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  table_name VARCHAR(255) NOT NULL,
  row_key    VARCHAR(255) NOT NULL,
  deleted_at DATETIME     DEFAULT CURRENT_TIMESTAMP
)
```

`Settings` is populated with a single default row (`INSERT OR IGNORE ... VALUES (1, ...)`).

> The playlist `thumbnail` column: local it's a `BLOB`; `sync.py` normalizes it to UTF-8 **text** (a base64 image) for the remote DB.

## Remote schema (`sync.py`)

The remote database mirrors the local one **except `Settings`** (kept local-only — per-machine state). Tables are created on first sync:

| Local | Remote |
| ----- | ------ |
| `Songs` | `songs` |
| `Playlists` | `playlists` |
| `Song_Playlist` | `song_playlist` |
| `Lyrics` | `lyrics` |
| `Music_History` | `music_history` |
| `Playlist_History` | `playlist_history` |

`Sync_Deletions` exists only locally (it is the *source* of tombstones, applied then cleared).

## How the sync works

`DatabaseSync` (`sync.py`) starts a daemon thread that runs a full cycle every **30 seconds** (`SYNC_INTERVAL`), 5 seconds after app start, plus one final cycle on shutdown.

### Sync order per cycle

1. **`_apply_deletions`** — read all `Sync_Deletions` rows (e.g. created by `DatabaseManager.record_deletion` when playlists are deleted), `DELETE` the matching rows on the remote (mapping `songs→file`, `playlists→id`, `lyrics→song_file`, `playlist_history→playlist_id`), then clear the local tombstone table.
2. **`_sync_songs`** — UPSERT all songs (`INSERT ... ON DUPLICATE KEY UPDATE`).
3. **`_sync_playlists`** — UPSERT all playlists **including `id`**, then `_align_auto_increment` so future `AUTO_INCREMENT` ids don't collide.
4. **`_sync_song_playlist`** — full replace: `DELETE` all remote `song_playlist` rows, re-insert everything, align auto-increment.
5. **`_sync_lyrics`** — UPSERT lyrics with explicit ids.
6. **`_sync_history`** (×2) — *incremental* for `music_history` / `playlist_history`: reads the remote `MAX(id)` and inserts only local rows with `id > max`, using `INSERT IGNORE`.

### Connections

- `pymysql` connections are wrapped in `_get_conn()`, which reconnects if the connection dropped.
- TLS: uses `DB_SSL_CA` if provided and present on disk, otherwise `certifi`'s bundle.
- Any exception during a cycle logs `[Sync] Sync error: ...` and forces a reconnect next cycle — sync failures never crash the app.

## First-run remote import

If sync is configured and the local DB is brand new, `main.py` prompts the user. If accepted, `DatabaseManager.sync_remote_to_local_and_download()`:

1. Pulls remote songs/playlists/lyrics/history into SQLite.
2. Compares against local files and **queues downloads** for missing songs.
3. Starts the regular background sync.