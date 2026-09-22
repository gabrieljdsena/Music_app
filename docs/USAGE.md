# Usage Guide

Hathor is a single-window desktop app. The left rail switches between views: **Home**, **Playlists**, **Download Songs**, **History**, and **Settings**.

## Home / Library

The main view lists the MP3 files found in the configured songs folder (`%appdata%\musicPlayer` by default).

- **Play a song** – click it. The queue is populated with the rest of the library from that point on.
- **Queue** – the panel shows upcoming tracks. You can jump to a queue position, remove entries, and reorder them.
- **Play next / Add to queue** – available on each song so you can build a queue without disturbing the current one.
- **Shuffle / Repeat** – toggles in the player bar; shuffle keeps a separate un-shuffled order so repeat/follow on music stays logical.
- **Progress & volume** – seek with the progress slider; the volume slider persists across sessions.
- **Edit metadata** – opens a modal to change title, artist, album, year, and genre, with optional cover art replacement. Metadata is written to the MP3 via mutagen.
- **Search iTunes metadata** – fetch a proposed title/artist/album/cover from the iTunes Search API (single result or multiple candidates to choose from).
- **Delete** – removes the file from the library and the `Songs` table.

> The app only plays `.mp3` files. Drop files into the songs folder and they appear on the next library refresh.

## Playlists

- **New playlist** – name + optional description + optional cover image.
- **Add songs** – from any song, pick one or more playlists to add it to.
- **Open a playlist** – shows its songs; playing one queues the rest of the playlist.
- **Edit / Delete** – rename, change description/cover, or delete. Deletion is recorded for remote sync (see [Database & remote sync](DATABASE.md)).

## Download Songs

- **Search** – type a song name; results come straight from YouTube with title, uploader, duration, and thumbnail.
- **Download** – appended an `"audio"` qualifier and downloads the best audio, converting to **320 kbps MP3** via FFmpeg.
- **Progress** – per-download progress bar updated by `update_download_progress` (0 → 90 % download, 95 % metadata processing, 100 % done).
- **Automatic tagging** – after download, Hathor queries the iTunes Search API and writes ID3 tags plus 600×600 cover art, then renames the file to `Title.mp3` (avoiding collisions with `Title (1).mp3`, etc.).
- **Metadata fallback** – if iTunes lookup fails, the yt-dlp title/uploader are used.

## Lyrics

The lyrics panel shows the current song's lyrics.

- **Synced** – LRC-style timestamps are translated into per-line highlighting as the song plays.
- **Plain** – full-text lyrics if no timed version is available.
- **Local cache** – fetched lyrics are stored in the `Lyrics` table so they work offline.
- **Romanization** – for Japanese tracks, a "romaji" toggle converts kana to Hepburn romanization via `pykakasi` (both for timed and plain lyrics).

## History

Two paginated lists:

- **Downloads** – every song downloaded (from the `Songs.date_download` column).
- **Played** – songs and playlists you've played (`Music_History` and `Playlist_History`), one entry per play event, latest first.

## Settings

- **Songs folder** – browse to a different library folder.
- **Background** – pick a custom background image or remove it.
- **Download limit** – maximum simultaneous downloads.
- **Volume** – default playback volume.

## Windows media keys / overlay

Hathor registers **System Media Transport Controls** via `winsdk` (see `services/windows_media.py`). Media keys (Play/Pause, Next, Previous) on your keyboard control playback, the OS media overlay shows the song title/artist/album art, and its press status stays in sync with the app's play state.

## First-run remote sync

If a `.env` with `DB_HOST` is present, the first launch asks whether to load the library from the remote DB. Choosing **"Yes, load from remote"**:

1. Pulls songs, playlists, lyrics, and history down into the local SQLite DB.
2. Queues downloads for any songs whose files aren't present locally.

Afterwards, normal background sync (every 30 s) keeps both sides in agreement.