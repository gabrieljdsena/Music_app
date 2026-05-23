import os
import sqlite3
import base64
import json
import pygame
import Download
import settings
from pathlib import Path
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, APIC
import winsdk.windows.media as media
import winsdk.windows.media.playback as playback
import random
import time
import urllib.request
import urllib.parse
import re

try:
    import pykakasi
    kks = pykakasi.kakasi()
except ImportError:
    kks = None
    print(" [Python] 'pykakasi' not found. Run 'pip install pykakasi' to enable Romaji lyrics.")

class Api:

    def __init__(self, db_path):
        self.db_path = db_path
        
        #public variables
        self.playing = False
        self.first_play = True
        self.last_song = {'File': None, 'Artist': "", 'Title': "", 'Album': "", 'Year': "", 'Duration': 0, 'CoverArt': None}
        self.current_time_offset = 0
        self.current_filename = None
        self.last_play_time = 0
        self.fallback_to_general_list = True
        self.pause_time = 0
        self.shuffle = False

        #queue
        self.song_list = []
        self.next_songs = []
        self.prev_songs = []
        
        self._window = None
        self.downloader = Download.MusicDownloader()
        
        #winsdk
        self._media_player = playback.MediaPlayer()
        self._smtc = self._media_player.system_media_transport_controls
        self._smtc.is_play_enabled = True
        self._smtc.is_pause_enabled = True
        self._smtc.is_next_enabled = True
        self._smtc.is_previous_enabled = True
        
        #winsdk
        self._smtc.add_button_pressed(self.on_smtc_button_pressed)
        
    def populate_queue(self, current_song):
        self.next_songs.clear()
        self.fallback_to_general_list = True
        self.prev_songs.clear()

        found = False
        for song in self.song_list:
            if found:
                self.next_songs.append(song)
            elif song.get('File') == current_song.get('File'):
                found = True
            else:
                self.prev_songs.append(song)
                
        if self._window:
            self._window.evaluate_js(f"""
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
        if self._window:
            self._window.evaluate_js(f"""
                window.queue_songs = {json.dumps(self.next_songs)};
                if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
            """)

    def next_to_queue(self, song_data):
        self.next_songs.insert(0, song_data)
        if self._window:
            self._window.evaluate_js(f"""
                window.queue_songs = {json.dumps(self.next_songs)};
                if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
            """)

    def clear_queue(self):
        self.next_songs.clear()
        self.fallback_to_general_list = False
        if self._window:
            self._window.evaluate_js(f"""
                window.queue_songs = [];
                if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
            """)
        
    def remove_from_queue(self, index):
        if 0 <= index < len(self.next_songs):
            self.next_songs.pop(index)
            if self._window:
                self._window.evaluate_js(f"""
                    window.queue_songs = {json.dumps(self.next_songs)};
                    if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                """)
            
    def reorder_queue(self, old_index, new_index):
        if 0 <= old_index < len(self.next_songs) and 0 <= new_index <= len(self.next_songs):
            item = self.next_songs.pop(old_index)
            self.next_songs.insert(new_index, item)
            if self._window:
                self._window.evaluate_js(f"""
                    window.queue_songs = {json.dumps(self.next_songs)};
                    if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                """)
    
    def toggle_shuffle(self):
        self.shuffle = not getattr(self, 'shuffle', False)
        if self.shuffle and self.next_songs:
            random.shuffle(self.next_songs)
            if self._window:
                self._window.evaluate_js(f"""
                    window.queue_songs = {json.dumps(self.next_songs)};
                    if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                """)
        elif not self.shuffle:
            if self.last_song and self.last_song.get('File'):
                self.populate_queue(self.last_song)
        return self.shuffle
        


    #winsdk comunication
    def on_smtc_button_pressed(self, sender, args):
        #winsdk
        if args.button == media.SystemMediaTransportControlsButton.PLAY or args.button == media.SystemMediaTransportControlsButton.PAUSE:
            is_playing = self.play_button(None)
            if self._window and is_playing is not None:
                self._window.evaluate_js(f"update_play_button_ui({'true' if is_playing else 'false'});")
                
        #winsdk
        elif args.button == media.SystemMediaTransportControlsButton.NEXT:
            self.play_next()
        elif args.button == media.SystemMediaTransportControlsButton.PREVIOUS:
            self.play_prev()


    #play button click / song div click handler
    def play_button(self, current_song=None, opening=False):

        if current_song is None:
            if self.first_play:
                return None
            if self.playing:
                self.pause_time = self.get_current_pos()
                pygame.mixer.music.pause()
                self.playing = False
                self._smtc.playback_status = media.MediaPlaybackStatus.PAUSED
            else:
                if pygame.mixer.music.get_pos() == -1:
                    pygame.mixer.music.play()
                    self.current_time_offset = 0
                else:
                    pygame.mixer.music.unpause()
                self.playing = True
                self._smtc.playback_status = media.MediaPlaybackStatus.PLAYING
            return self.playing

        self.current_filename = current_song.get('File')
        last_filename = self.last_song.get('File')
        
        # Retrieve CoverArt dynamically so it isn't needed in the main JSON payload
        if 'CoverArt' not in current_song or not current_song['CoverArt']:
            file_path = os.path.join(settings.path, self.current_filename)
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

        if(str(last_filename) != str(self.current_filename)):
            pygame.mixer.music.stop()
            self.first_play = True
            self.current_time_offset = 0

        if(self.first_play):
            self._window.evaluate_js(f"playing_view({json.dumps(current_song)})")
            pygame.mixer.music.load(os.path.join(settings.path, str(self.current_filename)))

            self._window.evaluate_js(f"window.plaiyng_info({json.dumps(current_song)})")

            pygame.mixer.music.set_volume(settings.volume)
            pygame.mixer.music.play()
            self.current_time_offset = 0
            self.last_play_time = time.time()
            
            if opening:
                pygame.mixer.music.pause()
                self.playing = False
                self.pause_time = 0
                self._smtc.playback_status = media.MediaPlaybackStatus.PAUSED
                if self._window:
                    self._window.evaluate_js("if (typeof update_play_button_ui === 'function') { update_play_button_ui(false); }")
                    # Trigger your frontend's visualizer pause logic
                    self._window.evaluate_js("if (typeof stop_visualizer === 'function') { stop_visualizer(); }")
            else:
                self.playing = True
                self._smtc.playback_status = media.MediaPlaybackStatus.PLAYING
                if self._window:
                    self._window.evaluate_js("if (typeof update_play_button_ui === 'function') { update_play_button_ui(true); }")

            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("UPDATE Settings SET current_song = ?", (self.current_filename,))
            except Exception as e:
                print(f" [Python] Database error: {e}")
            
            # Update Windows Media Overlay with current song info!
            updater = self._smtc.display_updater
            updater.type = media.MediaPlaybackType.MUSIC
            updater.music_properties.title = str(current_song.get('Title', 'Unknown'))
            updater.music_properties.artist = str(current_song.get('Artist', 'Unknown'))
            updater.update()

        elif(self.playing):
            self.pause_time = self.get_current_pos()
            pygame.mixer.music.pause()
            self.playing = False
            #winsdk
            self._smtc.playback_status = media.MediaPlaybackStatus.PAUSED
        else:
            if pygame.mixer.music.get_pos() == -1:
                pygame.mixer.music.play()
                self.current_time_offset = 0
                self.last_play_time = time.time()
            else:
                pygame.mixer.music.unpause()
            self.playing = True
            #winsdk
            self._smtc.playback_status = media.MediaPlaybackStatus.PLAYING

        self.last_song = current_song
        self.first_play = False
        return self.playing

    #sends progress bar progression
    def get_current_pos(self):
        if self.first_play:
            return 0
            
        if not self.playing:
            return getattr(self, 'pause_time', 0)
                    
        pos = pygame.mixer.music.get_pos()
        
        # -1 means the music is not playing (naturally ended or fully stopped)
        # Added 0.5s cooldown check to prevent Pygame buffering delays from instantly skipping the queue!
        if pos == -1:
            if self.playing and (time.time() - self.last_play_time > 0.5):
                self.play_next()
            return 0
            
        return self.current_time_offset + (pos / 1000.0)
    
    def play_next(self):
        if self.next_songs:
            next_song = self.next_songs.pop(0)
            if self.last_song and self.last_song.get('File'):
                self.prev_songs.append(self.last_song)
            
            # Keep frontend UI in sync by removing it from the visual queue and setting it active
            if self._window:
                self._window.evaluate_js(f"""
                    if (window.queue_songs && window.queue_songs.length > 0) {{
                        window.queue_songs.shift();
                        if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                    }}
                    window.current_playing_song = {json.dumps(next_song)};
                    if (typeof window.set_active_song_ui === 'function') {{ window.set_active_song_ui(window.current_playing_song); }}
                """)
            self.play_button(next_song)
        else:
            # Fall back to playing the next consecutive song in the general list
            if not self.fallback_to_general_list:
                self.playing = False
                self.pause_time = 0
                self._smtc.playback_status = media.MediaPlaybackStatus.STOPPED
                self.current_time_offset = 0
                if self._window:
                    self._window.evaluate_js("if (typeof update_play_button_ui === 'function') { update_play_button_ui(false); }")
                return

            next_song = None
            if getattr(self, 'shuffle', False) and self.song_list:
                available_songs = [s for s in self.song_list if s.get('File') != self.current_filename]
                if available_songs:
                    next_song = random.choice(available_songs)
                elif self.song_list:
                    next_song = self.song_list[0]
            elif self.current_filename and self.song_list:
                for i, song in enumerate(self.song_list):
                    if song.get('File') == self.current_filename:
                        if i + 1 < len(self.song_list):
                            next_song = self.song_list[i + 1]
                        break
            
            if next_song:
                if self.last_song and self.last_song.get('File'):
                    self.prev_songs.append(self.last_song)
                if self._window:
                    self._window.evaluate_js(f"window.current_playing_song = {json.dumps(next_song)}; if (typeof window.set_active_song_ui === 'function') {{ window.set_active_song_ui(window.current_playing_song); }}")
                self.play_button(next_song)
            else:
                # Reached the end of the queue and the end of the playlist, stop playing
                self.playing = False
                self.pause_time = 0
                self._smtc.playback_status = media.MediaPlaybackStatus.STOPPED
                self.current_time_offset = 0
                if self._window:
                    self._window.evaluate_js("if (typeof update_play_button_ui === 'function') { update_play_button_ui(false); }")
                    
    def play_prev(self):
        if self.prev_songs:
            prev_song = self.prev_songs.pop()
            
            if self.last_song and self.last_song.get('File'):
                self.next_songs.insert(0, self.last_song)
                
            # Keep frontend UI in sync by unshifting it back to the visual queue and setting it active
            if self._window:
                self._window.evaluate_js(f"""
                    if (window.queue_songs && window.current_playing_song) {{
                        window.queue_songs.unshift(window.current_playing_song);
                        if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                    }}
                    window.current_playing_song = {json.dumps(prev_song)};
                    if (typeof window.set_active_song_ui === 'function') {{ window.set_active_song_ui(window.current_playing_song); }}
                """)
                
            self.play_button(prev_song)
        else:
            # Fall back to playing the previous consecutive song in the general list
            prev_song = None
            if self.current_filename and self.song_list:
                for i, song in enumerate(self.song_list):
                    if song.get('File') == self.current_filename:
                        if i - 1 >= 0:
                            prev_song = self.song_list[i - 1]
                        break
            
            if prev_song:
                if self.last_song and self.last_song.get('File'):
                    self.next_songs.insert(0, self.last_song)
                    
                if self._window:
                    self._window.evaluate_js(f"""
                        if (window.queue_songs && window.current_playing_song) {{
                            window.queue_songs.unshift(window.current_playing_song);
                            if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                        }}
                        window.current_playing_song = {json.dumps(prev_song)}; 
                        if (typeof window.set_active_song_ui === 'function') {{ window.set_active_song_ui(window.current_playing_song); }}
                    """)
                self.play_button(prev_song)
            else:
                # Reached the beginning of the playlist, just rewind the current song
                pygame.mixer.music.play(0, 0.0)
                self.current_time_offset = 0
                self.last_play_time = time.time()
                if not self.playing:
                    pygame.mixer.music.pause()
                    


    #updates pygames volume
    def volume_slider(self,volume):
        pygame.mixer.music.set_volume(float(volume))
        settings.volume = float(volume)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("UPDATE Settings SET current_volume = ?", (volume,))
        except Exception as e:
            print(f" [Python] Database error: {e}")

    def romanize_text(self, text, is_lrc=False):
        if not kks or not text:
            return text
            
        lines = text.split('\n')
        romanized_lines = []
        for line in lines:
            if is_lrc:
                match = re.match(r'^(\[\d+:\d+\.\d+\])(.*)$', line)
                if match:
                    timestamp = match.group(1)
                    content = match.group(2)
                    
                    converted = kks.convert(content)
                    romaji = " ".join([item['hepburn'] for item in converted])
                    romaji = re.sub(r'\s+', ' ', romaji).strip() # Clean up double spaces
                    
                    romanized_lines.append(f"{timestamp} {romaji}")
                else:
                    romanized_lines.append(line)
            else:
                converted = kks.convert(line)
                romaji = " ".join([item['hepburn'] for item in converted])
                romaji = re.sub(r'\s+', ' ', romaji).strip()
                romanized_lines.append(romaji)
                
        return '\n'.join(romanized_lines)

    #change on song progress bar
    def progress_slider_click(self, sec):
        pygame.mixer.music.play(0, float(sec))
        self.current_time_offset = float(sec)
        
        # If the music was paused when the user seeked, keep it paused!
        if not self.playing:
            pygame.mixer.music.pause()
            self.pause_time = float(sec)
                
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
            return self.downloader.download_song(data, progress_callback)
        except Exception as e:
            print(f" [Python] Error: {e}")
            return f"Error: {e}"
        
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
    
    #sends the sons data to the frontend
    def send_song_list(self):
        path = Path(settings.path)
        files = [f.name for f in path.iterdir() if f.is_file() and f.suffix.lower() == '.mp3']
        self.song_list = []
        for file in files:
            file_path = str(path / file)
            song_data = self.get_song_metadata(file_path, file)
            self.song_list.append(song_data)
        self._window.evaluate_js(f"song_list({json.dumps(self.song_list)})")
    
    def load_settings(self):
        self._window.evaluate_js(f"window.load_settings({settings.volume})")

    def delete_song(self, song_data):
        filename = song_data.get('File')
        total_path = os.path.join(settings.path, filename)
        last_filename = self.last_song.get('File')
        
        if(str(last_filename) == str(filename)):
            pygame.mixer.music.stop()
            if(hasattr(pygame.mixer.music, 'unload')):
                pygame.mixer.music.unload()
            self.playing = False
            self.first_play = True
            self._smtc.playback_status = media.MediaPlaybackStatus.STOPPED
            self._window.evaluate_js("if (typeof update_play_button_ui === 'function') { update_play_button_ui(false); }")

        if os.path.exists(total_path):
            try:
                os.remove(total_path)
                # Keep backend memory synced with deletions without full reload
                self.song_list = [s for s in getattr(self, 'song_list', []) if s.get('File') != filename]
                self.next_songs = [s for s in getattr(self, 'next_songs', []) if s.get('File') != filename]
                self.prev_songs = [s for s in getattr(self, 'prev_songs', []) if s.get('File') != filename]
                if self._window:
                    self._window.evaluate_js(f"window.queue_songs = {json.dumps(self.next_songs)}; if (typeof window.update_queue_ui === 'function') window.update_queue_ui();")
            except Exception as e:
                print(f" [Python] Error deleting file: {e}")

    def update_song_metadata(self, song_data):
        filename = song_data.get('File')
        print(f" [Python] Updating metadata for: {filename}")
        try:
            total_path = os.path.join(settings.path, filename)
            if not os.path.exists(total_path):
                return False

            # If the song is currently playing, pygame holds a file lock.
            # We must stop and unload it before making modifications.
            last_filename = self.last_song.get('File')
            is_current_song = (str(last_filename) == str(filename))
            was_playing = False
            resume_pos = 0

            if is_current_song:
                was_playing = self.playing
                resume_pos = self.get_current_pos()
                pygame.mixer.music.stop()
                if hasattr(pygame.mixer.music, 'unload'):
                    pygame.mixer.music.unload()

            audio = MP3(total_path)
            if audio.tags is None:
                audio.add_tags()
            
            if song_data.get('Title'):
                audio.tags['TIT2'] = TIT2(encoding=3, text=[song_data['Title']])
            if song_data.get('Artist'):
                audio.tags['TPE1'] = TPE1(encoding=3, text=[song_data['Artist']])
            if song_data.get('Album'):
                audio.tags['TALB'] = TALB(encoding=3, text=[song_data['Album']])
            if song_data.get('Year'):
                audio.tags['TDRC'] = TDRC(encoding=3, text=[str(song_data['Year'])])
            
            if song_data.get('CoverArt') and song_data['CoverArt'].startswith('data:'):
                # Remove any existing cover art to avoid multiple stacked APIC frames
                keys_to_remove = [k for k in audio.tags.keys() if k.startswith('APIC')]
                for k in keys_to_remove:
                    audio.tags.pop(k)

                header, encoded = song_data['CoverArt'].split(',', 1)
                mime = header.split(';')[0].split(':')[1]
                img_data = base64.b64decode(encoded)
                audio.tags.add(
                    APIC(
                        encoding=3, mime=mime, type=3, desc=u'Cover', data=img_data
                    )
                )
            
            audio.save(v2_version=3)
            
            new_title = song_data.get('Title', '')
            new_file_name = filename
            
            if new_title and f"{new_title}.mp3" != filename:
                new_file_path = os.path.join(settings.path, f"{new_title}.mp3")
                if not os.path.exists(new_file_path):
                    os.rename(total_path, new_file_path)
                    new_file_name = f"{new_title}.mp3"
            
            # Resume playback if we interrupted the current song
            if is_current_song:
                self.last_song['File'] = new_file_name
                self.last_song['Title'] = song_data.get('Title', self.last_song.get('Title', 'Unknown'))
                self.last_song['Artist'] = song_data.get('Artist', self.last_song.get('Artist', 'Unknown'))
                self.last_song['Album'] = song_data.get('Album', self.last_song.get('Album', 'Unknown'))
                self.last_song['Year'] = song_data.get('Year', self.last_song.get('Year', 'Unknown'))
                if song_data.get('CoverArt'):
                    self.last_song['CoverArt'] = song_data['CoverArt']
                
                safe_song = json.dumps(self.last_song)
                self._window.evaluate_js(f"window.current_playing_song = {safe_song}; if (typeof window.set_active_song_ui === 'function') window.set_active_song_ui(window.current_playing_song); if (typeof window.plaiyng_info === 'function') window.plaiyng_info(window.current_playing_song);")
                
                pygame.mixer.music.load(os.path.join(settings.path, new_file_name))
                pygame.mixer.music.play(0, resume_pos)
                self.current_time_offset = resume_pos
                
                if not was_playing:
                    pygame.mixer.music.pause()
                else:
                    self.playing = True
                    
                # Update Windows overlay metadata
                updater = self._smtc.display_updater
                updater.music_properties.title = str(self.last_song.get('Title', 'Unknown'))
                updater.music_properties.artist = str(self.last_song.get('Artist', 'Unknown'))
                updater.update()

            # Update backend memory to avoid full reload
            for lst in [getattr(self, 'song_list', []), getattr(self, 'next_songs', []), getattr(self, 'prev_songs', [])]:
                for s in lst:
                    if s.get('File') == filename:
                        s['File'] = new_file_name
                        if 'Title' in song_data: s['Title'] = song_data['Title']
                        if 'Artist' in song_data: s['Artist'] = song_data['Artist']
                        if 'Album' in song_data: s['Album'] = song_data['Album']
                        if 'Year' in song_data: s['Year'] = song_data['Year']
                        if song_data.get('CoverArt'): s['CoverArt'] = song_data['CoverArt']

            if self._window:
                updated_song = next((s for s in getattr(self, 'song_list', []) if s.get('File') == new_file_name), song_data)
                safe_updated = json.dumps(updated_song)
                self._window.evaluate_js(f"if (typeof window.update_song_row_ui === 'function') window.update_song_row_ui({json.dumps(filename)}, {safe_updated});")
                self._window.evaluate_js(f"window.queue_songs = {json.dumps(getattr(self, 'next_songs', []))}; if (typeof window.update_queue_ui === 'function') window.update_queue_ui();")

            return True
        except Exception as e:
            print(f" [Python] Error updating metadata: {str(e)}")
            return False

    def load_current_song(self):
        try:
            if not self.song_list:
                path = Path(settings.path)
                if path.exists():
                    files = [f.name for f in path.iterdir() if f.is_file() and f.suffix.lower() == '.mp3']
                    for file in files:
                        file_path = str(path / file)
                        self.song_list.append(self.get_song_metadata(file_path, file))

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor().execute("SELECT current_song FROM Settings LIMIT 1")
                data = cursor.fetchone()
                if data and data[0]:
                    file = data[0]
                    file_path = os.path.join(settings.path, file)
                    song_data = self.get_song_metadata(file_path, file, include_cover=True)
                    self.play_button(song_data, True)
                    self.populate_queue(song_data)
                
        except Exception as e:
            print(f" [Python] Database error: {e}")

    def get_lyrics(self, track_name, artist_name, album_name=None, duration_seconds=None):
        base_url = "https://lrclib.net/api/get"
        
        # Build query parameters
        params = {
            "track_name": track_name,
            "artist_name": artist_name
        }
        if duration_seconds:
            params["duration"] = int(duration_seconds)

        query_string = urllib.parse.urlencode(params)
        url = f"{base_url}?{query_string}"
        
        headers = {'User-Agent': 'MyMusicPlayer/1.0'}
        req = urllib.request.Request(url, headers=headers)
        
        try:
            with urllib.request.urlopen(req) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    
                    synced = data.get("syncedLyrics")
                    plain = data.get("plainLyrics")
                    
                    if kks:
                        synced = self.romanize_text(synced, is_lrc=True)
                        plain = self.romanize_text(plain, is_lrc=False)

                    return {
                        "synced": synced,
                        "plain": plain
                    }
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f" [Python] Lyrics not found for {track_name} by {artist_name}")
            else:
                print(f" [Python] HTTP Error: {e.code}")
        except Exception as e:
            print(f" [Python] Failed to fetch lyrics: {e}")
            
        return None
        
    def search_itunes_metadata(self, title, artist=None):
        try:
            # Clean up default artist names to improve search accuracy
            artist_str = artist if artist and artist not in ['Unknown', 'Unknown Artist'] else None
            metadata = self.downloader.search_itunes(title, artist_str)
            if metadata:
                # Convert the raw bytes artwork into a base64 Data URI for the frontend
                if metadata.get('artwork'):
                    img_data = base64.b64encode(metadata['artwork']).decode('utf-8')
                    metadata['artwork'] = f"data:image/jpeg;base64,{img_data}"
                return metadata
        except Exception as e:
            print(f" [Python] iTunes Search Error: {str(e)}")
        return None

    def get_cover_art_base64(self, file_name):
        """Endpoint that the frontend can call to lazy-load cover arts asynchronously"""
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