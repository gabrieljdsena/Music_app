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
volume = 0.7
window_size = {'width': 1280, 'height': 720}
background = ""
browser = "firefox"
limit_downloads = "3"
current_playlist = None


if os.path.exists(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT * FROM Settings LIMIT 1")
            data = cursor.fetchone()
            
            if data:

                path = data['songs_path'] if data['songs_path'] else path
                volume = data['current_volume'] if data['current_volume'] is not None else volume
                if data['window_width'] and data['window_height']:
                    window_size = {'width': data['window_width'], 'height': data['window_height']}
                background = data['background_path'] if data['background_path'] else background
                limit_downloads = data['limit_downloads'] if data['limit_downloads'] else limit_downloads
                current_playlist = data['current_playlist'] if 'current_playlist' in data.keys() and data['current_playlist'] is not None else None

        except sqlite3.OperationalError:
            pass