import os
import sys
import subprocess
import Download
import settings
from pathlib import Path
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, APIC

print(" [Dev] Compiling Tailwind CSS...")
current_dir = os.path.dirname(os.path.abspath(__file__))
input_css = os.path.join(current_dir, 'ui', 'input.css')
output_css = os.path.join(current_dir, 'ui', 'output.css')
subprocess.run(f"npx @tailwindcss/cli -i \"{input_css}\" -o \"{output_css}\"", shell=True)

import webview
import threading
import sqlite3
import base64
import json
import pygame
import monitor
import winsdk.windows.media as media
import winsdk.windows.media.playback as playback


class Api:

    #public variables
    playing = False
    first_play = True
    last_song = {'File': "",'Artist': "",'Title': "",'Album': "",'Year': "",'Duration': "",'CoverArt': "",'file' : ""}
    current_time_offset = 0
    song_list = []


    def __init__(self):
        self._window = None
        self.downloader = Download.MusicDownloader()
        
        #winsdk
        self.media_player = playback.MediaPlayer()
        self.smtc = self.media_player.system_media_transport_controls
        self.smtc.is_play_enabled = True
        self.smtc.is_pause_enabled = True
        self.smtc.is_next_enabled = True
        self.smtc.is_previous_enabled = True
        
        #winsdk
        self.smtc.add_button_pressed(self.on_smtc_button_pressed)

    #winsdk comunication
    def on_smtc_button_pressed(self, sender, args):
        #winsdk
        if args.button == media.SystemMediaTransportControlsButton.PLAY or args.button == media.SystemMediaTransportControlsButton.PAUSE:
            is_playing = self.play_button(None)
            if self._window and is_playing is not None:
                self._window.evaluate_js(f"update_play_button_ui({'true' if is_playing else 'false'});")
                
        #winsdk
        elif args.button == media.SystemMediaTransportControlsButton.NEXT:
            pass # Link next track logic here later
        elif args.button == media.SystemMediaTransportControlsButton.PREVIOUS:
            pass # Link previous track logic here later


    #play button click / song div click handler
    def play_button(self, current_song=None, opening=False):

        if current_song is None:
            if self.first_play:
                return None
            if self.playing:
                pygame.mixer.music.pause()
                self.playing = False
                self.smtc.playback_status = media.MediaPlaybackStatus.PAUSED
            else:
                if pygame.mixer.music.get_pos() == -1:
                    pygame.mixer.music.play()
                    self.current_time_offset = 0
                else:
                    pygame.mixer.music.unpause()
                self.playing = True
                self.smtc.playback_status = media.MediaPlaybackStatus.PLAYING
            return self.playing

        if(str(self.last_song['file']) != str(current_song['file'])):
            pygame.mixer.music.stop()
            self.first_play = True
            self.current_time_offset = 0

        if(self.first_play):
            self._window.evaluate_js(f"playing_view({json.dumps(current_song)})")
            pygame.mixer.music.load(settings.path + "\\" + str(current_song['file']))

            self._window.evaluate_js(f"window.plaiyng_info({json.dumps(current_song)})")

            pygame.mixer.music.set_volume(settings.volume)
            pygame.mixer.music.play()
            self.current_time_offset = 0
            
            if opening:
                pygame.mixer.music.pause()
                self.playing = False
                self.smtc.playback_status = media.MediaPlaybackStatus.PAUSED
                if self._window:
                    self._window.evaluate_js("if (typeof update_play_button_ui === 'function') { update_play_button_ui(false); }")
                    # Trigger your frontend's visualizer pause logic
                    self._window.evaluate_js("if (typeof stop_visualizer === 'function') { stop_visualizer(); }")
            else:
                self.playing = True
                self.smtc.playback_status = media.MediaPlaybackStatus.PLAYING

            try:
                with sqlite3.connect(db_path) as conn:
                    conn.execute("UPDATE Settings SET current_song = ?", (current_song['file'],))
            except Exception as e:
                print(f" [Python] Database error: {e}")
            
            # Update Windows Media Overlay with current song info!
            updater = self.smtc.display_updater
            updater.type = media.MediaPlaybackType.MUSIC
            updater.music_properties.title = str(current_song.get('Title', 'Unknown'))
            updater.music_properties.artist = str(current_song.get('Artist', 'Unknown'))
            updater.update()

        elif(self.playing):
            pygame.mixer.music.pause()
            self.playing = not self.playing
            #winsdk
            self.smtc.playback_status = media.MediaPlaybackStatus.PAUSED
        else:
            if pygame.mixer.music.get_pos() == -1:
                pygame.mixer.music.play()
                self.current_time_offset = 0
            else:
                pygame.mixer.music.unpause()
            self.playing = not self.playing
            #winsdk
            self.smtc.playback_status = media.MediaPlaybackStatus.PLAYING

        self.last_song = current_song
        self.first_play = False
        return self.playing

    #sends progress bar progression
    def get_current_pos(self):
        if self.first_play:
            return 0
                    
        pos = pygame.mixer.music.get_pos()
        
        # -1 means the music is not playing (naturally ended or fully stopped)
        if pos == -1:
            if self.playing:
                self.playing = False
                self.smtc.playback_status = media.MediaPlaybackStatus.STOPPED
                self.current_time_offset = 0
                if self._window:
                    self._window.evaluate_js("if (typeof update_play_button_ui === 'function') { update_play_button_ui(false); }")
            return 0
            
        return self.current_time_offset + (pos / 1000.0)

    #updates pygames volume
    def volume_slider(self,volume):
        pygame.mixer.music.set_volume(float(volume))
        settings.volume = float(volume)
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute("UPDATE Settings SET current_volume = ?", (volume,))
        except Exception as e:
            print(f" [Python] Database error: {e}")

    #change on song progress bar
    def progress_slider_click(self, sec):
        pygame.mixer.music.play(0, float(sec))
        self.current_time_offset = float(sec)
        
        # If the music was paused when the user seeked, keep it paused!
        if not self.playing:
            pygame.mixer.music.pause()
        

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
        
    def get_song_metadata(self, file_path, file_name):
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
        files_and_data = []
        for file in files:
            file_path = str(path / file)
            song_data = self.get_song_metadata(file_path, file)
            self.song_list.append((song_data['Title'], song_data['File']))
            files_and_data.append(song_data)
        self._window.evaluate_js(f"song_list({json.dumps(files_and_data)})")
    
    def load_settings(self):
        self._window.evaluate_js(f"window.load_settings({settings.volume})")

    def delete_song(self, song_data):
        total_path = settings.path + "\\" + song_data['file']
        
        if(self.last_song.get('file') == song_data.get('file')):
            pygame.mixer.music.stop()
            if(hasattr(pygame.mixer.music, 'unload')):
                pygame.mixer.music.unload()
            self.playing = False
            self.first_play = True
            self.smtc.playback_status = media.MediaPlaybackStatus.STOPPED
            self._window.evaluate_js("if (typeof update_play_button_ui === 'function') { update_play_button_ui(false); }")

        if os.path.exists(total_path):
            try:
                os.remove(total_path)
            except Exception as e:
                print(f" [Python] Error deleting file: {e}")

    def update_song_metadata(self, song_data):
        print(f" [Python] Updating metadata for: {song_data.get('file')}")
        try:
            total_path = os.path.join(settings.path, song_data['file'])
            if not os.path.exists(total_path):
                return False

            # If the song is currently playing, pygame holds a file lock.
            # We must stop and unload it before making modifications.
            is_current_song = (self.last_song.get('file') == song_data['file'])
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
                header, encoded = song_data['CoverArt'].split(',', 1)
                mime = header.split(';')[0].split(':')[1]
                img_data = base64.b64decode(encoded)
                audio.tags.add(
                    APIC(
                        encoding=3, mime=mime, type=3, desc=u'Cover', data=img_data
                    )
                )
            
            audio.save()
            
            new_title = song_data.get('Title', '')
            new_file_name = song_data['file']
            
            if new_title and f"{new_title}.mp3" != song_data['file']:
                new_file_path = os.path.join(settings.path, f"{new_title}.mp3")
                if not os.path.exists(new_file_path):
                    os.rename(total_path, new_file_path)
                    new_file_name = f"{new_title}.mp3"
            
            # Resume playback if we interrupted the current song
            if is_current_song:
                self.last_song['file'] = new_file_name
                self.last_song['Title'] = song_data.get('Title', self.last_song.get('Title', 'Unknown'))
                self.last_song['Artist'] = song_data.get('Artist', self.last_song.get('Artist', 'Unknown'))
                if song_data.get('CoverArt'):
                    self.last_song['CoverArt'] = song_data['CoverArt']
                
                safe_name = json.dumps(new_file_name)
                self._window.evaluate_js(f"if (window.current_playing_song) window.current_playing_song.file = {safe_name};")
                
                pygame.mixer.music.load(os.path.join(settings.path, new_file_name))
                pygame.mixer.music.play(0, resume_pos)
                self.current_time_offset = resume_pos
                
                if not was_playing:
                    pygame.mixer.music.pause()
                else:
                    self.playing = True
                    
                # Update Windows overlay metadata
                updater = self.smtc.display_updater
                updater.music_properties.title = str(self.last_song.get('Title', 'Unknown'))
                updater.music_properties.artist = str(self.last_song.get('Artist', 'Unknown'))
                updater.update()

            self.send_song_list()
            return True
        except Exception as e:
            print(f" [Python] Error updating metadata: {str(e)}")
            return False

    def load_current_song(self):
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor().execute("SELECT current_song FROM Settings LIMIT 1")
                data = cursor.fetchone()
                if data and data[0]:
                    file = data[0]
                    file_path = os.path.join(settings.path, file)
                    song_data = self.get_song_metadata(file_path, file)
                    self.play_button(song_data, True)
                
        except Exception as e:
            print(f" [Python] Database error: {e}")
        


