import json
import urllib.request
import urllib.parse
import re
import sqlite3

try:
    import pykakasi
    kks = pykakasi.kakasi()
except ImportError:
    kks = None

class LyricsService:
    def __init__(self, api):
        self.api = api

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

    def get_lyrics(self, track_name, artist_name, album_name=None, duration_seconds=None):
        song_file = self.api.current_filename

        # 1. Check local cache first (enables offline lyrics)
        if song_file:
            try:
                with sqlite3.connect(self.api.db_path) as conn:
                    cursor = conn.execute(
                        "SELECT lyrics FROM Lyrics WHERE song_file = ?",
                        (song_file,)
                    )
                    row = cursor.fetchone()
                    if row and row[0]:
                        cached = json.loads(row[0])
                        synced = cached.get("synced")
                        plain = cached.get("plain")
                        if kks:
                            synced = self.romanize_text(synced, is_lrc=True) if synced else None
                            plain = self.romanize_text(plain, is_lrc=False) if plain else None
                        return {"synced": synced, "plain": plain}
            except Exception as e:
                print(f" [Python] Lyrics cache read error: {e}")

        # 2. Fetch from lrclib.net on cache miss
        base_url = "https://lrclib.net/api/get"
        
        clean_track = track_name.replace('？', '?').replace('！', '!')
        clean_track = re.sub(r'\s*[\(\[].*?(remaster|mix|version|edit|live|feat\.|ft\.).*?[\)\]]', '', clean_track, flags=re.IGNORECASE).strip()
        clean_artist = artist_name or ""

        if clean_artist.lower() in ["unknown", "unknown artist", ""] and " - " in clean_track:
            parts = clean_track.split(" - ", 1)
            clean_artist = parts[0].strip()
            clean_track = parts[1].strip()

        params = {
            "track_name": clean_track,
            "artist_name": clean_artist
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

                    # 3. Cache the raw (un-romanized) lyrics for future offline use
                    if song_file:
                        try:
                            lyrics_json = json.dumps({"synced": synced, "plain": plain})
                            with sqlite3.connect(self.api.db_path) as conn:
                                conn.execute(
                                    "INSERT OR REPLACE INTO Lyrics (song_file, lyrics) VALUES (?, ?)",
                                    (song_file, lyrics_json)
                                )
                        except Exception as cache_err:
                            print(f" [Python] Lyrics cache write error: {cache_err}")
                    
                    if kks:
                        synced = self.romanize_text(synced, is_lrc=True) if synced else None
                        plain = self.romanize_text(plain, is_lrc=False) if plain else None

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
