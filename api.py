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
    LyricsService
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
        
        self._window = None
        self.downloader = Download.MusicDownloader()
        
        # Initialize services
        self.playback = PlaybackController(self)
        self.metadata = MetadataManager(self)
        self.db = DatabaseManager(self, self.db_path)
        self.media_controls = WindowsMediaOverlay(self)
        self.lyrics = LyricsService(self)
        
    # ==========================
    # Playback & Queue Wrappers
    # ==========================
    def populate_queue(self, current_song):
        return self.playback.populate_queue(current_song)

    def populate_queue_from_list(self, current_song, song_list, playlist_id=None):
        return self.playback.populate_queue_from_list(current_song, song_list, playlist_id)
            
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
        pygame.mixer.music.play(0, float(sec))
        self.playback.current_time_offset = float(sec)
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

    def get_download_history(self, page=1, limit=10):
        return self.db.get_download_history(page, limit)

    def get_played_history(self, page=1, limit=10):
        return self.db.get_played_history(page, limit)

    def sync_local_songs_to_db(self):
        return self.db.sync_local_songs_to_db()

    def sync_remote_to_local_and_download(self):
        return self.db.sync_remote_to_local_and_download()

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

    def search_yt(self, query):
        return self.downloader.search_yt(query)

    def recieve_download(self, data):
        print(f" [Python] Received from JS: {data}")
        
        def progress_callback(d):
            if d['status'] == 'downloading':
                try:
                    total = d.get('total_bytes') or d.get('total_bytes_estimate')
                    downloaded = d.get('downloaded_bytes')
                    if total and downloaded:
                        percent = (downloaded / total) * 90
                        safe_data = json.dumps(data)
                        self._window.evaluate_js(f"if (typeof update_download_progress === 'function') update_download_progress({safe_data}, {percent});")
                except Exception:
                    pass
            elif d['status'] == 'finished':
                safe_data = json.dumps(data)
                self._window.evaluate_js(f"if (typeof update_download_progress === 'function') update_download_progress({safe_data}, 90, 'Processing...');")
            elif d['status'] == 'processing_metadata':
                safe_data = json.dumps(data)
                self._window.evaluate_js(f"if (typeof update_download_progress === 'function') update_download_progress({safe_data}, 95, 'Metadata...');")
            elif d['status'] == 'finished_all':
                safe_data = json.dumps(data)
                self._window.evaluate_js(f"if (typeof update_download_progress === 'function') update_download_progress({safe_data}, 100, 'Done!');")
                
        try:
            search_query = data.get('url') or f"{data.get('title', '')} {data.get('artist', '')} audio".strip() if isinstance(data, dict) else data
            result = self.downloader.download_song(search_query, progress_callback)
            if isinstance(result, dict) and result.get("status") == "success":
                try:
                    with sqlite3.connect(self.db_path) as conn:
                        conn.execute(
                            """INSERT INTO Songs (file, downloaded_link, title, artist, date_download)
                               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                               ON CONFLICT(file) DO UPDATE SET
                                   downloaded_link = excluded.downloaded_link,
                                   title = excluded.title,
                                   artist = excluded.artist,
                                   date_download = CURRENT_TIMESTAMP""",
                            (result["filename"], result.get("source_url"), result["title"], result.get("artist"))
                        )
                except Exception as db_err:
                    print(f" [Python] DB error saving download: {db_err}")
            return result
        except Exception as e:
            print(f" [Python] Error: {e}")
            return f"Error: {e}"

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
            self._window.evaluate_js(f"song_list({json.dumps(self.song_list)})")
    
    def load_settings(self):
        safe_bg = json.dumps(settings.background)
        safe_path = json.dumps(settings.path)
        if self._window:
            self._window.evaluate_js(f"if (typeof window.load_settings === 'function') window.load_settings({settings.volume}, {settings.limit_downloads}, {safe_bg}, {safe_path});")

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
                cursor = conn.cursor().execute("SELECT current_song, current_playlist FROM Settings LIMIT 1")
                data = cursor.fetchone()
                if data and data[0]:
                    file = data[0]
                    playlist_id = data[1]
                    self.playback.current_playlist_id = playlist_id
                    
                    file_path = os.path.join(settings.path, file)
                    song_data = self.metadata.get_song_metadata(file_path, file, include_cover=True)
                    self.play_button(song_data, True)
                    
                    if playlist_id is not None:
                        playlist_songs = self.db.get_playlist_songs(playlist_id)
                        if playlist_songs:
                            self.populate_queue_from_list(song_data, playlist_songs, playlist_id)
                        else:
                            self.populate_queue(song_data)
                    else:
                        self.populate_queue(song_data)
                
        except Exception as e:
            print(f" [Python] Database error: {e}")

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