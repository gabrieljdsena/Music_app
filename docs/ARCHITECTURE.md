# Architecture

Hathor is a **Python backend + HTML/JS frontend** desktop app. The UI runs inside a `pywebview` window and talks to Python over a JS bridge.

## Component diagram

```
┌─────────────────────────────────────────────┐
│                 pywebview window              │
│                                               │
│  ui/index.html + views/* (HTML · Alpine.js)    │
│     │  evaluate_js()      ▲  JS calls          │
│     ▼                     │  bridge            │
│  ┌──────────────────────────┐                  │
│  │        api.Api           │  (api.py)        │
│  │  single JS-bridge object │                  │
│  └──────┬───────────┬──────┘                  │
└─────────┼───────────┼─────────────────────────┘
          │           │
   ┌──────▼───────┐  ┌▼────────────────┐
   │ services/    │  │ Download.py     │
   │ playback     │  │  yt-dlp + FFmpeg│
   │ metadata     │  │  iTunes API     │
   │ database     │  ├───────────────►─┤
   │ lyrics       │  │ sync.py         │
   │ windows_media│  │  DatabaseSync   │
   └──────┬───────┘  └───────┬─────────┘
          │                  │
   ┌──────▼──────┐    ┌─────▼──────────────┐
   │ SQLite      │    │ MySQL / TiDB       │
   │ music_player.db   │ (remote, optional) │
   └─────────────┘    └────────────────────┘
```

## Startup sequence (`main.py`)

1. **Load `.env`** (`python-dotenv`). In frozen builds it is read from next to the executable — never bundled.
2. **Compile Tailwind** (dev only): `npx @tailwindcss/cli -i ui/input.css -o ui/output.css`.
3. **Path setup** — `get_base_path()` (`sys._MEIPASS` when frozen) vs `get_data_path()` (`sys.executable` dir when frozen). The DB, `.env`, and library go in the *data* path; bundled assets come from `_MEIPASS`.
4. **Init DB** — runs `database.sql` against `music_player.db` if it exists as a script; detects first run.
5. **Instantiate `Api(db_path)`** and create the `pywebview` window (`title='Hathor'`, centered, `min_size=(895, 400)`).
6. **Link `api._window`** so Python can push JS back via `window.evaluate_js(...)`.
7. **Remote sync** — if `DB_HOST` is set, create `DatabaseSync`; on first run ask the user whether to import the remote library (SweetAlert2 dialog), otherwise start the background loop immediately.
8. **`on_start`** — init `pygame.mixer`, `api.load_current_song()`, start sync thread.

Window resizes are debounced (`threading.Timer`, 0.4 s) and persisted to `Settings.window_width/height` on every resize.

## The JS bridge (`api.py`)

Every public method on `Api` is callable from JavaScript because `pywebview` exposes the `js_api` object by name. The API is deliberately a thin **wrapper layer** — real logic lives in `services/` (each service holds a backreference to `api` so it can read shared state like `api.playing` and push `evaluate_js` updates).

| Area | api.py methods (examples) |
| ---- | ------------------------- |
| Playback/queue | `play_button`, `play_next`, `play_prev`, `populate_queue`, `populate_queue_from_list`, `jump_to_queue_index`, `add_to_queue`, `next_to_queue`, `clear_queue`, `remove_from_queue`, `reorder_queue`, `toggle_shuffle`, `get_current_pos`, `progress_slider_click` |
| Metadata/files | `get_song_metadata`, `get_cover_art_base64`, `get_local_image_base64`, `update_song_metadata`, `delete_song` |
| Lyrics | `get_lyrics`, `romanize_text` |
| Database | `load_playlists`, `new_playlist`, `update_playlist`, `delete_playlist`, `get_playlist_songs`, `update_song_playlists`, `get_download_history`, `get_played_history`, `sync_local_songs_to_db`, `sync_remote_to_local_and_download` |
| Downloads | `search_yt`, `recieve_download`, `search_itunes_metadata`, `search_itunes_metadata_multi` |
| Settings/UI | `volume_slider`, `update_download_limit`, `update_background`, `update_songs_path`, `load_settings`, `send_song_list`, `load_current_song`, `pick_folder`, `pick_background`, `pick_playlist_image` |

Python → JS communication uses `self._window.evaluate_js("...")` with JSON-payload strings, for example `send_song_list` pushes the whole library with `song_list(<json>)` and download progress calls `update_download_progress(...)`.

## Services (`services/`)

- **`playback.py` — `PlaybackController`** — owns the queue state (`next_songs`, `prev_songs`, `unshuffled_song_list`, `current_playlist_id`), shuffle, repeat, playback position tracking (`current_time_offset`, `pause_time`), and the "song ended → play next" auto-advance logic. Uses `pygame.mixer.music`.
- **`metadata.py` — `MetadataManager`** — reads track metadata + embedded cover art with `mutagen`, converts covers to base64 data URIs for the UI, synchronizes the `Songs` table, renames on metadata change, and deletes files.
- **`database.py` — `DatabaseManager`** — all SQLite read/write: playlists CRUD, song↔playlist mapping, download/played history, local folder → DB rescan, and `sync_remote_to_local_and_download` (the first-run remote import).
- **`lyrics.py` — `LyricsService`** — local cache lookup, remote lyric fetch, LRC parsing, and romaji conversion via `pykakasi` (degrades gracefully if the lib is missing).
- **`windows_media.py` — `WindowsMediaOverlay`** — `winsdk` SMTC integration: play/pause/next/previous buttons, playback status, and album art handed to the OS overlay.

## Downloads (`Download.py`)

`MusicDownloader` wraps `yt-dlp`:

- `search_yt` — flat search (`ytsearchN`, no download).
- `download_song` — `bestaudio/best` → FFmpeg MP3 320 kbps → iTunes lookup → `apply_metadata` (ID3v2.3) → safe rename. Progress hooks drive the UI.
- `search_itunes` / `search_itunes_multi` — iTunes Search API with query-cleaning fallbacks; downloads high-res artwork.
- `_safe_filename` — strips Windows-illegal characters and double spaces, caps at 150 chars.

## Audio engine

Audio is played by **`pygame.mixer.music`** (streams MP3s). Volume is set with `set_volume`; seeks restart playback at an offset (`play(0, sec)`) and keep reconcile with `PlaybackController.current_time_offset`. Because pygame's mixer is a single stream, everything above—queue, shuffle, repeat—is orchestrated by `PlaybackController`.

## Threading model

| Thread | Purpose |
| ------ | ------- |
| Main | `pywebview.start(...)`, window event loop, `pygame.mixer` |
| Sync | `DatabaseSync._loop` — sleeps 5 s, syncs every 30 s, final sync on stop (daemon) |
| First-run prompt | `_prompt_remote_sync` → `do_prompt` runs the `evaluate_js` SweetAlert flow off-thread |
| Resize debounce | `threading.Timer(0.4, save_window_size)` |

Downloads run inside `yt-dlp` on the caller's thread while progress is pushed back through `evaluate_js`.

## Debugging

- `monitor.py` is a **disabled** RAM monitor (uncomment the import in `main.py` to use it).
- Most Python modules print `[Python] ...` / `[Sync] ...` / `[Debug] ...` prefixed logs to stdout; in a console build these appear in the terminal.