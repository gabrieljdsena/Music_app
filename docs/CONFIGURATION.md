# Configuration

Hathor has two configuration surfaces:

1. **Persisted app settings** — stored in the `Settings` row of the local SQLite database and editable from the UI.
2. **Environment configuration** — a `.env` file used exclusively for the optional **MySQL/TiDB remote sync**.

## App settings (SQLite `Settings` table)

Loaded at startup by `settings.py` and kept current by `api.py` whenever you change something in the UI.

| Setting | Column | Default | Notes |
| ------- | ------ | ------- | ----- |
| Songs folder | `songs_path` | `%appdata%\musicPlayer` | Folder scanned for `.mp3` files |
| Podcasts folder | `podcasts_path` | `%appdata%\musicPlayerPodcasts` | Separate non-music library |
| Volume | `current_volume` | `0.7` | Applied to `pygame.mixer` on every change |
| Window size | `window_width`, `window_height` | `1280 × 720` | Saved 0.4 s after each resize |
| Download limit | `limit_downloads` | `3` | Max concurrent downloads |
| Background | `background_path` | *(empty)* | Custom background image path |
| Crossfade | `crossfade_enabled`, `crossfade_seconds` | off, `5.0` | 1–12 s equal-power overlap on auto-advance |
| Current song | `current_song` | — | Resumed on next launch |
| Current playlist | `current_playlist` | — | Playlist resumed on next launch (legacy; superseded by `queue_source`) |
| Queue source | `queue_source` | — | Playback-context JSON `{type, id}` rebuilt on launch |
| Custom queue | `queue_songs`, `custom_queue` | — | Manually built queue restored verbatim |

`api.py` exposes the corresponding setters to the JS UI:

- `volume_slider(volume)`
- `update_download_limit(limit)`
- `update_background(background)` / `remove_background()`
- `update_songs_path(songs_path)` / `pick_folder()`
- `update_podcasts_path(podcasts_path)` / `pick_podcasts_folder()`
- `set_crossfade(enabled, seconds)` / `get_playback_settings()`
- `pick_background()`

## Remote sync (`.env`)

Remote sync is **disabled by default**. It is only activated when `DB_HOST` is present in the environment (loaded by `python-dotenv` in `main.py`). Without it, `main.py` prints:

```
[Sync] No remote DB configured in .env, sync disabled.
```

### Environment variables

| Variable | Required | Example | Purpose |
| -------- | -------- | ------- | ------- |
| `DB_HOST` | yes | `gateway01.ap-northeast-1.prod.aws.tidbcloud.com` | MySQL/TiDB host or gateway |
| `DB_PORT` | no (default `4000`) | `4000` | Port. TiDB Cloud defaults to `4000`; standard MySQL is `3306` |
| `DB_USER` | yes | `root` | Remote user (e.g. `<user>.auth` for TiDB Cloud) |
| `DB_PASSWORD` | yes | `••••` | Remote password |
| `DB_NAME` | yes | `music_app` | Target database |
| `DB_SSL_CA` | no | `CA.pem` | Path to a CA bundle. Falls back to `certifi`'s bundle. |

A minimal `.env`:

```env
DB_HOST=gateway01.ap-northeast-1.prod.aws.tidbcloud.com
DB_PORT=4000
DB_USER=2YourName.auth
DB_PASSWORD=your-password
DB_NAME=music_app
```

> **Security**: `.env` (and `CA.pem`) are ignored by `.gitignore` — never commit real credentials.

### Where the `.env` is read

`main.py`:

1. Loads `.env` from the current working directory.
2. If the app is frozen (PyInstaller build), loads `.env` from **next to the executable** (`os.path.dirname(sys.executable)`) — it is deliberately never bundled inside the exe.

### Behavior when configured

- On **first run** with sync configured, the app prompts the user to load the remote library (songs, podcasts, playlists, lyrics, daily mix, history) and queue missing downloads into their respective folders.
- After that, everything is manual: **Push to Remote** uploads one full cycle; **Sync Remote** pulls remote rows down (see [Database & remote sync](DATABASE.md)).
- Deletions are propagated via the `Sync_Deletions` tombstone table (songs, podcasts, playlists, lyrics, history).

## Turning sync off

Delete or comment out `DB_HOST`, or remove the `.env` file entirely. The next launch will run fully local.