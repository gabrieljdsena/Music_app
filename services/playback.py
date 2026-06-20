import os
import json
import random
import time
import sqlite3
import pygame
import settings
from mutagen.id3 import ID3
import base64

class PlaybackController:
    def __init__(self, api):
        self.api = api
        self.next_songs = []
        self.prev_songs = []
        self.unshuffled_song_list = []
        self.current_playlist_id = None
        
        self.current_time_offset = 0
        self.last_play_time = 0
        self.pause_time = 0
        self.fallback_to_general_list = True

    def populate_queue(self, current_song):
        self.next_songs.clear()
        self.fallback_to_general_list = True
        self.prev_songs.clear()
        self.current_playlist_id = None
        self.unshuffled_song_list = list(self.api.song_list)

        found = False
        for song in self.api.song_list:
            if found:
                self.next_songs.append(song)
            elif song.get('File') == current_song.get('File'):
                found = True
            else:
                self.prev_songs.append(song)

        if not found and len(self.api.song_list) > 0:
            self.next_songs = list(self.api.song_list)
            self.prev_songs.clear()

        try:
            with sqlite3.connect(self.api.db_path) as conn:
                conn.execute("UPDATE Settings SET current_playlist = NULL")
        except Exception as e:
            print(f" [Python] Database error clearing current_playlist: {e}")

        if getattr(self.api, '_window', None):
            self.api._window.evaluate_js(f"""
                window.is_custom_queue = false;
                window.queue_songs = {json.dumps(self.next_songs)};
                if (typeof window.update_queue_ui === 'function') {{
                    window.update_queue_ui();
                }}
            """)

    def populate_queue_from_list(self, current_song, song_list, playlist_id=None):
        self.next_songs.clear()
        self.fallback_to_general_list = False
        self.prev_songs.clear()
        self.current_playlist_id = playlist_id
        self.unshuffled_song_list = list(song_list)

        found = False
        for song in song_list:
            if found:
                self.next_songs.append(song)
            elif song.get('File') == current_song.get('File'):
                found = True
            else:
                self.prev_songs.append(song)

        try:
            with sqlite3.connect(self.api.db_path) as conn:
                conn.execute("UPDATE Settings SET current_playlist = ?", (playlist_id,))
        except Exception as e:
            print(f" [Python] Database error saving current_playlist: {e}")
                
        if getattr(self.api, '_window', None):
            self.api._window.evaluate_js(f"""
                window.is_custom_queue = false;
                window.queue_songs = {json.dumps(self.next_songs)};
                if (typeof window.update_queue_ui === 'function') {{
                    window.update_queue_ui();
                }}
            """)
            
    def jump_to_queue_index(self, index):
        if 0 <= index < len(self.next_songs):
            self.next_songs = self.next_songs[index+1:]

    def add_to_queue(self, song_data):
        self.next_songs.append(song_data)
        if getattr(self.api, '_window', None):
            self.api._window.evaluate_js(f"""
                window.queue_songs = {json.dumps(self.next_songs)};
                if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
            """)

    def next_to_queue(self, song_data):
        self.next_songs.insert(0, song_data)
        if getattr(self.api, '_window', None):
            self.api._window.evaluate_js(f"""
                window.queue_songs = {json.dumps(self.next_songs)};
                if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
            """)

    def clear_queue(self):
        self.next_songs.clear()
        self.fallback_to_general_list = False
        if getattr(self.api, '_window', None):
            self.api._window.evaluate_js(f"""
                window.queue_songs = [];
                if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
            """)
        
    def remove_from_queue(self, index):
        if 0 <= index < len(self.next_songs):
            self.next_songs.pop(index)
            if getattr(self.api, '_window', None):
                self.api._window.evaluate_js(f"""
                    window.queue_songs = {json.dumps(self.next_songs)};
                    if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                """)
            
    def reorder_queue(self, old_index, new_index):
        if 0 <= old_index < len(self.next_songs) and 0 <= new_index <= len(self.next_songs):
            item = self.next_songs.pop(old_index)
            self.next_songs.insert(new_index, item)
            if getattr(self.api, '_window', None):
                self.api._window.evaluate_js(f"""
                    window.queue_songs = {json.dumps(self.next_songs)};
                    if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                """)
    
    def toggle_shuffle(self):
        self.api.shuffle = not getattr(self.api, 'shuffle', False)
        if self.api.shuffle and self.next_songs:
            random.shuffle(self.next_songs)
            if getattr(self.api, '_window', None):
                self.api._window.evaluate_js(f"""
                    window.queue_songs = {json.dumps(self.next_songs)};
                    if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                """)
        elif not self.api.shuffle:
            if self.api.last_song and self.api.last_song.get('File'):
                if self.unshuffled_song_list:
                    self.populate_queue_from_list(self.api.last_song, self.unshuffled_song_list, self.current_playlist_id)
                elif self.current_playlist_id is not None:
                    playlist_songs = self.api.db.get_playlist_songs(self.current_playlist_id)
                    if playlist_songs:
                        self.populate_queue_from_list(self.api.last_song, playlist_songs, self.current_playlist_id)
                    else:
                        self.populate_queue(self.api.last_song)
                else:
                    self.populate_queue(self.api.last_song)
        return self.api.shuffle

    def get_current_pos(self):
        if self.api.first_play:
            return 0
            
        if not self.api.playing:
            return self.pause_time
                    
        pos = pygame.mixer.music.get_pos()
        
        if pos == -1:
            if self.api.playing and (time.time() - self.last_play_time > 3.0):
                self.play_next(auto=True)
            return 0
            
        return self.current_time_offset + (pos / 1000.0)

    def play_button(self, current_song=None, opening=False):
        if current_song is None:
            if self.api.first_play:
                if self.api.current_filename and os.path.exists(os.path.join(settings.path, str(self.api.current_filename))):
                    try:
                        pygame.mixer.music.load(os.path.join(settings.path, str(self.api.current_filename)))
                        pygame.mixer.music.play()
                        self.api.playing = True
                        self.api.first_play = False
                        if hasattr(self.api, 'media_controls'):
                            self.api.media_controls.set_playing(True)
                        return True
                    except Exception as e:
                        print(f" [Python] Force play error: {e}")
                return None
            if self.api.playing:
                self.pause_time = self.get_current_pos()
                pygame.mixer.music.pause()
                self.api.playing = False
                if hasattr(self.api, 'media_controls'):
                    self.api.media_controls.set_playing(False)
            else:
                if pygame.mixer.music.get_pos() == -1:
                    pygame.mixer.music.play()
                    self.current_time_offset = 0
                else:
                    pygame.mixer.music.unpause()
                self.api.playing = True
                if hasattr(self.api, 'media_controls'):
                    self.api.media_controls.set_playing(True)
            return self.api.playing

        self.api.current_filename = current_song.get('File')
        last_filename = self.api.last_song.get('File')
        
        if 'CoverArt' not in current_song or not current_song['CoverArt']:
            file_path = os.path.join(settings.path, self.api.current_filename)
            if os.path.exists(file_path):
                try:
                    song_file = ID3(file_path)
                    for key in song_file.keys():
                        if key.startswith('APIC'):
                            apic = song_file[key]
                            img_data = base64.b64encode(apic.data).decode('utf-8')
                            current_song['CoverArt'] = f"data:{apic.mime};base64,{img_data}"
                            break
                except Exception:
                    pass

        if str(last_filename) != str(self.api.current_filename):
            pygame.mixer.music.stop()
            self.api.first_play = True
            self.current_time_offset = 0
        else:
            last_instance = self.api.last_song.get('_instanceId') or self.api.last_song.get('_historyId')
            curr_instance = current_song.get('_instanceId') or current_song.get('_historyId')
            if last_instance and curr_instance and str(last_instance) != str(curr_instance):
                pygame.mixer.music.stop()
                self.api.first_play = True
                self.current_time_offset = 0

        if self.api.first_play:
            if getattr(self.api, '_window', None):
                self.api._window.evaluate_js(f"playing_view({json.dumps(current_song)})")
            pygame.mixer.music.load(os.path.join(settings.path, str(self.api.current_filename)))

            if getattr(self.api, '_window', None):
                self.api._window.evaluate_js(f"window.plaiyng_info({json.dumps(current_song)})")

            pygame.mixer.music.set_volume(settings.volume)
            pygame.mixer.music.play()
            self.current_time_offset = 0
            self.last_play_time = time.time()
            
            if opening:
                pygame.mixer.music.pause()
                self.api.playing = False
                self.pause_time = 0
                if hasattr(self.api, 'media_controls'):
                    self.api.media_controls.set_playing(False)
                if getattr(self.api, '_window', None):
                    self.api._window.evaluate_js("if (typeof update_play_button_ui === 'function') { update_play_button_ui(false); }")
                    self.api._window.evaluate_js("if (typeof stop_visualizer === 'function') { stop_visualizer(); }")
            else:
                self.api.playing = True
                if hasattr(self.api, 'media_controls'):
                    self.api.media_controls.set_playing(True)
                if getattr(self.api, '_window', None):
                    self.api._window.evaluate_js("if (typeof update_play_button_ui === 'function') { update_play_button_ui(true); }")

            try:
                with sqlite3.connect(self.api.db_path) as conn:
                    conn.execute("UPDATE Settings SET current_song = ?", (self.api.current_filename,))
                    if not opening:
                        conn.execute("INSERT INTO Music_History (song_file) VALUES (?)", (self.api.current_filename,))
                        if self.current_playlist_id is not None:
                            conn.execute("INSERT INTO Playlist_History (playlist_id) VALUES (?)", (self.current_playlist_id,))
            except Exception as e:
                print(f" [Python] Database error: {e}")
            
            if hasattr(self.api, 'media_controls'):
                self.api.media_controls.update_overlay(
                    current_song.get('Title', 'Unknown'),
                    current_song.get('Artist', 'Unknown'),
                    current_song.get('CoverArt')
                )

        elif self.api.playing:
            self.pause_time = self.get_current_pos()
            pygame.mixer.music.pause()
            self.api.playing = False
            if hasattr(self.api, 'media_controls'):
                self.api.media_controls.set_playing(False)
        else:
            if pygame.mixer.music.get_pos() == -1:
                pygame.mixer.music.play()
                self.current_time_offset = 0
                self.last_play_time = time.time()
            else:
                pygame.mixer.music.unpause()
            self.api.playing = True
            if hasattr(self.api, 'media_controls'):
                self.api.media_controls.set_playing(True)

        self.api.last_song = current_song
        self.api.first_play = False
        return self.api.playing

    def play_next(self, auto=False):
        if getattr(self.api, 'repeat', False) and auto and self.api.last_song and self.api.last_song.get('File'):
            pygame.mixer.music.stop()
            self.api.first_play = True
            self.current_time_offset = 0
            self.play_button(self.api.last_song)
            return

        if self.next_songs:
            next_song = self.next_songs.pop(0)
            if self.api.last_song and self.api.last_song.get('File'):
                self.prev_songs.append(self.api.last_song)
            
            if getattr(self.api, '_window', None):
                self.api._window.evaluate_js(f"""
                    if (window.queue_songs && window.queue_songs.length > 0) {{
                        window.queue_songs.shift();
                        if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                    }}
                    window.current_playing_song = {json.dumps(next_song)};
                    if (typeof window.set_active_song_ui === 'function') {{ window.set_active_song_ui(window.current_playing_song); }}
                """)
            self.play_button(next_song)
        else:
            self.api.playing = False
            self.pause_time = 0
            if hasattr(self.api, 'media_controls'):
                self.api.media_controls.set_stopped()
            self.current_time_offset = 0
            if getattr(self.api, '_window', None):
                self.api._window.evaluate_js("if (typeof update_play_button_ui === 'function') { update_play_button_ui(false); }")

    def play_prev(self):
        if self.prev_songs:
            prev_song = self.prev_songs.pop()
            
            if self.api.last_song and self.api.last_song.get('File'):
                self.next_songs.insert(0, self.api.last_song)
                
            if getattr(self.api, '_window', None):
                self.api._window.evaluate_js(f"""
                    if (window.queue_songs && window.current_playing_song) {{
                        window.queue_songs.unshift(window.current_playing_song);
                        if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                    }}
                    window.current_playing_song = {json.dumps(prev_song)};
                    if (typeof window.set_active_song_ui === 'function') {{ window.set_active_song_ui(window.current_playing_song); }}
                """)
                
            self.play_button(prev_song)
        else:
            prev_song = None
            if self.api.current_filename and getattr(self.api, 'song_list', None):
                for i, song in enumerate(self.api.song_list):
                    if song.get('File') == self.api.current_filename:
                        if i - 1 >= 0:
                            prev_song = self.api.song_list[i - 1]
                        break
            
            if prev_song:
                if self.api.last_song and self.api.last_song.get('File'):
                    self.next_songs.insert(0, self.api.last_song)
                    
                if getattr(self.api, '_window', None):
                    self.api._window.evaluate_js(f"""
                        if (window.queue_songs && window.current_playing_song) {{
                            window.queue_songs.unshift(window.current_playing_song);
                            if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                        }}
                        window.current_playing_song = {json.dumps(prev_song)}; 
                        if (typeof window.set_active_song_ui === 'function') {{ window.set_active_song_ui(window.current_playing_song); }}
                    """)
                self.play_button(prev_song)
            else:
                pygame.mixer.music.play(0, 0.0)
                self.current_time_offset = 0
                self.last_play_time = time.time()
                if not self.api.playing:
                    pygame.mixer.music.pause()
