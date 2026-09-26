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
7. **Remote sync** — manual-only. If `DB_HOST` is set, a first run asks whether to import the remote library (SweetAlert2 dialog); afterwards pushes/pulls happen exclusively through the Settings buttons (`Api.sync_local_to_remote` / `Api.sync_remote_to_local_and_download`). No background thread is started.
8. **`on_start`** — init `pygame.mixer`, `api.load_current_song()` (restores the current song *and* rebuilds its queue from the persisted `Settings.queue_source` context), start-up maintenance thread.

Window resizes are debounced (`threading.Timer`, 0.4 s) and persisted to `Settings.window_width/height` on every resize.

## The JS bridge (`api.py`)

Every public method on `Api` is callable from JavaScript because `pywebview` exposes the `js_api` object by name. The API is deliberately a thin **wrapper layer** — real logic lives in `services/` (each service holds a backreference to `api` so it can read shared state like `api.playing` and push `evaluate_js` updates).

| Area | api.py methods (examples) |
| ---- | ------------------------- |
| Playback/queue | `play_button`, `play_next`, `play_prev`, `populate_queue`, `populate_queue_from_list` (with `source` context), `jump_to_queue_index`, `add_to_queue`, `next_to_queue`, `clear_queue`, `remove_from_queue`, `reorder_queue`, `toggle_shuffle`, `get_current_pos`, `progress_slider_click`, `get_playback_settings`, `set_crossfade` |
| Metadata/files | `get_song_metadata`, `get_cover_art_base64`, `get_local_image_base64`, `update_song_metadata`, `delete_song`, `delete_podcast` |
| Lyrics | `get_lyrics`, `search_lyrics`, `save_lyrics`, `romanize_text` |
| Database | `load_playlists`, `new_playlist`, `update_playlist`, `delete_playlist`, `get_playlist_songs`, `update_song_playlists`, `get_songs_by_artist`, `get_songs_by_album`, `get_all_songs`, `get_daily_mix`, `get_recently_played`, `get_recently_downloaded`, `get_podcasts`, `get_podcast_details`, `delete` variants, `get_download_history`, `get_played_history`, `sync_local_songs_to_db`, `sync_local_podcasts_to_db`, `sync_local_to_remote`, `sync_remote_to_local_and_download` |
| Downloads | `search_yt`, `recieve_download` (accepts `{url, title, is_podcast}`), `get_download_jobs`, `retry_download`, `search_itunes_metadata`, `search_itunes_metadata_multi` |
| Settings/UI | `volume_slider`, `update_download_limit`, `update_background`, `update_songs_path`, `update_podcasts_path`, `load_settings`, `send_song_list`, `load_current_song`, `pick_folder`, `pick_podcasts_folder`, `pick_background`, `pick_playlist_image` |

Python → JS communication uses `self._window.evaluate_js("...")` with JSON-payload strings, for example `send_song_list` pushes the whole library with `song_list(<json>)` and download progress calls `update_download_progress(...)`.

## Services (`services/`)

