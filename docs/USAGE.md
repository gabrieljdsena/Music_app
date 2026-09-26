# Usage Guide

Hathor is a single-window desktop app. The left rail switches between views: **Home**, **Download**, **Podcasts**, **Playlists**, **History**, and **Settings**. Clicking an artist or album name anywhere opens its dedicated page.

## Home dashboard

Home is a dashboard, not a song list:

- **All Songs card** — opens the full library (searchable, sortable). Clicking a song queues the rest of the visible list from that point on.
- **Daily Mix card** — opens your 50-song mix of the day (see below).
- **Recently Played / Recently Downloaded** — horizontal strips. Clicking an episode/song plays it with the strip as its queue. The currently playing item is highlighted in the strip.
- Each card shows live counts (library size, today's mix size/date).

## Daily Mix

A fresh 50-song mix built every day from your listening history (`Music_History` play counts):

- 2 songs from your top 10 most-played,
- 5 from ranks 11–25,
- 13 from ranks 26–70,
- the remaining 30 prefer songs outside your top 70 (discoveries/long tail).

The mix is generated once per day and cached, so it stays stable if you restart. Small libraries just get fewer songs; if the mix can't be built (no songs yet), the view explains what to do.

## All Songs

The main library view lists the MP3 files in the songs folder (`%appdata%\musicPlayer` by default).

- **Play a song** – click it. The queue is populated with the rest of the (visible, i.e. search-filtered) list from that point on.
- **Queue** – the panel shows upcoming tracks. You can jump to a queue position, remove entries, and reorder them.
- **Play next / Add to queue** – available on each song so you can build a queue without disturbing the current one.
- **Shuffle / Repeat** – toggles in the player bar; shuffle keeps a separate un-shuffled order so repeat/follow on music stays logical.
- **Progress & volume** – seek with the progress slider; the volume slider persists across sessions.
- **Edit metadata** – opens a modal to change title, artist, album, year, and genre, with optional cover art replacement. Metadata is written to the MP3 via mutagen. A failed OS overlay update can never fail the save itself.
- **Search iTunes metadata** – fetch a proposed title/artist/album/cover from the iTunes Search API (single result or multiple candidates to choose from).
- **Delete** – removes the file from the library and the `Songs` table.
- **Right-click any song** – opens the same context menu as the ⋮ button, anywhere songs are listed (library, mixes, playlists, artist/album pages, queue).

> The app only plays `.mp3` files. Drop files into the songs folder and they appear on the next library refresh.

## Podcasts / non-music audio

A separate library for podcasts and other spoken audio, kept fully apart from music:

- **Separate folder** – `%appdata%\musicPlayerPodcasts` by default (changeable in Settings). Files here never show up in All Songs, Daily Mix, or Recently Played.
- **Downloading** – the Download view has a **Songs / Podcast-Audio** toggle. Podcast downloads skip the iTunes song-match (which would mangle episode names) and keep the uploader's title/author instead. Failed podcast downloads retry as podcasts.
- **Listing is lightweight** – the view shows filenames/DB titles and dates only. Full metadata (duration, cover) loads on demand: **Get Metadata** in the episode menu, automatically when you press play, or via **Edit Info**.
- **Episode menu** (⋮ button or right-click) – Play, Play Next, Add to Queue, Get Metadata, Edit Info, Delete.
- **No playlists, but queues merge** – episodes queue among themselves, and Play Next / Add to Queue work in both directions, so one queue can mix songs and episodes. Episode plays don't pollute music history, Daily Mix, or Recently Played.
- **Editing** – Edit Info opens the shared metadata modal and writes tags to the episode file plus the `Podcasts` table.
- **Sync** – podcasts sync both ways with the remote DB like songs do (see [Database & remote sync](DATABASE.md)).

## Now Playing & shortcuts

- Click the mini-player to open the **fullscreen Now Playing** view (large art, full controls, volume).
- **Keyboard** – `Space` play/pause, `←`/`→` seek ±10 s, `↑`/`↓` volume, media keys work via the OS integration below.
- **Restart resume** – Hathor remembers the current song *and where it was playing from* (playlist, daily mix, artist, album, recents, podcast). On launch the queue is rebuilt from that context; a customized queue is restored verbatim. Stale contexts fall back gracefully (e.g. yesterday's daily mix → today's mix if the song is still in it, otherwise the general library).

## Playlists

- **New playlist** – name + optional description + optional cover image.
- **Add songs** – from any song, pick one or more playlists to add it to.
- **Open a playlist** – shows its songs; playing one queues the rest of the playlist.
- **Edit / Delete** – rename, change description/cover, or delete. Deletion is recorded for remote sync (see [Database & remote sync](DATABASE.md)).

## Download Songs

- **Search** – type a song name; results come straight from YouTube with title, uploader, duration, and thumbnail.
- **Songs / Podcast-Audio toggle** – downloads land in the music library or the podcasts library respectively (own folder, own table).
- **Download** – appended an `"audio"` qualifier and downloads the best audio, converting to **320 kbps MP3** via FFmpeg.
- **Progress** – per-download progress bar updated by `update_download_progress` (0 → 90 % download, 95 % metadata processing, 100 % done).
- **Automatic tagging** – after a *song* download, Hathor queries the iTunes Search API and writes ID3 tags plus 600×600 cover art, then renames the file to `Title.mp3` (avoiding collisions with `Title (1).mp3`, etc.). Podcast downloads skip this and keep the uploader's title/author.
- **Metadata fallback** – if iTunes lookup fails, the yt-dlp title/uploader are used.
- **Batch import** – import a `.txt` file with one query per line; downloads run with the configured concurrency limit and respect the Songs/Podcast toggle.

## Lyrics

The lyrics panel shows the current song's lyrics.

- **Synced** – LRC-style timestamps are translated into per-line highlighting as the song plays.
- **Plain** – full-text lyrics if no timed version is available.
- **Local cache** – fetched lyrics are stored in the `Lyrics` table so they work offline.
- **Unknown artists** – LRCLIB's exact endpoint requires an artist (it answers those requests with HTTP 400), so artist-less songs fall back to a track-only search that is only accepted on an exact track-name match.
- **Romanization** – for Japanese tracks, a "romaji" toggle converts kana to Hepburn romanization via `pykakasi` (both for timed and plain lyrics).

## History

Two paginated lists:

- **Downloads** – every song downloaded (from the `Songs.date_download` column).
- **Played** – songs and playlists you've played (`Music_History` and `Playlist_History`), one entry per play event, latest first.

## Settings

- **Songs folder** – browse to a different library folder, plus a manual "Sync Now" rescan.
- **Podcasts folder** – same for the podcasts library (browse + rescan).
- **Background** – pick a custom background image or remove it.
- **Download limit** – maximum simultaneous downloads.
- **Volume** – default playback volume.
- **Crossfade** – enable/disable overlap between consecutive tracks (1–12 s, equal-power curve). Applies to automatic transitions; manual next/previous stay instant. Track handoff is gapless whether crossfade is on or off.

## Remote sync (manual)

There is **no background auto-sync**. Both directions run only when you click:

- **Push to Remote** – one-shot upload of songs, podcasts, playlists, lyrics, and history to the remote DB.
- **Sync Remote** (pull) – downloads remote rows into SQLite and queues downloads for missing files (songs → songs folder, podcasts → podcasts folder). Pulling also adopts the remote daily mix of the day.
- **Sync Local Songs to Database** – rescans the songs folder into the DB (see Podcasts folder row for the podcast equivalent).

## Windows media keys / overlay

Hathor registers **System Media Transport Controls** via `winsdk` (see `services/windows_media.py`). Media keys (Play/Pause, Next, Previous) on your keyboard control playback, the OS media overlay shows the song title/artist/album art, and its press status stays in sync with the app's play state.

## First-run remote sync

If a `.env` with `DB_HOST` is present, the first launch asks whether to load the library from the remote DB. Choosing **"Yes, load from remote"**:

1. Pulls songs, podcasts, playlists, lyrics, daily mix, and history down into the local SQLite DB.
2. Queues downloads for any songs/podcasts whose files aren't present locally (each into its own folder).

Afterwards, sync stays fully manual via the Settings buttons above.