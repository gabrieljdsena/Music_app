# Database Schema & Remote Sync

Hathor stores everything locally in **SQLite** (`music_player.db`). When configured, the library (songs, podcasts, playlists, lyrics, history, daily mix) can be pushed to / pulled from a **MySQL/TiDB** database — always manually, via the Settings buttons. There is no background sync thread.

## Local schema (`database.sql`)

```sql
Songs(
  file            VARCHAR(255) PRIMARY KEY,   -- filename in the songs folder
  downloaded_link VARCHAR(255),               -- source URL (YouTube)
  title           VARCHAR(255) NOT NULL,
  date_download   DATETIME   DEFAULT CURRENT_TIMESTAMP,
  artist          VARCHAR(255)
)

Podcasts(                                     -- separate non-music library
  file            VARCHAR(255) PRIMARY KEY,   -- filename in the podcasts folder
  downloaded_link VARCHAR(255),
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
  standard_volume    BOOLEAN,                -- (column: standardize_volume)
  current_tab        VARCHAR(255),
  window_width       INT,
  window_height      INT,
  background_path    VARCHAR(255),
  songs_path         VARCHAR(255),
  podcasts_path      VARCHAR(255),           -- separate podcasts/audio folder
  browser            VARCHAR(50),
  queue_songs        TEXT,                   -- persisted custom queue (JSON filenames)
  custom_queue       INTEGER DEFAULT 0,      -- 1 = restore queue_songs on launch
  queue_source       TEXT,                   -- playback context JSON {type, id}
  crossfade_enabled  INTEGER DEFAULT 0,
  crossfade_seconds  REAL DEFAULT 5
)

Download_Queue(                            -- download job log (local only)
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  qid         UUID NOT NULL UNIQUE,
  url         VARCHAR(1000),
  title       VARCHAR(255),
  artist      VARCHAR(255),
  status      VARCHAR(50) DEFAULT 'queued',
  progress    REAL DEFAULT 0,
  error       TEXT,
  filename    VARCHAR(255),
  is_podcast  INTEGER DEFAULT 0,            -- route to podcasts folder/table
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
)

Daily_Mix(                                  -- today's generated mix (local only)
  mix_date    VARCHAR(10) PRIMARY KEY,      -- YYYY-MM-DD
  song_files  TEXT NOT NULL,                -- JSON filename list, play order
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
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
| `Podcasts` | `podcasts` |
| `Playlists` | `playlists` |
| `Song_Playlist` | `song_playlist` |
| `Lyrics` | `lyrics` |
| `Music_History` | `music_history` |
| `Playlist_History` | `playlist_history` |
| `Daily_Mix` | `daily_mix` |

`Sync_Deletions`, `Download_Queue`, and `Daily_Mix`-adjacent housekeeping stay local-only except where noted below (`daily_mix` *is* mirrored — see pull/push rules).

## How the sync works

Sync is **manual-only**: no background thread exists. Two directions, both from Settings buttons (or the first-run prompt):

### Push — "Push to Remote" (`Api.sync_local_to_remote` → `DatabaseSync.sync_once`)

Runs one full `_run_sync()` cycle:

1. **`_apply_deletions`** — read all `Sync_Deletions` rows, `DELETE` the matching remote rows (mapping `songs→file`, `podcasts→file`, `playlists→id`, `lyrics→song_file`, `music_history→song_file`, `playlist_history→playlist_id`), then clear the local tombstone table.
2. **`_sync_songs`** — UPSERT all songs (`INSERT ... ON DUPLICATE KEY UPDATE`).
3. **`_sync_podcasts`** — UPSERT all podcasts, same pattern.
4. **`_sync_playlists`** — UPSERT all playlists **including `id`**, then `_align_auto_increment` so future `AUTO_INCREMENT` ids don't collide.
5. **`_sync_song_playlist`** — full replace: `DELETE` all remote `song_playlist` rows, re-insert everything, align auto-increment.
6. **`_sync_lyrics`** — UPSERT lyrics with explicit ids.
7. **`_sync_daily_mix`** — UPSERT the local daily mix (last writer wins per `mix_date`) and prune remote mixes older than the newest local one, so an outdated device can never delete a newer mix.
8. **`_sync_history`** (×2) — *incremental* for `music_history` / `playlist_history`: reads the remote `MAX(id)` and inserts only local rows with `id > max`, using `INSERT IGNORE`.

### Pull — "Sync Remote" (`DatabaseManager.sync_remote_to_local_and_download`)

Fetches remote songs, podcasts, playlists, lyrics, history, and the daily mix into SQLite (guarded so remote DBs predating `podcasts`/`daily_mix` don't break the pull), then queues downloads for missing files — songs into the songs folder, podcasts into the podcasts folder. Adopting the remote daily mix makes every device play the same mix of the day; locally stored mixes older than today are pruned.

### Connections

- `pymysql` connections are wrapped in `_get_conn()`, which reconnects if the connection dropped.
- TLS: uses `DB_SSL_CA` if provided and present on disk, otherwise `certifi`'s bundle.
- Any exception during a cycle logs `[Sync] Sync error: ...` and forces a reconnect next cycle — sync failures never crash the app.

## First-run remote import

If sync is configured and the local DB is brand new, `main.py` prompts the user. If accepted, `DatabaseManager.sync_remote_to_local_and_download()`:

1. Pulls remote songs, podcasts, playlists, lyrics, daily mix, and history into SQLite.
2. Compares against local files and **queues downloads** for missing songs and episodes (each into its own folder).
3. Nothing else starts afterwards — further syncs are manual via the Settings buttons.