import os
import time
import psutil

def monitor_usage():
    while True:
        try:
            process = psutil.Process(os.getpid())
            total_memory = process.memory_info().rss
            
            # PyWebView spawns multiple Chromium child processes. We need to add their RAM too!
            for child in process.children(recursive=True):
                total_memory += child.memory_info().rss
                
            print(f" [Debug] Total RAM usage: {total_memory / (1024 * 1024):.2f} MB")
        except Exception:
            pass
        time.sleep(5)