- **`playback.py` — `PlaybackController`** — owns the queue state (`next_songs`, `prev_songs`, `unshuffled_song_list`, `current_playlist_id`), shuffle, repeat, playback position tracking (`current_time_offset`, `pause_time`), and the "song ended → play next" auto-advance logic. Uses `pygame.mixer.music` as the primary output plus one `Sound` channel for crossfades (see Audio engine). Persists the playback context (`Settings.queue_source`: playlist / daily mix / artist / album / recents / podcast) so restarts rebuild the right queue; a persisted custom queue still wins.
- **`metadata.py` — `MetadataManager`** — reads track metadata + embedded cover art with `mutagen`, converts covers to base64 data URIs for the UI, synchronizes the `Songs`/`Podcasts` tables, renames on metadata change, and deletes files (songs and podcast episodes separately).
- **`database.py` — `DatabaseManager`** — all SQLite read/write: playlists CRUD, song↔playlist mapping, artist/album lookups, full-library and podcast listings (`get_all_songs`, `get_podcasts`, lightweight by design), on-demand podcast details, daily-mix generation (2 × top 10, 5 × 11–25, 13 × 26–70 by `Music_History` counts, rest preferring outside the top 70), recently-played/downloaded strips, download/played history, local folder → DB rescans, queue-source rebuild, and `sync_remote_to_local_and_download` (the manual remote import, podcasts included).
- **`lyrics.py` — `LyricsService`** — local cache lookup, remote lyric fetch (LRCLIB exact endpoint, with a track-only search fallback for artist-less songs that only accepts exact track matches), LRC parsing, and romaji conversion via `pykakasi` (degrades gracefully if the lib is missing).
- **`windows_media.py` — `WindowsMediaOverlay`** — `winsdk` SMTC integration: play/pause/next/previous buttons, playback status, and album art handed to the OS overlay (via `StorageFile`, with embedded-art fallback and file-URI fallback). All overlay calls are wrapped so OS flakiness can never break playback or metadata saves.
- **`downloads.py` — `DownloadManager`** — background download queue with a bounded concurrency pool (`Settings.limit_downloads`), a persisted `Download_Queue` job log (including the `is_podcast` routing flag, preserved across retries), and a JS progress feed. `retry()` re-enqueues failed jobs.
- **`apple.py` — `AppleService`** — artist/album image lookups; **`startup_maintenance.py`** — FFmpeg auto-download + yt-dlp library update checks shown in the startup toast.

## Downloads (`Download.py`)

`MusicDownloader` wraps `yt-dlp`:

- `search_yt` — flat search (`ytsearchN`, no download).
- `download_song(search, progress_callback, dest_path, is_podcast)` — `bestaudio/best` → FFmpeg MP3 320 kbps into `dest_path` (songs folder by default, podcasts folder for podcast jobs) → metadata → `apply_metadata` (ID3v2.3) → safe rename. Progress hooks drive the UI.
- Songs go through the iTunes lookup; **podcast downloads skip it** and keep the uploader's title/author (plus no artwork fetch).
- `search_itunes` / `search_itunes_multi` — iTunes Search API with query-cleaning fallbacks; downloads high-res artwork.
- `_safe_filename` — strips Windows-illegal characters and double spaces, caps at 150 chars.

## Audio engine

Audio is played by **`pygame.mixer.music`** (streams MP3s), which is the primary output. Volume is set with `set_volume`; seeks restart playback at an offset (`play(0, sec)`) reconciled with `PlaybackController.current_time_offset`. Because pygame's mixer is a single stream, everything above—queue, shuffle, repeat—is orchestrated by `PlaybackController`.

**Crossfade** (optional, Settings) alternates between the music module and one dedicated `Sound` channel: near a track's end the incoming song fades in on the idle output while the outgoing fades out, using an **equal-power** (`cos`/`sin`) curve over 50 ms steps, then ownership flips. Ramps are generation-guarded so manual actions (next/prev/seek/explicit play) cancel them cleanly, and a pause mid-ramp settles the ramp first. **Gapless handoff is always on**: a `set_endevent` watcher plus a monitor backup advance the instant a stream ends (with staleness guards so phantom end-events can neither skip nor stop tracks).

## Threading model

| Thread | Purpose |
| ------ | ------- |
| Main | `pywebview.start(...)`, window event loop, `pygame.mixer` |
| Crossfade monitor | polls position/remaining every 0.2 s, starts ramps, advances channel-owned tracks (daemon) |
| Endevent watcher | `music.set_endevent` pump, reacts within ~50 ms (daemon) |
| First-run prompt | `_prompt_remote_sync` → `do_prompt` runs the `evaluate_js` SweetAlert flow off-thread |
| Resize debounce | `threading.Timer(0.4, save_window_size)` |
| Startup maintenance | FFmpeg + yt-dlp checks with progress toast (daemon) |

Downloads run on a bounded pool (`DownloadManager` scheduler + one worker thread per active job). There is no sync thread — pushes/pulls run synchronously inside the Settings-button bridge calls.

## Debugging

- `monitor.py` is a **disabled** RAM monitor (uncomment the import in `main.py` to use it).
- Most Python modules print `[Python] ...` / `[Sync] ...` / `[Debug] ...` prefixed logs to stdout; in a console build these appear in the terminal.