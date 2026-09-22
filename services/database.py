import sqlite3
import os
import datetime
import json
from pathlib import Path
import settings

class DatabaseManager:
    def __init__(self, api, db_path):
        self.api = api
        self.db_path = db_path

    def load_playlists(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                query = "SELECT id, title, description, thumbnail FROM Playlists"
                cursor = conn.execute(query)
                return [{"id": row[0], "title": row[1], "description": row[2], "thumbnail": row[3]} for row in cursor.fetchall()]
        except Exception as e:
            print(f" [Python] Error loading playlists: {str(e)}")
            return []

    def new_playlist(self, data):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("INSERT INTO Playlists (title, description, thumbnail) VALUES (?, ?, ?)", (data.get("Title"), data.get("Description"), data.get("Cover")))
        except Exception as e:
            print(f" [Python] Error creating playlist: {str(e)}")

    def getImage(self, id):
        try:
            with sqlite3.connect(self.db_path) as conn:
                query = "SELECT thumbnail FROM Playlists WHERE id = ?"
                cursor = conn.execute(query, (id,))
                row = cursor.fetchone()
                if row:
                    return row[0]
        except Exception as e:
            print(f" [Python] Error loading playlist image: {str(e)}")
            return None

    def update_playlist(self, playlist_id, data):
        try:
            with sqlite3.connect(self.db_path) as conn:
                fields = []
                values = []
                if "title" in data:
                    fields.append("title = ?")
                    values.append(data["title"])
                if "description" in data:
                    fields.append("description = ?")
                    values.append(data["description"])
                if "thumbnail" in data:
                    fields.append("thumbnail = ?")
                    values.append(data["thumbnail"])
                if fields:
                    values.append(playlist_id)
                    conn.execute(f"UPDATE Playlists SET {', '.join(fields)} WHERE id = ?", values)
            return True
        except Exception as e:
            print(f" [Python] Error updating playlist: {str(e)}")
            return False

    def delete_playlist(self, playlist_id):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM Playlist_History WHERE playlist_id = ?", (playlist_id,))
                conn.execute("DELETE FROM Song_Playlist WHERE playlist_id = ?", (playlist_id,))
                conn.execute("DELETE FROM Playlists WHERE id = ?", (playlist_id,))
            self.record_deletion("playlists", playlist_id)
            self.record_deletion("playlist_history", playlist_id)
            return True
        except Exception as e:
            print(f" [Python] Error deleting playlist: {str(e)}")
            return False

    def record_deletion(self, table_name, row_key):
        """Record a deletion so the background sync can remove the row on the remote DB."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("INSERT INTO Sync_Deletions (table_name, row_key) VALUES (?, ?)", (table_name, str(row_key)))
        except Exception as e:
            print(f" [Python] Error recording deletion: {str(e)}")

    def get_song_playlists(self, song_file):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT playlist_id FROM Song_Playlist WHERE song_file = ?", (song_file,))
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            print(f" [Python] Error getting song playlists: {str(e)}")
            return []

    def update_song_playlists(self, song_file, song_title, playlist_ids):
        try:
            with sqlite3.connect(self.db_path) as conn:
                # First ensure song exists in Songs table to satisfy foreign key constraint
                conn.execute("INSERT OR IGNORE INTO Songs (file, title) VALUES (?, ?)", (song_file, song_title or song_file))
                
                # Delete existing associations
                conn.execute("DELETE FROM Song_Playlist WHERE song_file = ?", (song_file,))
                
                # Insert new associations
                for pid in playlist_ids:
                    conn.execute("INSERT INTO Song_Playlist (song_file, playlist_id) VALUES (?, ?)", (song_file, pid))
            return True
        except Exception as e:
            print(f" [Python] Error updating song playlists: {str(e)}")
            return False

    def get_playlist_songs(self, playlist_id):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT song_file, date_added FROM Song_Playlist WHERE playlist_id = ?", (playlist_id,))
                rows = cursor.fetchall()
                
                playlist_songs = []
                for row in rows:
                    file_name = row[0]
                    date_added = row[1]
                    file_path = os.path.join(settings.path, file_name)
                    if os.path.exists(file_path):
                        song_data = self.api.metadata.get_song_metadata(file_path, file_name)
                        if date_added:
                            song_data['DateAdded'] = date_added.replace(' ', 'T') + 'Z'
                        else:
                            song_data['DateAdded'] = None
                        playlist_songs.append(song_data)
                return playlist_songs
        except Exception as e:
            print(f" [Python] Error loading playlist songs: {str(e)}")
            return []

    def get_songs_by_artist(self, artist):
        """Return all songs whose metadata Artist matches the given artist (metadata-only)."""
        path = Path(settings.path)
        if not path.exists() or not artist:
            return []
        artist = str(artist).strip()

        date_lookup = {}
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT file, date_download FROM Songs")
                for row in cursor.fetchall():
                    if row[1]:
                        date_lookup[row[0]] = row[1].replace(' ', 'T') + 'Z' if 'T' not in str(row[1]) else str(row[1])
                    else:
                        date_lookup[row[0]] = None
        except Exception as e:
            print(f" [Python] Error fetching date_download for artist: {e}")

        songs = []
        for f in path.iterdir():
            if f.is_file() and f.suffix.lower() == '.mp3':
                song_data = self.api.metadata.get_song_metadata(str(f), f.name)
                if (song_data.get('Artist') or '') == artist:
                    song_data['DateDownload'] = date_lookup.get(f.name, None)
                    songs.append(song_data)
        songs.sort(key=lambda s: ((s.get('Album') or '').lower(), (s.get('Title') or '').lower()))
        return songs

    def get_songs_by_album(self, album):
        """Return all songs whose metadata Album matches the given album (metadata-only)."""
        path = Path(settings.path)
        if not path.exists() or not album:
            return []
        album = str(album).strip()

        date_lookup = {}
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT file, date_download FROM Songs")
                for row in cursor.fetchall():
                    if row[1]:
                        date_lookup[row[0]] = row[1].replace(' ', 'T') + 'Z' if 'T' not in str(row[1]) else str(row[1])
                    else:
                        date_lookup[row[0]] = None
        except Exception as e:
            print(f" [Python] Error fetching date_download for album: {e}")

        songs = []
        for f in path.iterdir():
            if f.is_file() and f.suffix.lower() == '.mp3':
                song_data = self.api.metadata.get_song_metadata(str(f), f.name)
                if (song_data.get('Album') or '') == album:
                    song_data['DateDownload'] = date_lookup.get(f.name, None)
                    songs.append(song_data)
        songs.sort(key=lambda s: ((s.get('Artist') or '').lower(), (s.get('Title') or '').lower()))
        return songs

    def get_download_history(self, page=1, limit=10):
        try:
            import math
            page = max(int(page), 1)
            limit = max(int(limit), 1)
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT file, date_download FROM Songs "
                    "WHERE downloaded_link IS NOT NULL AND downloaded_link != '' "
                    "ORDER BY date_download DESC"
                )
                rows = cursor.fetchall()

            existing = [
                (file_name, date_download)
                for file_name, date_download in rows
                if os.path.exists(os.path.join(settings.path, file_name))
            ]
            total_count = len(existing)
            total_pages = math.ceil(total_count / limit) if total_count > 0 else 1
            if page > total_pages:
                page = total_pages
            offset = (page - 1) * limit
            page_rows = existing[offset:offset + limit]

            history_songs = []
            for file_name, date_download in page_rows:
                file_path = os.path.join(settings.path, file_name)
                song_data = self.api.metadata.get_song_metadata(file_path, file_name, include_cover=True)
                song_data['DateDownload'] = date_download.replace(' ', 'T') + 'Z' if date_download else None
                history_songs.append(song_data)
            return {"items": history_songs, "total_pages": total_pages, "current_page": page}
        except Exception as e:
            print(f" [Python] Error loading download history: {str(e)}")
            return {"items": [], "total_pages": 1, "current_page": 1}

    def get_played_history(self, page=1, limit=10):
        try:
            import math
            page = max(int(page), 1)
            limit = max(int(limit), 1)
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT song_file, date_played FROM Music_History ORDER BY date_played DESC")
                rows = cursor.fetchall()

            existing = [
                (file_name, date_played)
                for file_name, date_played in rows
                if os.path.exists(os.path.join(settings.path, file_name))
            ]
            total_count = len(existing)
            total_pages = math.ceil(total_count / limit) if total_count > 0 else 1
            if page > total_pages:
                page = total_pages
            offset = (page - 1) * limit
            page_rows = existing[offset:offset + limit]

            history_songs = []
            for file_name, date_played in page_rows:
                file_path = os.path.join(settings.path, file_name)
                song_data = self.api.metadata.get_song_metadata(file_path, file_name, include_cover=True)
                song_data['DatePlayed'] = date_played.replace(' ', 'T') + 'Z' if date_played else None
                history_songs.append(song_data)
            return {"items": history_songs, "total_pages": total_pages, "current_page": page}
        except Exception as e:
            print(f" [Python] Error loading played history: {str(e)}")
            return {"items": [], "total_pages": 1, "current_page": 1}

    def sync_local_songs_to_db(self):
        """Iterate over the local music folder and ensure all songs exist in the database."""
        path = Path(settings.path)
        if not path.exists():
            return "Folder does not exist."
            
        files = [f.name for f in path.iterdir() if f.is_file() and f.suffix.lower() == '.mp3']
        added_count = 0
        updated_count = 0
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                for file in files:
                    file_path = str(path / file)
                    song_data = self.api.metadata.get_song_metadata(file_path, file)
                    
                    title = song_data.get('Title', file.replace('.mp3', ''))
                    artist = song_data.get('Artist', '')
                    if artist == 'Unknown':
                        artist = ''
                        
                    # Check if exists
                    cursor = conn.execute("SELECT title, artist FROM Songs WHERE file = ?", (file,))
                    row = cursor.fetchone()
                    
                    if row:
                        if row[0] != title or row[1] != artist:
                            conn.execute("UPDATE Songs SET title = ?, artist = ? WHERE file = ?", (title, artist, file))
                            updated_count += 1
                    else:
                        try:
                            mtime = os.path.getmtime(file_path)
                            dt = datetime.datetime.utcfromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
                            conn.execute("INSERT INTO Songs (file, title, artist, date_download) VALUES (?, ?, ?, ?)", (file, title, artist, dt))
                        except Exception:
                            conn.execute("INSERT INTO Songs (file, title, artist) VALUES (?, ?, ?)", (file, title, artist))
                        added_count += 1
                        
            return f"Sync complete: {added_count} added, {updated_count} updated."
        except Exception as e:
            print(f" [Python] Sync error: {e}")
            return f"Error: {str(e)}"

    def sync_remote_to_local_and_download(self):
        """Fetch missing songs from remote database, save to local, and trigger downloads."""
        if not os.getenv("DB_HOST"):
            return "No remote DB configured. Remote sync unavailable."
        
        try:
            from sync import get_mysql_connection
            conn = get_mysql_connection()
            with conn.cursor() as cur:
                # Add check for songs table existence
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_name = 'songs'"
                )
                if not cur.fetchone()[0]:
                    return "Remote database not initialized yet."
                cur.execute("SELECT file, downloaded_link, title, date_download, artist FROM songs")
                remote_songs = cur.fetchall()
                
                cur.execute("SELECT id, title, description, thumbnail FROM playlists")
                remote_playlists = cur.fetchall()
                
                cur.execute("SELECT id, song_file, playlist_id, date_added FROM song_playlist")
                remote_song_playlist = cur.fetchall()
                
                cur.execute("SELECT id, song_file, lyrics FROM lyrics")
                remote_lyrics = cur.fetchall()
                
                cur.execute("SELECT id, song_file, date_played FROM music_history")
                remote_music_history = cur.fetchall()
                
                cur.execute("SELECT id, playlist_id, date_played FROM playlist_history")
                remote_playlist_history = cur.fetchall()
            conn.close()
        except Exception as e:
            return f"Error connecting to remote DB: {e}"
        
        songs_to_download = []
        added_count = 0
        try:
            with sqlite3.connect(self.db_path) as local_conn:
                for file, downloaded_link, title, date_download, artist in remote_songs:
                    # Check if exists in local db
                    cursor = local_conn.execute("SELECT file FROM Songs WHERE file = ?", (file,))
                    if not cursor.fetchone():
                        # Insert into local
                        local_conn.execute(
                            "INSERT INTO Songs (file, downloaded_link, title, date_download, artist) VALUES (?, ?, ?, ?, ?)",
                            (file, downloaded_link, title, date_download, artist)
                        )
                        added_count += 1
                        
                        file_path = os.path.join(settings.path, file)
                        if not os.path.exists(file_path):
                            if downloaded_link:
                                vid_id = downloaded_link.split("v=")[-1] if "v=" in downloaded_link else downloaded_link.split("/")[-1]
                                url = downloaded_link
                            else:
                                vid_id = ""
                                url = f"{title} {artist if artist else ''} audio".strip()
                                
                            songs_to_download.append({
                                "id": vid_id,
                                "url": url,
                                "title": title,
                                "artist": artist if artist else "Unknown"
                            })
                
                # Sync other tables
                for row in remote_playlists:
                    local_conn.execute("""
                        INSERT INTO Playlists (id, title, description, thumbnail) VALUES (?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            title = excluded.title,
                            description = excluded.description,
                            thumbnail = excluded.thumbnail
                    """, row)
                for row in remote_song_playlist:
                    local_conn.execute("""
                        INSERT INTO Song_Playlist (id, song_file, playlist_id, date_added) VALUES (?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            song_file = excluded.song_file,
                            playlist_id = excluded.playlist_id,
                            date_added = excluded.date_added
                    """, row)
                for row in remote_lyrics:
                    local_conn.execute("""
                        INSERT INTO Lyrics (id, song_file, lyrics) VALUES (?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            song_file = excluded.song_file,
                            lyrics = excluded.lyrics
                    """, row)
                for row in remote_music_history:
                    local_conn.execute("""
                        INSERT INTO Music_History (id, song_file, date_played) VALUES (?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            song_file = excluded.song_file,
                            date_played = excluded.date_played
                    """, row)
                for row in remote_playlist_history:
                    local_conn.execute("""
                        INSERT INTO Playlist_History (id, playlist_id, date_played) VALUES (?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            playlist_id = excluded.playlist_id,
                            date_played = excluded.date_played
                    """, row)
                    
        except Exception as e:
            return f"Error syncing to local DB: {e}"
        
        if songs_to_download:
            import threading
            def download_worker():
                for song_data in songs_to_download:
                    try:
                        self.api.recieve_download(song_data)
                    except Exception as e:
                        print(f" [Python] Download error for {song_data.get('title')}: {e}")
                
                if getattr(self.api, '_window', None):
                    self.api._window.evaluate_js("if (typeof window.pywebview !== 'undefined' && typeof window.pywebview.api !== 'undefined') window.pywebview.api.send_song_list();")
                    
            threading.Thread(target=download_worker, daemon=True).start()
            
        return f"Synced {added_count} new entries from remote DB. Started downloading {len(songs_to_download)} songs."
