import os
import sys
import subprocess
from dotenv import load_dotenv

load_dotenv()

# In a frozen build the .env must live next to the exe (never bundled inside it).
if getattr(sys, 'frozen', False):
    load_dotenv(os.path.join(os.path.dirname(sys.executable), '.env'))

if not getattr(sys, 'frozen', False):
    print(" [Dev] Compiling Tailwind CSS...")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    input_css = os.path.join(current_dir, 'ui', 'input.css')
    output_css = os.path.join(current_dir, 'ui', 'output.css')
    subprocess.run(f"npx @tailwindcss/cli -i \"{input_css}\" -o \"{output_css}\"", shell=True)

import webview
import threading
import sqlite3
import json
import pygame
#import monitor
import settings
from api import Api
from sync import DatabaseSync


# 1. Setup paths
def get_base_path():
    if hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

def get_data_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

current_dir = get_base_path()
data_dir = get_data_path()
db_path = os.path.join(data_dir, 'music_player.db')
html_file = os.path.join(current_dir, 'ui', 'index.html')

# Detect first run: DB file doesn't exist yet
is_first_run = not os.path.exists(db_path)

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
if is_first_run:
    print(" [Python] First run detected (database was just created).")

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
    min_size=(895, 400),
    frameless=True
)

# 4. Link the window back to the API so it can use evaluate_js
api._window = window

resize_timer = None

def save_window_size(width, height):
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE Settings SET window_width = ?, window_height = ?", (width, height))
    except Exception as e:
        print(f" [Python] Database error: {e}")

def on_resized(width, height):
    global resize_timer
    if resize_timer is not None:
        resize_timer.cancel()
    resize_timer = threading.Timer(0.4, save_window_size, args=(width, height))
    resize_timer.start()

# 5. Setup MySQL/TiDB remote sync
db_sync = None
if os.getenv("DB_HOST"):
    db_sync = DatabaseSync(db_path)
else:
    print(" [Sync] No remote DB configured in .env, sync disabled.")

def on_start(window):
    pygame.init()
    pygame.mixer.init()
    
    # Start the resource monitor in a background thread so it doesn't block the app
    #monitor_thread = threading.Thread(target=monitor.monitor_usage, daemon=True)
    #monitor_thread.start()
    
    # On first run with a remote DB, ask the user if they want to load from it
    if is_first_run and db_sync:
        _prompt_remote_sync(window)
    else:
        api.load_current_song()
        # Start background MySQL sync
        if db_sync:
            db_sync.start()

def _prompt_remote_sync(window):
    """Show a SweetAlert2 dialog asking the user to load data from the remote DB."""
    import time as _time
    
    # Wait for the webview DOM to be ready
    _time.sleep(1.5)
    
    js_code = """
    (async () => {
        try {
            const result = await Swal.fire({
                title: 'Remote Library Found',
                html: 'A remote database is configured.<br>Would you like to <b>load your music library</b> from it?<br><br><small>This will sync songs, playlists, lyrics, and history, then download any missing songs.</small>',
                icon: 'question',
                showCancelButton: true,
                confirmButtonText: 'Yes, load from remote',
                cancelButtonText: 'No, start fresh',
                allowOutsideClick: false,
                allowEscapeKey: false,
                customClass: { popup: 'swal-dark' }
            });
            return result.isConfirmed ? 'yes' : 'no';
        } catch (e) {
            return 'no';
        }
    })()
    """
    
    def do_prompt():
        try:
            answer = window.evaluate_js(js_code)
            if answer == 'yes':
                print(" [Python] User chose to load from remote DB.")
                # Show loading indicator
                window.evaluate_js("""
                    Swal.fire({
                        title: 'Syncing from Remote...',
                        html: 'Downloading your library data and queuing song downloads.',
                        allowOutsideClick: false,
                        allowEscapeKey: false,
                        didOpen: () => { Swal.showLoading(); },
                        customClass: { popup: 'swal-dark' }
                    });
                """)
                result = api.sync_remote_to_local_and_download()
                print(f" [Python] Remote sync result: {result}")
                window.evaluate_js(f"""
                    Swal.fire({{  
                        title: 'Sync Complete',
                        text: {json.dumps(str(result))},
                        icon: 'success',
                        timer: 3000,
                        showConfirmButton: false,
                        customClass: {{ popup: 'swal-dark' }}
                    }});
                """)
            else:
                print(" [Python] User chose to start fresh.")
            
            # Load songs & start regular sync regardless of choice
            api.load_current_song()
            if db_sync:
                db_sync.start()
                
        except Exception as e:
            print(f" [Python] Remote sync prompt error: {e}")
            api.load_current_song()
            if db_sync:
                db_sync.start()
    
    threading.Thread(target=do_prompt, daemon=True).start()

if __name__ == '__main__':
    window.events.resized += on_resized
    webview.start(on_start, window, debug=False)