import base64
import os
import sqlite3
import json
import pygame
import Download
import settings
from pathlib import Path
import random
import time

from services import (
    PlaybackController,
    MetadataManager,
    DatabaseManager,
    WindowsMediaOverlay,
    LyricsService,
    AppleService,
    DownloadManager,
    startup_maintenance
)

class Api:
    def __init__(self, db_path):
        self.db_path = db_path
        
        # Public state variables accessed by JS or other modules
        self.playing = False
        self.first_play = True
        self.last_song = {'File': None, 'Artist': "", 'Title': "", 'Album': "", 'Year': "", 'Duration': 0, 'CoverArt': None}
        self.current_filename = None
        self.shuffle = False
        self.repeat = False
        self.song_list = []
        # pywebview 6.x `window.state` is a State dict (never the string
        # 'maximized'), so the maximized flag is tracked here instead.
        self._is_maximized = False
        
        self._window = None
        self.downloader = Download.MusicDownloader()
        self._ensure_schema()
        
        # Initialize services
        self.playback = PlaybackController(self)
        self.metadata = MetadataManager(self)
        self.db = DatabaseManager(self, self.db_path)
        self.media_controls = WindowsMediaOverlay(self)
        self.lyrics = LyricsService(self)
        self.apple = AppleService(self)
        self.download_manager = DownloadManager(self)

    def _ensure_schema(self):
        """Add columns/tables introduced after the database was first created."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                for column, ddl in {
                    'queue_songs': 'TEXT',
                    'custom_queue': 'INTEGER DEFAULT 0',
                    'queue_source': 'TEXT',
                    'podcasts_path': 'TEXT',
                    'crossfade_enabled': 'INTEGER DEFAULT 0',
                    'crossfade_seconds': 'REAL DEFAULT 5',
                }.items():
                    try:
                        conn.execute(f"ALTER TABLE Settings ADD COLUMN {column} {ddl}")
                    except sqlite3.OperationalError:
                        pass
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS Download_Queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        qid TEXT UNIQUE,
                        url TEXT,
                        title TEXT,
                        artist TEXT,
                        status TEXT DEFAULT 'queued',
                        progress REAL DEFAULT 0,
                        error TEXT,
                        filename TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS Daily_Mix (
                        mix_date varchar(10) PRIMARY KEY,
                        song_files text not null,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS Podcasts (
                        file varchar(255) PRIMARY KEY,
                        downloaded_link varchar(255),
                        title varchar(255) NOT NULL,
                        date_download DATETIME DEFAULT CURRENT_TIMESTAMP,
                        artist varchar(255)
                    )
                """)
                try:
                    conn.execute("ALTER TABLE Download_Queue ADD COLUMN is_podcast INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
        except Exception as e:
            print(f" [Python] Schema migration error: {e}")
        
    # ==========================
    # Playback & Queue Wrappers
    # ==========================
    def populate_queue(self, current_song):
        return self.playback.populate_queue(current_song)

    def populate_queue_from_list(self, current_song, song_list, playlist_id=None, source=None):
        return self.playback.populate_queue_from_list(current_song, song_list, playlist_id, source)
            
    def jump_to_queue_index(self, index):
        return self.playback.jump_to_queue_index(index)

    def add_to_queue(self, song_data):
        return self.playback.add_to_queue(song_data)

    def next_to_queue(self, song_data):
        return self.playback.next_to_queue(song_data)

    def clear_queue(self):
        return self.playback.clear_queue()
        
    def remove_from_queue(self, index):
        return self.playback.remove_from_queue(index)
            
    def reorder_queue(self, old_index, new_index):
        return self.playback.reorder_queue(old_index, new_index)
    
    def toggle_shuffle(self):
        return self.playback.toggle_shuffle()

    def play_button(self, current_song=None, opening=False):
        return self.playback.play_button(current_song, opening)

    def get_current_pos(self):
        return self.playback.get_current_pos()
    
    def toggle_repeat(self):
        self.repeat = not self.repeat
        return self.repeat

    def play_next(self, auto=False):
        return self.playback.play_next(auto)

    def play_prev(self):
        return self.playback.play_prev()

    def progress_slider_click(self, sec):
        try:
            self.playback._cancel_crossfade()
        except Exception:
            pass
        # Always (re)load through the music module: after a crossfade the
        # module may still hold the previous song.
        try:
            if self.current_filename:
                base = settings.podcasts_path if (self.last_song or {}).get('IsPodcast') else settings.path
                pygame.mixer.music.load(os.path.join(base, str(self.current_filename)))
        except Exception as e:
            print(f" [Python] Could not load song file for seek: {e}")
            return
        pygame.mixer.music.play(0, float(sec))
        self.playback.current_time_offset = float(sec)
        self.playback.last_play_time = time.time()
        if not self.playing:
            pygame.mixer.music.pause()
            self.playback.pause_time = float(sec)

    # ==========================
    # Metadata & Files Wrappers
    # ==========================
    def get_song_metadata(self, file_path, file_name, include_cover=False):
        return self.metadata.get_song_metadata(file_path, file_name, include_cover)

    def get_cover_art_base64(self, file_name):
        return self.metadata.get_cover_art_base64(file_name)

    def get_local_image_base64(self, file_path):
        return self.metadata.get_local_image_base64(file_path)

    def delete_song(self, song_data):
        return self.metadata.delete_song(song_data)

    def update_song_metadata(self, song_data):
        return self.metadata.update_song_metadata(song_data)

    # ==========================
    # Lyrics Wrappers
    # ==========================
    def romanize_text(self, text, is_lrc=False):
        return self.lyrics.romanize_text(text, is_lrc)

    def get_lyrics(self, track_name, artist_name, album_name=None, duration_seconds=None):
        return self.lyrics.get_lyrics(track_name, artist_name, album_name, duration_seconds)

    def search_lyrics(self, track_name, artist_name, album_name=None, duration_seconds=None):
        return self.lyrics.search_lyrics(track_name, artist_name, album_name, duration_seconds)

    def save_lyrics(self, synced, plain):
        return self.lyrics.save_lyrics_for_current(synced, plain)

    def get_artist_image(self, artist):
        return self.apple.get_artist_image(artist)

    def get_album_image(self, album, artist=None):
        return self.apple.get_album_image(album, artist)

    # ==========================
    # Frameless Window Controls
    # ==========================
    def get_window_geometry(self):
        if not self._window:
            return None
        return {
            "x": int(self._window.x or 0),
            "y": int(self._window.y or 0),
            "width": int(self._window.width or 0),
            "height": int(self._window.height or 0),
        }

    def move_window(self, x, y):
        if self._window:
            self._window.move(int(x), int(y))
        return True

    def resize_window(self, width, height, x=None, y=None):
        if not self._window:
            return False
        self._window.resize(int(width), int(height))
        if x is not None and y is not None:
            self._window.move(int(x), int(y))
        return True

    def toggle_maximize_window(self):
        if not self._window:
            return False
        try:
            if self._is_maximized:
                self._window.restore()
            else:
                self._window.maximize()
            self._is_maximized = not self._is_maximized
            return True
        except Exception as e:
            print(f" [Python] toggle_maximize_window error: {e}")
            return False

    def window_action(self, action):
        if not self._window:
            return False
        if action == 'minimize':
            self._window.minimize()
        elif action == 'restore':
            self._window.restore()
        elif action == 'maximize':
            self._window.maximize()
        elif action == 'close':
            self._window.destroy()
        return True

    # ==========================
    # Database Wrappers
    # ==========================
    def load_playlists(self):
        return self.db.load_playlists()

    def new_playlist(self, data):
        return self.db.new_playlist(data)

    def getImage(self, id):
        return self.db.getImage(id)

    def update_playlist(self, playlist_id, data):
        return self.db.update_playlist(playlist_id, data)

    def delete_playlist(self, playlist_id):
        return self.db.delete_playlist(playlist_id)

    def get_song_playlists(self, song_file):
        return self.db.get_song_playlists(song_file)

    def update_song_playlists(self, song_file, song_title, playlist_ids):
        return self.db.update_song_playlists(song_file, song_title, playlist_ids)

    def get_playlist_songs(self, playlist_id):
        return self.db.get_playlist_songs(playlist_id)

    def get_songs_by_artist(self, artist):
        return self.db.get_songs_by_artist(artist)

    def get_songs_by_album(self, album):
        return self.db.get_songs_by_album(album)

    def get_all_songs(self):
        return self.db.get_all_songs()

    def get_podcasts(self):
        return self.db.get_podcasts()

    def get_podcast_details(self, file):
        return self.db.get_podcast_details(file)

    def sync_local_podcasts_to_db(self):
        return self.db.sync_local_podcasts_to_db()

    def delete_podcast(self, song_data):
        return self.metadata.delete_podcast(song_data)

    def get_library_count(self):
        return self.db.get_library_count()

    def get_daily_mix(self):
        return self.db.get_daily_mix()

    def get_recently_played(self, limit=15):
        return self.db.get_recently_played(limit)

    def get_recently_downloaded(self, limit=15):
        return self.db.get_recently_downloaded(limit)

    def get_download_history(self, page=1, limit=10):
        return self.db.get_download_history(page, limit)

    def get_played_history(self, page=1, limit=10):
        return self.db.get_played_history(page, limit)

    def sync_local_songs_to_db(self):
        return self.db.sync_local_songs_to_db()

    def sync_remote_to_local_and_download(self):
        return self.db.sync_remote_to_local_and_download()

    def sync_local_to_remote(self):
        """Manual one-shot push of the local library to the remote DB."""
        if not os.getenv("DB_HOST"):
            return "No remote DB configured. Remote sync unavailable."
        try:
            from sync import DatabaseSync
            return DatabaseSync(self.db_path).sync_once()
        except Exception as e:
            print(f" [Python] Push sync error: {e}")
            return f"Error: {e}"

    # ==========================
    # Native Application Logic
    # ==========================
    def volume_slider(self, volume):
        pygame.mixer.music.set_volume(float(volume))
        settings.volume = float(volume)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("UPDATE Settings SET current_volume = ?", (volume,))
        except Exception as e:
            print(f" [Python] Database error: {e}")
        # Keep the crossfade channel in sync when it is the active output
        try:
            self.playback.sync_output_volume()
        except Exception:
            pass

    def get_playback_settings(self):
        """Crossfade prefs for the Settings UI."""
        try:
            enabled = bool(getattr(settings, 'crossfade_enabled', False))
        except Exception:
            enabled = False
        try:
            seconds = float(getattr(settings, 'crossfade_seconds', 5) or 0)
        except (TypeError, ValueError):
            seconds = 5.0
        return {'crossfade_enabled': enabled, 'crossfade_seconds': seconds}

    def set_crossfade(self, enabled, seconds):
        """Persist crossfade prefs and apply them live."""
        if isinstance(enabled, str):
            enabled = enabled.lower() in ('1', 'true', 'on', 'yes')
        else:
            enabled = bool(enabled)
        try:
            seconds = max(0.0, min(12.0, float(seconds)))
        except (TypeError, ValueError):
            seconds = 5.0
        settings.crossfade_enabled = enabled
        settings.crossfade_seconds = seconds
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE Settings SET crossfade_enabled = ?, crossfade_seconds = ?",
                    (int(enabled), seconds),
                )
        except Exception as e:
            print(f" [Python] Database error: {e}")
        try:
            self.playback.crossfade_enabled = enabled
            self.playback.crossfade_seconds = seconds
        except Exception:
            pass
        if enabled and seconds > 0:
            return f"Crossfade on ({seconds:g}s). Gapless handoff always on."
        return "Crossfade off. Gapless handoff always on."

    def update_download_limit(self, limit):
        settings.limit_downloads = str(limit)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("UPDATE Settings SET limit_downloads = ?", (limit,))
        except Exception as e:
            print(f" [Python] Database error: {e}")

    def update_background(self, background):
        settings.background = str(background)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("UPDATE Settings SET background_path = ?", (background,))
        except Exception as e:
            print(f" [Python] Database error: {e}")

    def remove_background(self):
        settings.background = None
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("UPDATE Settings SET background_path = ?", (settings.background,))
            return True
        except Exception as e:
            print(f" [Python] Database error: {e}")
            return False

    def update_songs_path(self, songs_path):
        settings.path = str(songs_path)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("UPDATE Settings SET songs_path = ?", (songs_path,))
        except Exception as e:
            print(f" [Python] Database error: {e}")

    def update_podcasts_path(self, podcasts_path):
        settings.podcasts_path = str(podcasts_path)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("UPDATE Settings SET podcasts_path = ?", (podcasts_path,))
        except Exception as e:
            print(f" [Python] Database error: {e}")

    def search_yt(self, query):
        return self.downloader.search_yt(query)

    # ==========================
    # Startup Maintenance
    # ==========================
    def get_ffmpeg_status(self):
        """Non-blocking status check for the settings/startup UI."""
        try:
            return startup_maintenance.find_ffmpeg()
        except Exception as e:
            return {'found': False, 'exe': None, 'dir': None, 'error': str(e)}

    def get_library_status(self):
        """Read-only yt-dlp / youtube-lib version comparison (no installs)."""
        try:
            return startup_maintenance.check_libraries_status()
        except Exception as e:
            print(f" [Python] Library status error: {e}")
            return []

    def run_startup_maintenance(self):
        """Blocking re-run of the startup checks (Retry button). Pushes progress to UI."""
        try:
            window = self._window

            def push(event):
                if not window:
                    return
                try:
                    window.evaluate_js(
                        "if (window.startupToast && window.startupToast.update) "
                        f"window.startupToast.update({json.dumps(event)});"
                    )
                except Exception:
                    pass

            try:
                if window:
                    window.evaluate_js(
                        "if (window.startupToast && window.startupToast.show) "
                        "window.startupToast.show('Checking for updates…');"
                    )
            except Exception:
                pass
            summary = startup_maintenance.run_startup_checks(push)
            try:
                if window:
                    window.evaluate_js(
                        "if (window.startupToast && window.startupToast.complete) "
                        f"window.startupToast.complete({json.dumps(summary)});"
                    )
            except Exception:
                pass
            return summary
        except Exception as e:
            print(f" [Python] Startup maintenance error: {e}")
            return {'ffmpeg': {'status': 'error', 'error': str(e)},
                    'libraries': [], 'all_ok': False}

    def recieve_download(self, data):
        print(f" [Python] Received from JS: {data}")
        return self.download_manager.submit(data, block=True)

    def get_download_jobs(self, limit=15):
        return self.download_manager.get_jobs(limit)

    def retry_download(self, data):
        return self.download_manager.retry(data)

    def send_song_list(self):
        path = Path(settings.path)
        files = [f.name for f in path.iterdir() if f.is_file() and f.suffix.lower() == '.mp3']
        
        date_lookup = {}
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT file, date_download FROM Songs")
                for row in cursor.fetchall():
                    if row[1]:
                        date_lookup[row[0]] = row[1].replace(' ', 'T') + 'Z' if 'T' not in str(row[1]) else str(row[1])
                    else:
                        date_lookup[row[0]] = None
        except Exception as e:
            print(f" [Python] Error fetching date_download: {e}")
        
        local_list = []
        for file in files:
            file_path = str(path / file)
            song_data = self.metadata.get_song_metadata(file_path, file)
            song_data['DateDownload'] = date_lookup.get(file, None)
            local_list.append(song_data)
        self.song_list = local_list
        if self._window:
            try:
                self._window.evaluate_js(f"if (typeof window.song_list === 'function') window.song_list({json.dumps(self.song_list)})")
            except Exception as e:
                print(f" [Python] song_list JS error (page may not be ready yet): {e}")
    
    def load_settings(self):
        safe_bg = json.dumps(settings.background)
        safe_path = json.dumps(settings.path)
        safe_podcasts = json.dumps(getattr(settings, 'podcasts_path', ''))
        if self._window:
            self._window.evaluate_js(f"if (typeof window.load_settings === 'function') window.load_settings({settings.volume}, {settings.limit_downloads}, {safe_bg}, {safe_path}, {safe_podcasts});")

    def load_current_song(self):
        try:
            if not self.song_list:
                path = Path(settings.path)
                if path.exists():
                    files = [f.name for f in path.iterdir() if f.is_file() and f.suffix.lower() == '.mp3']
                    local_list = []
                    for file in files:
                        file_path = str(path / file)
                        local_list.append(self.metadata.get_song_metadata(file_path, file))
                    if not self.song_list:
                        self.song_list = local_list

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor().execute("SELECT current_song, current_playlist, queue_source FROM Settings LIMIT 1")
                data = cursor.fetchone()
                if data and data[0]:
                    file = data[0]
                    playlist_id = data[1]
                    try:
                        source = json.loads(data[2]) if len(data) > 2 and data[2] else None
                    except Exception:
                        source = None
                    # Legacy rows predate queue_source: a stored playlist id
                    # still means "playing from that playlist".
                    if not isinstance(source, dict) and playlist_id is not None:
                        source = {'type': 'playlist', 'id': playlist_id}
                    if isinstance(source, dict):
                        self.playback.current_playlist_id = (
                            source.get('id') if source.get('type') == 'playlist' else None
                        )
                    else:
                        self.playback.current_playlist_id = playlist_id
                    
                    base_path = settings.podcasts_path if isinstance(source, dict) and source.get('type') == 'podcast' else settings.path
                    file_path = os.path.join(base_path, file)
                    song_data = self.metadata.get_song_metadata(file_path, file, include_cover=True)
                    if isinstance(source, dict) and source.get('type') == 'podcast':
                        song_data['IsPodcast'] = True
                    self.play_button(song_data, True)
                    
                    if not self.playback.restore_saved_queue(song_data):
                        if not self._rebuild_queue_from_source(song_data, file, source):
                            self.populate_queue(song_data)
                
        except Exception as e:
            print(f" [Python] Database error: {e}")

    def _rebuild_queue_from_source(self, song_data, file, source):
        """Rebuild the queue from the persisted playback context.

        Returns True when the queue was rebuilt (current song found in the
        source list), False to let the caller fall back to the general list.
        A stale source (deleted playlist, reset daily mix, renamed artist)
        simply misses and falls back.
        """
        try:
            if not isinstance(source, dict):
                return False
            stype = source.get('type')
            sid = source.get('id')
            songs = None
            playlist_id = None
            refresh_source = source

            if stype == 'playlist':
                try:
                    playlist_id = int(sid)
                except (TypeError, ValueError):
                    return False
                songs = self.db.get_playlist_songs(playlist_id)
            elif stype == 'daily_mix':
                # The mix resets every day: rebuild from TODAY's mix. If the
                # song isn't in it anymore, the caller falls back gracefully.
                mix = self.db.get_daily_mix()
                songs = mix.get('songs', []) if isinstance(mix, dict) else []
                refresh_source = {'type': 'daily_mix', 'id': mix.get('date') if isinstance(mix, dict) else None}
            elif stype == 'artist' and sid:
                songs = self.db.get_songs_by_artist(str(sid))
            elif stype == 'album' and sid:
                songs = self.db.get_songs_by_album(str(sid))
            elif stype == 'recently_played':
                songs = self.db.get_recently_played()
            elif stype == 'recently_downloaded':
                songs = self.db.get_recently_downloaded()
            elif stype == 'podcast' and file:
                # Single-episode queue (no playlists/queue for podcasts). The
                # episode may be gone (folder moved, file deleted) -> fall back.
                pod_path = os.path.join(settings.podcasts_path, file)
                if os.path.exists(pod_path):
                    song_data = self.metadata.get_song_metadata(pod_path, file)
                    song_data['IsPodcast'] = True
                    self.populate_queue_from_list(song_data, [song_data], None, source)
                    return True
                return False
            elif stype == 'all_songs':
                return False
            else:
                return False

            if not songs or not any(s.get('File') == file for s in songs):
                return False
            self.populate_queue_from_list(song_data, songs, playlist_id, refresh_source)
            return True
        except Exception as e:
            print(f" [Python] Queue rebuild error: {e}")
            return False

    def js_log(self, message):
        """Receive log lines from the frontend (window.onerror /
        unhandledrejection forwarders). Written to hathor.log."""
        try:
            import logging
            logging.getLogger('hathor').info("JS: %s", str(message)[:2000])
        except Exception:
            pass
        return True

    def get_current_song_ui_state(self):
        """Let the frontend pull the current song when it's ready.

        Covers the startup race where Python pushes `playing_view` /
        `plaiyng_info` before the webview DOM has parsed its scripts
        (which previously surfaced as `ReferenceError: playing_view
        is not defined`). The UI calls this on startup and renders
        locally if the early push was skipped.
        """
        try:
            song = getattr(self, 'last_song', None)
            if not song or not song.get('File'):
                return {'song': None, 'is_playing': False}
            return {'song': song, 'is_playing': bool(getattr(self, 'playing', False))}
        except Exception as e:
            print(f" [Python] get_current_song_ui_state error: {e}")
            return {'song': None, 'is_playing': False}

    def search_itunes_metadata(self, title, artist=None):
        try:
            artist_str = artist if artist and artist not in ['Unknown', 'Unknown Artist'] else None
            metadata = self.downloader.search_itunes(title, artist_str)
            if metadata:
                if metadata.get('artwork'):
                    img_data = base64.b64encode(metadata['artwork']).decode('utf-8')
                    metadata['artwork'] = f"data:image/jpeg;base64,{img_data}"
                return metadata
        except Exception as e:
            print(f" [Python] iTunes Search Error: {str(e)}")
        return None

    def search_itunes_metadata_multi(self, title, artist=None):
        """Search iTunes and return multiple metadata options for user selection."""
        try:
            artist_str = artist if artist and artist not in ['Unknown', 'Unknown Artist'] else None
            results = self.downloader.search_itunes_multi(title, artist_str)
            return results if results else []
        except Exception as e:
            print(f" [Python] iTunes Multi-Search Error: {str(e)}")
            return []

    def pick_folder(self):
        import webview
        if self._window:
            result = self._window.create_file_dialog(
                webview.FOLDER_DIALOG,
                allow_multiple=False
            )
            if result and len(result) > 0:
                self.update_songs_path(result[0])
                return result[0]
        return None

    def pick_podcasts_folder(self):
        import webview
        if self._window:
            result = self._window.create_file_dialog(
                webview.FOLDER_DIALOG,
                allow_multiple=False
            )
            if result and len(result) > 0:
                self.update_podcasts_path(result[0])
                return result[0]
        return None

    def pick_background(self):
        import webview
        if self._window:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=('Image Files (*.bmp;*.jpg;*.jpeg;*.gif;*.png)', 'All files (*.*)')
            )
            if result and len(result) > 0:
                self.update_background(result[0])
                return result[0]
        return None

    def pick_playlist_image(self):
        import webview
        if self._window:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=('Image Files (*.bmp;*.jpg;*.jpeg;*.gif;*.png)', 'All files (*.*)')
            )
            if result and len(result) > 0:
                return self.get_local_image_base64(result[0])
        return None