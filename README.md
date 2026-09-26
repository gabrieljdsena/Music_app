# Hathor — Music Player

A Windows desktop music player with a native Python backend and a modern web UI. Hathor plays your local MP3 library, downloads songs from YouTube, enriches tags and cover art from iTunes, keeps synced lyrics, and can mirror your library to a MySQL/TiDB database for multi-device sync.

![Platform](https://img.shields.io/badge/platform-Windows-0078d6)
![Python](https://img.shields.io/badge/python-3.10%2B-3776ab)
![License](https://img.shields.io/badge/license-ISC-blue)

## Features

- **Playback & queue** — play/pause, next/previous, seek bar, volume with persistence, shuffle and repeat, plus a reorderable "play next" queue per song or playlist. Restarting rebuilds the queue from where you were listening (playlist, mix, artist, …). Optional equal-power **crossfade** (1–12 s) with gapless handoff.
- **Library management** — scan a local folder for MP3s, edit song metadata (title, artist, album, year, genre, cover art) in place, and delete tracks. Home is a dashboard: full-library view, daily mix, and recently-played/downloaded strips.
- **Daily Mix** — a fresh 50-song mix every day built from your listening history (favorites-weighted with room for discoveries).
- **Podcasts / non-music audio** — a fully separate library (own folder, own tables) with its own view, per-episode menu, metadata editing, and two-way sync. Episodes can share the queue with songs in both directions.
- **YouTube downloads** — search YouTube in-app and download audio as 320 kbps MP3 via `yt-dlp` + FFmpeg, with live progress in the UI. A Songs/Podcast toggle routes each download to the right library (podcast downloads skip the iTunes song-match).
- **Automatic metadata enrichment** — every song download looks up the matching track on the iTunes Search API and writes ID3 tags (`TIT2`, `TPE1`, `TALB`, `TDRC`, `TCON`, `APIC`) and renames the file to the cleaned title.
- **Lyrics** — synced (LRC-style) and plain lyrics fetched per track, cached locally for offline use, with Japanese-to-romaji romanization via `pykakasi` and a track-only fallback for artist-less songs.
- **Playlists** — create/delete playlists, add songs to multiple playlists, attach custom cover art, and play an entire playlist.
- **History** — download history and playback (played songs / played playlists) with pagination.
- **Windows media integration** — System Media Transport Controls (SMTC) via `winsdk` for media keys and OS overlay, including album art and play/pause/next/previous.
- **Visual customization** — set a custom background image (or remove it), window size and position remembered between runs.
- **Remote sync (optional, manual)** — push/pull songs, podcasts, playlists, lyrics, daily mix, and history to a MySQL or TiDB server from the Settings buttons, with tombstone-based deletion propagation and first-run library import.

## Tech stack

| Layer  | Technology |
| ------ | ---------- |
| Shell  | Python + [pywebview](https://pywebview.flowrl.com/) (WebView2/Edge) + `pygame.mixer` for audio |
| UI     | HTML/CSS/JS, [Tailwind CSS](https://tailwindcss.com/), [Alpine.js](https://alpinejs.dev/), [SweetAlert2](https://sweetalert2.github.io/), [Notyf](https://github.com/caroso1222/notyf) |
| Downloads | `yt-dlp`, FFmpeg |
| Metadata | `mutagen` (ID3), iTunes Search API |
| Lyrics  | `pykakasi` (romanization), `urllib` |
| Storage | SQLite (`sqlite3`), optional MySQL/TiDB (`pymysql`) |
| Media keys | `winsdk` (Windows.Media / SMTC) |
| Packaging | PyInstaller |

## Quick start (development)

Requires **Python 3.10+**, **Node.js + npm** (for Tailwind), and **FFmpeg**.

```powershell
# 1. Python dependencies
pip install -r requirements.txt

# 2. Frontend dependencies (Alpine.js, SweetAlert2, Notyf, Tailwind CLI)
npm install

# 3. Place FFmpeg in ./ffmpeg/bin (ffmpeg.exe / ffprobe.exe)

# 4. Run
python main.py
```

On startup in dev mode, `main.py` compiles `ui/input.css` into `ui/output.css` with the Tailwind CLI automatically.

## Building an executable

```powershell
pip install pyinstaller
pyinstaller music_player.spec
```

The build is emitted to `dist/MusicPlayer/`. The `.env` file must live next to the executable (never bundled) when running the frozen build.

## Configuration

All settings are stored in the local `music_player.db` (SQLite) and can be changed from the in-app settings view. Remote sync is opt-in and configured exclusively through a `.env` file next to `main.py` (or the executable):

```env
DB_HOST=your-mysql-or-tidb-host
DB_PORT=4000
DB_USER=user
DB_PASSWORD=password
DB_NAME=dbname
DB_SSL_CA=path/to/ca.pem
```

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for full details.

## Documentation

- [Installation guide](docs/INSTALLATION.md)
- [Configuration (`.env` & settings)](docs/CONFIGURATION.md)
- [Usage guide](docs/USAGE.md)
- [Architecture & code overview](docs/ARCHITECTURE.md)
- [Database schema & remote sync](docs/DATABASE.md)
- [Development & packaging](docs/DEVELOPMENT.md)

## Project layout

```
main.py            App entry point, window creation, startup + first-run flow
api.py             pywebview JS bridge: every method the UI calls
Download.py        yt-dlp YouTube downloader + iTunes metadata/artwork lookup
sync.py            Manual one-shot MySQL/TiDB push (DatabaseSync) + remote schema
database.sql       SQLite schema
settings.py        Loads persisted settings from the SQLite Settings table
monitor.py         Debug RAM usage monitor (currently disabled)
services/          Domain logic: playback (+crossfade), metadata, database, lyrics, windows_media, downloads, apple
ui/                Frontend: views, modals, Tailwind/compiled CSS, vendored libs
music_player.spec  PyInstaller build script
```

## License

ISC — see `package.json`. Third-party assets (`ui/sweetalert2/`, `notyf`, Alpine.js, Tailwind) remain under their respective licenses.