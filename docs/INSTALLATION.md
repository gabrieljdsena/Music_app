# Installation Guide

This guide covers setting up Hathor from source on Windows. The app targets Windows (WebView2/Edge via pywebview, SMTC media controls, Windows-safe filenames), so macOS/Linux support is not guaranteed.

## Prerequisites

| Tool | Version | Purpose |
| ---- | ------- | ------- |
| Python | 3.10+ (tested on 3.11) | Backend runtime |
| Node.js + npm | 18+ | Tailwind CLI and vendored frontend deps |
| FFmpeg | latest | Audio extraction/conversion to MP3 |

## 1. Clone the repository

```powershell
git clone https://github.com/gabrieljdsena/Music_app.git
cd "Music Player"
```

## 2. Install Python dependencies

```powershell
pip install -r requirements.txt
```

The requirements file installs:

```
webviewpy      # pywebview — native window + JS bridge
mutagen        # ID3/MP3 tag editing
yt_dlp         # YouTube audio downloading
download       # helper dependency for yt_dlp
pygame         # audio playback
winsdk         # Windows.Media (SMTC) overlay + media keys
psutil         # debug resource monitor
pykakasi       # Japanese text → romaji (lyrics)
pymysql        # MySQL/TiDB remote sync
pillow         # image handling for cover art
```

## 3. Install frontend dependencies

```powershell
npm install
```

This pulls the packages referenced by `package.json`:

- `alpinejs` — reactive UI framework (vendored into `ui/cdn.min.js`)
- `sweetalert2` — dialogs (vendored into `ui/sweetalert2/`)
- `notyf` — toasts (vendored into `ui/`)
- `tailwindcss`, `postcss`, `autoprefixer` — stylesheet compilation

> The repo vendors these libraries inside `ui/` and `node_modules/` is only needed in dev for the Tailwind CLI. For a normal dev run you can rely on the vendored copies as long as `npm install` has been run once for the CLI.

## 4. Provide FFmpeg

Download FFmpeg and place `ffmpeg.exe` (and `ffprobe.exe`) in:

```
ffmpeg/bin/
```

`Download.py:68` looks for FFmpeg at `<project>/ffmpeg/bin` (or `<exe-dir>/ffmpeg/bin` in frozen builds). Without FFmpeg, YouTube downloads will fail during audio extraction.

## 5. Run in development

```powershell
python main.py
```

During development (non-frozen) `main.py` first compiles Tailwind:

```powershell
npx @tailwindcss/cli -i "ui\input.css" -o "ui\output.css"
```

then opens the window titled **Hathor**.

### First run

On first run, `main.py` creates the SQLite database (`music_player.db`) from `database.sql`. The app then scans the default library folder:

```
%appdata%\musicPlayer
```

for `.mp3` files. You can point it at another folder later from the settings view (or via `update_songs_path` in the API).

## Troubleshooting

- **Window doesn't open** – make sure WebView2 Runtime is installed (Windows 10/11 usually has it). `pip show webviewpy` to confirm install.
- **Downloads fail** – confirm `ffmpeg/bin` exists and `yt-dlp` is installed (`yt-dlp --version`).
- **Missing JS/CSS styling** – recompile Tailwind with the `npx` command above, and confirm `npm install` was run once so the CLI exists.
- **`pykakasi` errors** – the lyrics service degrades gracefully (romanization disabled) if `pykakasi` is unavailable; nothing else breaks.