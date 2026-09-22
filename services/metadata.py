import os
import time
import re
import json
import base64
import sqlite3
import pygame
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, APIC
import settings

class MetadataManager:
    def __init__(self, api):
        self.api = api

    def get_song_metadata(self, file_path, file_name, include_cover=False):
        song_data = {
            'File': file_name,
            'Artist': 'Unknown',
            'Title': file_name.replace('.mp3', ''),
            'Album': 'Unknown',
            'Year': 'Unknown',
            'Duration': 0,
            'CoverArt': None,
        }
        if os.path.exists(file_path):
            try:
                song = ID3(file_path)
                song_mp3 = MP3(file_path)
                song_data['Artist'] = str(song.get('TPE1')) if 'TPE1' in song else 'Unknown'
                song_data['Title'] = str(song.get('TIT2')) if 'TIT2' in song else file_name.replace('.mp3', '')
                song_data['Album'] = str(song.get('TALB')) if 'TALB' in song else 'Unknown'
                song_data['Year'] = str(song.get('TDRC')) if 'TDRC' in song else 'Unknown'
                song_data['Duration'] = song_mp3.info.length
                
                if include_cover:
                    for key in song.keys():
                        if key.startswith('APIC'):
                            apic = song[key]
                            img_data = base64.b64encode(apic.data).decode('utf-8')
                            song_data['CoverArt'] = f"data:{apic.mime};base64,{img_data}"
                            break
            except Exception as e:
                print(f" [Python] Failed to load metadata for {file_name}: {e}")
        return song_data

    def get_cover_art_base64(self, file_name):
        file_path = os.path.join(settings.path, file_name)
        if os.path.exists(file_path):
            try:
                song_file = ID3(file_path)
                for key in song_file.keys():
                    if key.startswith('APIC'):
                        apic = song_file[key]
                        img_data = base64.b64encode(apic.data).decode('utf-8')
                        return f"data:{apic.mime};base64,{img_data}"
            except Exception:
                pass
        return None

    def get_local_image_base64(self, file_path):
        import mimetypes
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                    mime_type, _ = mimetypes.guess_type(file_path)
                    if not mime_type:
                        mime_type = "image/jpeg"
                    return f"data:{mime_type};base64,{encoded_string}"
            except Exception as e:
                print(f" [Python] Error loading local image: {e}")
        return None

    def delete_song(self, song_data):
        filename = song_data.get('File')
        total_path = os.path.join(settings.path, filename)
        
        # We need to access api.playback, but playback isn't created yet maybe?
        # Let's rely on api properties or method calls
        last_filename = self.api.last_song.get('File')
        
        if str(last_filename) == str(filename):
            pygame.mixer.music.stop()
            if hasattr(pygame.mixer.music, 'unload'):
                pygame.mixer.music.unload()
            self.api.playing = False
            self.api.first_play = True
            
            if hasattr(self.api, 'media_controls'):
                self.api.media_controls.set_stopped()
                
            if getattr(self.api, '_window', None):
                self.api._window.evaluate_js("if (typeof update_play_button_ui === 'function') { update_play_button_ui(false); }")

        if os.path.exists(total_path):
            try:
                os.remove(total_path)
                self.api.song_list = [s for s in getattr(self.api, 'song_list', []) if s.get('File') != filename]
                
                # Remove the local DB rows so the sync doesn't re-upload the deleted song
                try:
                    with sqlite3.connect(self.api.db_path) as conn:
                        conn.execute("DELETE FROM Song_Playlist WHERE song_file = ?", (filename,))
                        conn.execute("DELETE FROM Lyrics WHERE song_file = ?", (filename,))
                        conn.execute("DELETE FROM Music_History WHERE song_file = ?", (filename,))
                        conn.execute("DELETE FROM Songs WHERE file = ?", (filename,))
                    if getattr(self.api, 'db', None):
                        self.api.db.record_deletion("songs", filename)
                        self.api.db.record_deletion("lyrics", filename)
                        self.api.db.record_deletion("music_history", filename)
                except Exception as db_err:
                    print(f" [Python] Error cleaning up song from database: {db_err}")
                
                # Check playback queue
                if hasattr(self.api, 'playback'):
                    self.api.playback.next_songs = [s for s in getattr(self.api.playback, 'next_songs', []) if s.get('File') != filename]
                    self.api.playback.prev_songs = [s for s in getattr(self.api.playback, 'prev_songs', []) if s.get('File') != filename]
                    queue_json = json.dumps(self.api.playback.next_songs)
                else:
                    self.api.next_songs = [s for s in getattr(self.api, 'next_songs', []) if s.get('File') != filename]
                    self.api.prev_songs = [s for s in getattr(self.api, 'prev_songs', []) if s.get('File') != filename]
                    queue_json = json.dumps(self.api.next_songs)
                    
                if getattr(self.api, '_window', None):
                    self.api._window.evaluate_js(f"window.queue_songs = {queue_json}; if (typeof window.update_queue_ui === 'function') window.update_queue_ui();")
            except Exception as e:
                print(f" [Python] Error deleting file: {e}")

    def update_song_metadata(self, song_data):
        filename = song_data.get('File')
        print(f" [Python] Updating metadata for: {filename}")
        try:
            total_path = os.path.join(settings.path, filename)
            if not os.path.exists(total_path):
                return False

            last_filename = self.api.last_song.get('File')
            is_current_song = (str(last_filename) == str(filename))
            was_playing = False
            resume_pos = 0

            if is_current_song:
                was_playing = getattr(self.api, 'playing', False)
                if hasattr(self.api, 'playback'):
                    resume_pos = self.api.playback.get_current_pos()
                else:
                    resume_pos = self.api.get_current_pos()
                    
                pygame.mixer.music.stop()
                if hasattr(pygame.mixer.music, 'unload'):
                    pygame.mixer.music.unload()

            audio = MP3(total_path)
            if audio.tags is None:
                audio.add_tags()
            
            if 'Title' in song_data:
                audio.tags['TIT2'] = TIT2(encoding=3, text=[song_data['Title']])
            if 'Artist' in song_data:
                audio.tags['TPE1'] = TPE1(encoding=3, text=[song_data['Artist']])
            if 'Album' in song_data:
                audio.tags['TALB'] = TALB(encoding=3, text=[song_data['Album']])
            if 'Year' in song_data:
                audio.tags['TDRC'] = TDRC(encoding=3, text=[str(song_data['Year'])])
            
            if song_data.get('CoverArt'):
                cover_art = song_data['CoverArt']
                if cover_art == 'REMOVE':
                    keys_to_remove = [k for k in audio.tags.keys() if k.startswith('APIC')]
                    for k in keys_to_remove:
                        audio.tags.pop(k)
                else:
                    img_data = None
                    mime = 'image/jpeg'
                
                    if cover_art.startswith('data:'):
                        header, encoded = cover_art.split(',', 1)
                        mime = header.split(';')[0].split(':')[1]
                        img_data = base64.b64decode(encoded)
                    elif cover_art.startswith('http://') or cover_art.startswith('https://'):
                        try:
                            import urllib.request
                            headers = {'User-Agent': 'MyMusicPlayer/1.0'}
                            req = urllib.request.Request(cover_art, headers=headers)
                            with urllib.request.urlopen(req) as response:
                                img_data = response.read()
                                if '.png' in cover_art.lower():
                                    mime = 'image/png'
                                elif '.gif' in cover_art.lower():
                                    mime = 'image/gif'
                        except Exception as e:
                            print(f" [Python] Failed to download artwork from URL {cover_art}: {e}")
                    
                    if img_data:
                        keys_to_remove = [k for k in audio.tags.keys() if k.startswith('APIC')]
                        for k in keys_to_remove:
                            audio.tags.pop(k)

                        audio.tags.add(
                            APIC(
                                encoding=3, mime=mime, type=3, desc=u'Cover', data=img_data
                            )
                        )
            
            audio.save(v2_version=3)
            
            if is_current_song:
                self.api.last_song['Title'] = song_data.get('Title', self.api.last_song.get('Title', 'Unknown'))
                self.api.last_song['Artist'] = song_data.get('Artist', self.api.last_song.get('Artist', 'Unknown'))
                self.api.last_song['Album'] = song_data.get('Album', self.api.last_song.get('Album', 'Unknown'))
                self.api.last_song['Year'] = song_data.get('Year', self.api.last_song.get('Year', 'Unknown'))
                if song_data.get('CoverArt') == 'REMOVE':
                    self.api.last_song['CoverArt'] = None
                elif song_data.get('CoverArt'):
                    self.api.last_song['CoverArt'] = song_data['CoverArt']
                
                safe_song = json.dumps(self.api.last_song)
                if getattr(self.api, '_window', None):
                    self.api._window.evaluate_js(f"""
                        window.current_playing_song = {safe_song}; 
                        if (typeof window.set_active_song_ui === 'function') window.set_active_song_ui(window.current_playing_song); 
                        if (typeof window.playing_view === 'function') window.playing_view(window.current_playing_song);
                        if (typeof window.fetchLyricsForCurrentSong === 'function') window.fetchLyricsForCurrentSong(window.current_playing_song);
                    """)
                
                pygame.mixer.music.load(os.path.join(settings.path, filename))
                pygame.mixer.music.play(0, resume_pos)
                
                if hasattr(self.api, 'playback'):
                    self.api.playback.current_time_offset = resume_pos
                    self.api.playback.last_play_time = time.time()
                else:
                    self.api.current_time_offset = resume_pos
                    self.api.last_play_time = time.time()
                
                if not was_playing:
                    pygame.mixer.music.pause()
                else:
                    self.api.playing = True
                    
                if hasattr(self.api, 'media_controls'):
                    self.api.media_controls.update_overlay(
                        self.api.last_song.get('Title', 'Unknown'),
                        self.api.last_song.get('Artist', 'Unknown'),
                        self.api.last_song.get('CoverArt')
                    )

            # Update backend lists
            lists_to_update = [getattr(self.api, 'song_list', [])]
            if hasattr(self.api, 'playback'):
                lists_to_update.extend([
                    getattr(self.api.playback, 'next_songs', []),
                    getattr(self.api.playback, 'prev_songs', [])
                ])
                if hasattr(self.api.playback, 'unshuffled_song_list'):
                    lists_to_update.append(self.api.playback.unshuffled_song_list)
            else:
                lists_to_update.extend([
                    getattr(self.api, 'next_songs', []),
                    getattr(self.api, 'prev_songs', [])
                ])
                if hasattr(self.api, 'unshuffled_song_list'):
                    lists_to_update.append(self.api.unshuffled_song_list)
            
            for lst in lists_to_update:
                for s in lst:
                    if s.get('File') == filename:
                        if 'Title' in song_data: s['Title'] = song_data['Title']
                        if 'Artist' in song_data: s['Artist'] = song_data['Artist']
                        if 'Album' in song_data: s['Album'] = song_data['Album']
                        if 'Year' in song_data: s['Year'] = song_data['Year']
                        if song_data.get('CoverArt') == 'REMOVE': s['CoverArt'] = None
                        elif song_data.get('CoverArt'): s['CoverArt'] = song_data['CoverArt']

            if getattr(self.api, '_window', None):
                updated_song = next((s for s in getattr(self.api, 'song_list', []) if s.get('File') == filename), song_data)
                # Ensure cover art is included in the update for the UI
                if song_data.get('CoverArt') and song_data['CoverArt'] != 'REMOVE':
                    updated_song['CoverArt'] = song_data['CoverArt']
                elif song_data.get('CoverArt') == 'REMOVE':
                    updated_song['CoverArt'] = None
                safe_updated = json.dumps(updated_song)
                self.api._window.evaluate_js(f"if (typeof window.update_song_row_ui === 'function') window.update_song_row_ui({json.dumps(filename)}, {safe_updated});")
                
                next_queue = getattr(self.api.playback, 'next_songs', []) if hasattr(self.api, 'playback') else getattr(self.api, 'next_songs', [])
                self.api._window.evaluate_js(f"window.queue_songs = {json.dumps(next_queue)}; if (typeof window.update_queue_ui === 'function') window.update_queue_ui();")

            # Database update
            try:
                with sqlite3.connect(self.api.db_path) as conn:
                    conn.execute("UPDATE Songs SET title = ?, artist = ? WHERE file = ?",
                        (song_data.get('Title', ''), song_data.get('Artist', ''), filename))
            except Exception as e:
                print(f" [Python] DB error updating song: {e}")
            return True
        except Exception as e:
            print(f" [Python] Error updating metadata: {str(e)}")
            return False