# 1. Setup paths
def get_base_path():
    if hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

current_dir = get_base_path()
db_path = os.path.join(current_dir, 'music_player.db')
html_file = os.path.join(current_dir, 'ui', 'index.html')

#Initialize SQLITE DB
def init_db():
    sql_file = os.path.join(current_dir, 'database.sql')
    
    if os.path.exists(sql_file):
        with open(sql_file, 'r', encoding='utf-8') as f:
            sql_script = f.read()
        try:
            with sqlite3.connect(db_path) as conn:
                conn.executescript(sql_script)
                print(" [Python] Database initialized successfully.")
        except Exception as e:
            print(f" [Python] Database initialization error: {e}")

init_db()

# 2. Instantiate API first
api = Api()

# Calculate center position based on the primary screen
try:
    screen = webview.screens[0]
    center_x = (screen.width - settings.window_size['width']) // 2
    center_y = (screen.height - settings.window_size['height']) // 2
except Exception:
    center_x, center_y = None, None

# 3. Create window and pass the api instance
window = webview.create_window(
    title='Music App',
    url=html_file,
    js_api=api,
    width=settings.window_size['width'],
    height=settings.window_size['height'],
    x=center_x,
    y=center_y,
    min_size=(560, 400)
)

# 4. Link the window back to the API so it can use evaluate_js
api._window = window

def on_resized(width, height):
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE Settings SET window_width = ?, window_height = ?", (width, height))
    except Exception as e:
        print(f" [Python] Database error: {e}")

def on_start(window):
    pygame.init()
    pygame.mixer.init()
    
    # Start the resource monitor in a background thread so it doesn't block the app
    monitor_thread = threading.Thread(target=monitor.monitor_usage, daemon=True)
    monitor_thread.start()
    
    api.load_current_song()

if __name__ == '__main__':
    window.events.resized += on_resized
    webview.start(on_start, window, debug=False)