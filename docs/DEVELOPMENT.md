# Development & Packaging

Notes for developers working on Hathor.

## Project layout

```
main.py               Entry point; window + startup, first-run remote prompt
api.py                pywebview JS bridge (all methods callable from the UI)
Download.py           yt-dlp downloader + iTunes metadata/artwork
sync.py               DatabaseSync — manual one-shot MySQL/TiDB push (+schema)
database.sql          SQLite schema
settings.py           Loads persisted settings from SQLite
monitor.py            Debug RAM monitor (disabled)
services/
  playback.py         PlaybackController — queue, shuffle, repeat, position, crossfade engine, queue-source persistence
  metadata.py         MetadataManager — mutagen tag read/write, covers, deletes (songs + podcasts)
  database.py         DatabaseManager — SQLite CRUD, library/podcast listings, daily mix, recents, queue rebuild + remote import
  lyrics.py           LyricsService — fetch, cache, LRC parse, romaji, track-only fallback
  windows_media.py    WindowsMediaOverlay — winsdk SMTC media keys/overlay
  downloads.py        DownloadManager — bounded download pool, job log, retries
  apple.py            AppleService — artist/album artwork lookups
  startup_maintenance.py  FFmpeg auto-download + yt-dlp update checks
  __init__.py         Exports the services
ui/
  index.html          Main single-page app shell
  views/*.html        View fragments (home dashboard, all_songs, daily_mix, podcasts, artist, album, playlists, download, history, settings)
  modals/*.html       Modal fragments (add/edit playlist, edit song)
  input.css           Tailwind source (v4 syntax)
  output.css          Compiled Tailwind output (generated)
  css.css             Hand-written styling
  cdn.min.js          Vendored Alpine.js
  notyf.*             Vendored Notyf
  sweetalert2/        Vendored SweetAlert2
music_player.spec     PyInstaller spec
requirements.txt      Python deps
package.json          npm deps (Alpine, SweetAlert2, Notyf, Tailwind toolchain)
```

## Dev workflow

### Running in development

```powershell
python main.py
```

> On every dev start, Tailwind is recompiled: `npx @tailwindcss/cli -i "ui\input.css" -o "ui\output.css"`. `main.py:13-17` gates this on `not sys.frozen`.

### Editing styles

Edit **`ui/input.css`** (Tailwind v4 source) or `ui/css.css`, then restart the app or run the npx command manually. Do not edit `ui/output.css` by hand — it's generated and committed only so the frozen app needs no build step. If you change Tailwind usage, run `npm install` first to ensure the CLI is available.

### Adding a UI view

1. Add a fragment under `ui/views/` (Alpine.js-based, matching the existing pattern).
2. Wire it into `ui/index.html` and its JS handlers.
3. Add the bridge method to `API` in `api.py` (and service logic under `services/` if needed).
4. Recompile Tailwind if you used new utility classes.

### Adding a Python service

- Follow the existing services pattern: define a class that takes `api` as its constructor arg so it can read shared state (`api.playing`, `api.song_list`, ...) and push to the UI via `api._window.evaluate_js(...)`.
- Export it from `services/__init__.py`.
- Keep `api.py` thin: delegate to the service and return its result.
- New views that play audio must pass a queue `source` (`{type, id}`) to `populate_queue_from_list` so restarts rebuild the right queue; new tables need a `CREATE TABLE IF NOT EXISTS` in both `database.sql` and `Api._ensure_schema`, plus a remote mirror in `sync.py` if they should sync.

## Building the executable (PyInstaller)

```
pip install pyinstaller
pyinstaller music_player.spec
```

Output: `dist/MusicPlayer/`.

The spec bundles `ui/`, `ffmpeg/`, `database.sql`, `node_modules/`, and `pykakasi` data files. Important details:

- **`.env` is not bundled** — it must sit next to `MusicPlayer.exe` (runtime reads `os.path.dirname(sys.executable)`).
- The SQLite DB, `.env`, and library folder all live in `get_data_path()` (`sys.executable` dir) when frozen.
- Console is disabled (`console=False`), so stdout logs are invisible in the shipped app.

> `dist/` and `build/` are git-ignored.

## Code conventions

- **No external comments added to code** — existing files keep their brief `# Region` separators and inline notes but new code follows the quiet style of the codebase.
- Logging to stdout uses `[Python]`, `[Sync]`, `[Debug]` prefixes.
- JSON is passed between Python and JS as serialized strings via `evaluate_js`; bridge methods that receive data from JS accept plain dicts from `pywebview`.

## Contributing flow

1. Create a branch / fork.
2. Make changes, keeping the service-wrapper pattern in `api.py`.
3. Run the app once with the new code; sanity-check downloads, playback, and (if configured) sync.
4. No automated test suite currently exists; manual verification is the norm.
5. Commit with a concise message referencing the feature/version (repo history uses version tags like `0.90`).