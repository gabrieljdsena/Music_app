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
import logging
import pygame
#import monitor
import settings
from api import Api
from sync import DatabaseSync


# Persistent file log (hathor.log next to the DB) so issues can be
# diagnosed even when no console is visible (e.g. frozen builds).
def _setup_file_logging():
    try:
        if getattr(sys, 'frozen', False):
            log_dir = os.path.dirname(sys.executable)
        else:
            log_dir = os.path.dirname(os.path.abspath(__file__))
        log_file = os.path.join(log_dir, 'hathor.log')
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler(sys.stdout),
            ],
            force=True,
        )
    except Exception as e:
        print(f" [Python] Could not set up file logging: {e}")

_setup_file_logging()
_log = logging.getLogger('hathor')
_log.info("=== Hathor starting ===")


def _log_uncaught(exc_type, exc_value, exc_tb):
    try:
        _log.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
    except Exception:
        pass

sys.excepthook = _log_uncaught

if hasattr(threading, 'excepthook'):
    _orig_thread_excepthook = threading.excepthook

    def _log_thread_uncaught(args):
        try:
            _log.error(
                "Uncaught thread exception in %s", getattr(args, 'thread', None),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        except Exception:
            pass
        try:
            _orig_thread_excepthook(args)
        except Exception:
            pass

    threading.excepthook = _log_thread_uncaught


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
    frameless=True,
    # The app implements its own titlebar drag (pointer events + move_window).
    # pywebview's native easy_drag would make EVERY mousedown in the app move
    # the window (and fight with sliders/seekbars), so it must stay off.
    easy_drag=False
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
    # Don't persist the maximized footprint as the restored window size.
    try:
        if getattr(api, '_is_maximized', False):
            return
    except Exception:
        pass
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

def _run_startup_maintenance(window):
    """Background FFmpeg auto-download + YouTube-lib update checks.

    Non-blocking: pushes progress to the startup toast in the UI so the
    user can keep using the app while it works.
    """
    import time as _time
    import json as _json
    try:
        from services import startup_maintenance as _maint
    except Exception as e:
        print(f" [Startup] maintenance module unavailable: {e}")
        return

    # Give the webview DOM a moment to define window.startupToast.
    _time.sleep(1.0)

    def safe_js(code):
        try:
            window.evaluate_js(code)
        except Exception:
            pass

    def push(event):
        try:
            safe_js(
                "if (window.startupToast && window.startupToast.update) "
                f"window.startupToast.update({_json.dumps(event)});"
            )
        except Exception:
            pass

    # Only show the popup when there is actual work to do, otherwise stay
    # out of the way. A quick synchronous pre-check decides that.
    try:
        ffmpeg_ok = _maint.find_ffmpeg().get('found', False)
    except Exception:
        ffmpeg_ok = True
    try:
        lib_status = _maint.check_libraries_status()
        libs_ok = all(r.get('status') == 'up-to-date' for r in lib_status) if lib_status else True
    except Exception:
        libs_ok = True

    if ffmpeg_ok and libs_ok:
        print(" [Startup] FFmpeg + libraries up to date, no popup needed.")
        return

    safe_js(
        "if (window.startupToast && window.startupToast.show) "
        "window.startupToast.show('Checking for updates…');"
    )
    try:
        summary = _maint.run_startup_checks(push)
        print(f" [Startup] summary: ffmpeg={summary.get('ffmpeg', {}).get('status')} "
              f"libs={[ (r.get('package'), r.get('status')) for r in summary.get('libraries', []) ]}")
        safe_js(
            "if (window.startupToast && window.startupToast.complete) "
            f"window.startupToast.complete({_json.dumps(summary)});"
        )
    except Exception as e:
        print(f" [Startup] maintenance failed: {e}")
        safe_js(
            "if (window.startupToast && window.startupToast.update) "
            f"window.startupToast.update({_json.dumps({'stage': 'done', 'label': 'Startup check failed', 'percent': 100, 'detail': str(e)[:160], 'status': 'error'})});"
        )


def on_start(window):
    pygame.init()
    pygame.mixer.init()

    # Startup maintenance (FFmpeg + yt-dlp updates) in the background.
    threading.Thread(target=_run_startup_maintenance, args=(window,), daemon=True).start()

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