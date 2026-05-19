import os
import sqlite3

#loads settings from db

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'music_player.db')

# Default values
path = os.path.expandvars(r'%appdata%\musicPlayer')
volume = 0.7
window_size = {'width': 1280, 'height': 720}
background = ""
browser = "firefox"

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

        except sqlite3.OperationalError:
            pass