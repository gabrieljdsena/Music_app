import os
import sqlite3

#loads settings from db

def get_data_path():
    import sys
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

db_path = os.path.join(get_data_path(), 'music_player.db')

# Default values
path = os.path.expandvars(r'%appdata%\musicPlayer')
podcasts_path = os.path.expandvars(r'%appdata%\musicPlayerPodcasts')
volume = 0.7
window_size = {'width': 1280, 'height': 720}
background = ""
browser = "firefox"
limit_downloads = "3"
current_playlist = None
crossfade_enabled = False
crossfade_seconds = 5.0


if os.path.exists(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT * FROM Settings LIMIT 1")
            data = cursor.fetchone()
            
            if data:

                path = data['songs_path'] if data['songs_path'] else path
                if 'podcasts_path' in data.keys() and data['podcasts_path']:
                    podcasts_path = data['podcasts_path']
                volume = data['current_volume'] if data['current_volume'] is not None else volume
                if data['window_width'] and data['window_height']:
                    window_size = {'width': data['window_width'], 'height': data['window_height']}
                background = data['background_path'] if data['background_path'] else background
                limit_downloads = data['limit_downloads'] if data['limit_downloads'] else limit_downloads
                current_playlist = data['current_playlist'] if 'current_playlist' in data.keys() and data['current_playlist'] is not None else None
                if 'crossfade_enabled' in data.keys() and data['crossfade_enabled'] is not None:
                    crossfade_enabled = bool(data['crossfade_enabled'])
                if 'crossfade_seconds' in data.keys() and data['crossfade_seconds'] is not None:
                    try:
                        crossfade_seconds = float(data['crossfade_seconds'])
                    except (TypeError, ValueError):
                        pass

        except sqlite3.OperationalError:
            pass