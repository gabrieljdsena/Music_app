import os
import sys
import subprocess

print(" [Dev] Compiling Tailwind CSS...")
current_dir = os.path.dirname(os.path.abspath(__file__))
input_css = os.path.join(current_dir, 'ui', 'input.css')
output_css = os.path.join(current_dir, 'ui', 'output.css')
subprocess.run(f"npx @tailwindcss/cli -i \"{input_css}\" -o \"{output_css}\"", shell=True)

import webview
import threading
import sqlite3
import pygame
import monitor
import settings
from api import Api


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
api = Api(db_path)

# Calculate center position based on the primary screen
try:
    screen = webview.screens[0]
    center_x = (screen.width - settings.window_size['width']) // 2
    center_y = (screen.height - settings.window_size['height']) // 2
except Exception:
    center_x, center_y = None, None

# 3. Create window and pass the api instance
window = webview.create_window(
    title='Hathor',
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