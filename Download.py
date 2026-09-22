import os
import sys
import re
import json
import threading
import importlib
import subprocess
import urllib.request
import urllib.parse
from yt_dlp import YoutubeDL
import settings
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, APIC, TCON

class MusicDownloader:
    def __init__(self):
        self._ytdlp_checked = False
        self._ytdlp_lock = threading.Lock()

    # ==========================
    # yt-dlp Self-Update
    # ==========================
    def _check_update_ytdlp_once(self):
        """Check PyPI once per session and auto-update yt-dlp before the first download."""
        if self._ytdlp_checked:
            return
        with self._ytdlp_lock:
            if self._ytdlp_checked:
                return
            self._ytdlp_checked = True
            self._check_and_update_ytdlp()

    def _check_and_update_ytdlp(self):
        try:
            from yt_dlp.version import __version__ as current
        except Exception:
            current = None

        if getattr(sys, 'frozen', False):
            print(" [Python] yt-dlp auto-update skipped (frozen build).")
            return
        if not current:
            return

        latest = self._get_latest_ytdlp_version()
        if not latest:
            return

        if self._version_tuple(current) >= self._version_tuple(latest):
            print(f" [Python] yt-dlp is up to date ({current}).")
            return

        print(f" [Python] Updating yt-dlp {current} -> {latest}...")
        try:
            subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '--upgrade', 'yt-dlp'],
                check=True,
                capture_output=True,
                timeout=180
            )
            print(" [Python] yt-dlp updated successfully.")
            self._reload_ytdlp()
        except Exception as e:
            print(f" [Python] yt-dlp auto-update failed: {e}")

    @staticmethod
    def _get_latest_ytdlp_version():
        try:
            req = urllib.request.Request(
                "https://pypi.org/pypi/yt-dlp/json",
                headers={'User-Agent': 'Hathor-MusicPlayer/1.0'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                return data['info']['version']
        except Exception as e:
            print(f" [Python] Could not check the latest yt-dlp version: {e}")
            return None

    @staticmethod
    def _version_tuple(version):
        return tuple(int(p) for p in re.findall(r'\d+', str(version)))

    @staticmethod
    def _reload_ytdlp():
        try:
            ytdl_module = importlib.import_module('yt_dlp')
            importlib.reload(ytdl_module)
            globals()['YoutubeDL'] = ytdl_module.YoutubeDL
            print(" [Python] yt-dlp reloaded; updated version is active.")
        except Exception as e:
            print(f" [Python] Could not reload yt-dlp, restart the app to use the update: {e}")

    def search_yt(self, search_query, limit=5):
        """Searches YouTube and returns a list of results without downloading."""
        ydl_opts = {
            'extract_flat': True,
            'skip_download': True,
            'quiet': True,
        }
        try:
            with YoutubeDL(ydl_opts) as ydl:
                # Extract info for top search results
                info = ydl.extract_info(f"ytsearch{limit}:{search_query}", download=False)
                results = []
                if 'entries' in info:
                    for entry in info['entries']:
                        results.append({
                            'id': entry.get('id'),
                            'title': entry.get('title'),
                            'uploader': entry.get('uploader'),
                            'duration': entry.get('duration', 0),
                            'thumbnail': entry.get('thumbnails', [{}])[-1].get('url', '') if entry.get('thumbnails') else ''
                        })
                return results
        except Exception as e:
            print(f" [Python] Search Error: {str(e)}")
            return []

    @staticmethod
    def _safe_filename(name):
        """Strip characters that are invalid in Windows filenames."""
        name = re.sub(r'[\\/:*?"<>|]', '', str(name))
        name = re.sub(r'[\x00-\x1f]', '', name)
        name = re.sub(r'\s+', ' ', name).strip().rstrip('.')
        if len(name) > 150:
            name = name[:150].rstrip()
        return name or 'Unknown'

    def download_song(self, search, progress_callback=None):
        self._check_update_ytdlp_once()

        base_path = sys._MEIPASS if hasattr(sys, '_MEIPASS') else os.path.dirname(__file__)
        ffmpeg_path = os.path.join(base_path, 'ffmpeg', 'bin')
        
        print(f" [Python] Searching and Downloading: {search}")
        
        appdata_path = settings.path
        os.makedirs(appdata_path, exist_ok=True)
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }],
            # Video id keeps concurrent downloads from colliding; restrictfilenames
            # removes characters that are invalid on Windows.
            'outtmpl': os.path.join(appdata_path, '%(id)s_%(title)s.%(ext)s'),
            'ffmpeg_location': ffmpeg_path,
            'restrictfilenames': True,
            'noplaylist': True,
        }
        
        if progress_callback:
            ydl_opts['progress_hooks'] = [progress_callback]
            
        try:
            with YoutubeDL(ydl_opts) as ydl:
                # If the search is a direct YouTube URL, use it directly. Otherwise, do a search.
                query = search if search.startswith('http') else f"ytsearch1:{search} audio"
                info = ydl.extract_info(query, download=True)
                if 'entries' in info and len(info['entries']) > 0:
                    info = info['entries'][0]

            downloaded_files = info.get('requested_downloads') or []
            if downloaded_files and downloaded_files[0].get('filepath'):
                output_file = downloaded_files[0]['filepath']
            else:
                output_file = os.path.join(
                    appdata_path,
                    self._safe_filename(f"{info.get('id')}_{info.get('title')}") + '.mp3'
                )
            
            if progress_callback:
                progress_callback({'status': 'processing_metadata'})
                
            # Try to get better metadata from iTunes
            itunes_metadata = self.search_itunes(info.get('title'), info.get('uploader'))
            
            if itunes_metadata:
                metadata = itunes_metadata
            else:
                # Fallback to yt-dlp data
                metadata = {
                    'title': info.get('title', ''),
                    'artist': info.get('uploader', '')
                }
            
            final_path = self.apply_metadata(output_file, metadata)
            if not final_path:
                raise Exception("Failed to apply metadata")
            final_filename = os.path.basename(final_path)
            
            if progress_callback:
                progress_callback({'status': 'finished_all'})

            final_title = metadata.get('title', info.get('title', 'Unknown'))
            source_url = info.get('webpage_url') or info.get('original_url') or (search if isinstance(search, str) and search.startswith('http') else None)
                
            print(f" [Python] Download complete with metadata: {metadata}")
            return {
                "status": "success",
                "filename": final_filename,
                "title": final_title,
                "artist": metadata.get('artist', ''),
                "source_url": source_url
            }
            
        except Exception as e:
            print(f" [Python] Error: {str(e)}")
            return f"Download failed: {str(e)}"
        
    def search_itunes(self, title, artist=None):
        # Clean the title for better fallback queries
        clean_title = title
        if " - " in clean_title:
            clean_title = clean_title.split(" - ")[-1]
        
        clean_title = re.sub(r'\([^)]*\)', '', clean_title)
        clean_title = re.sub(r'\[[^\]]*\]', '', clean_title)
        clean_title = re.sub(r'(?i)(official music video|official video|official lyric video|official audio|lyric video|lyrics|audio|visualizer)', '', clean_title)
        clean_title = " ".join(clean_title.split()).strip()

        # Build a list of fallback queries
        queries_to_try = []
        if artist:
            queries_to_try.append(f"{clean_title} {artist}")
            queries_to_try.append(f"{title} {artist}")
        queries_to_try.append(clean_title)
        queries_to_try.append(title)
        
        # Remove empty and duplicate queries while keeping order
        unique_queries = []
        for q in queries_to_try:
            if q and q not in unique_queries:
                unique_queries.append(q)
                
        for query in unique_queries:
            try:
                encoded_query = urllib.parse.quote(query)
                url = f"https://itunes.apple.com/search?term={encoded_query}&entity=song&limit=1"
                
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req) as response:
                    data = json.loads(response.read().decode())
                    
                if data['resultCount'] > 0:
                    result = data['results'][0]
                    
                    # Get high-res artwork url
                    artwork_url = result.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
                    artwork_data = None
                    if artwork_url:
                        try:
                            art_req = urllib.request.Request(artwork_url, headers={'User-Agent': 'Mozilla/5.0'})
                            with urllib.request.urlopen(art_req) as art_response:
                                artwork_data = art_response.read()
                        except Exception as e:
                            print(f"Failed to download artwork: {e}")
                    
                    return {
                        'title': result.get('trackName', title),
                        'artist': result.get('artistName', artist),
                        'album': result.get('collectionName', ''),
                        'year': result.get('releaseDate', '')[:4],
                        'genre': result.get('primaryGenreName', ''),
                        'artwork': artwork_data
                    }
            except Exception as e:
                print(f"iTunes lookup failed for query '{query}': {str(e)}")
        
        return None

    def search_itunes_multi(self, title, artist=None, limit=5):
        """Search iTunes and return multiple results for user selection."""
        clean_title = title
        if " - " in clean_title:
            clean_title = clean_title.split(" - ")[-1]
        
        clean_title = re.sub(r'\([^)]*\)', '', clean_title)
        clean_title = re.sub(r'\[[^\]]*\]', '', clean_title)
        clean_title = re.sub(r'(?i)(official music video|official video|official lyric video|official audio|lyric video|lyrics|audio|visualizer)', '', clean_title)
        clean_title = " ".join(clean_title.split()).strip()

        query = f"{clean_title} {artist}" if artist else clean_title
        
        try:
            encoded_query = urllib.parse.quote(query)
            url = f"https://itunes.apple.com/search?term={encoded_query}&entity=song&limit={limit}"
            
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
            
            results = []
            if data['resultCount'] > 0:
                for result in data['results']:
                    artwork_url = result.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
                    results.append({
                        'title': result.get('trackName', title),
                        'artist': result.get('artistName', artist or ''),
                        'album': result.get('collectionName', ''),
                        'year': result.get('releaseDate', '')[:4],
                        'genre': result.get('primaryGenreName', ''),
                        'artwork_url': artwork_url,
                        'artwork_thumb': result.get('artworkUrl100', ''),
                    })
            return results
        except Exception as e:
            print(f"iTunes multi-search failed: {str(e)}")
            return []

    def apply_metadata(self, file_path, metadata):
        """Apply metadata to MP3 file"""
        try:
            audio = MP3(file_path)
            if audio.tags is None:
                audio.add_tags()
            
            if metadata.get('title'):
                audio.tags['TIT2'] = TIT2(encoding=3, text=[metadata['title']])
            if metadata.get('artist'):
                audio.tags['TPE1'] = TPE1(encoding=3, text=[metadata['artist']])
            if metadata.get('album'):
                audio.tags['TALB'] = TALB(encoding=3, text=[metadata['album']])
            if metadata.get('year'):
                audio.tags['TDRC'] = TDRC(encoding=3, text=[metadata['year']])
            if metadata.get('genre'):
                audio.tags['TCON'] = TCON(encoding=3, text=[metadata['genre']])
            if metadata.get('artwork'):
                audio.tags.add(
                    APIC(
                        encoding=3,
                        mime='image/jpeg',
                        type=3, # 3 is for cover image
                        desc=u'Cover',
                        data=metadata['artwork']
                    )
                )
            
            audio.save(v2_version=3)
            print(f"Metadata applied")

            # Rename file to match title, without overwriting an existing file
            title = metadata.get('title')
            base = self._safe_filename(title) if title else None
            if not base:
                return file_path

            directory = os.path.dirname(file_path)
            new_path = os.path.join(directory, f"{base}.mp3")

            # Keep the file as-is if it already matches its final name
            if os.path.normpath(new_path).lower() == os.path.normpath(file_path).lower():
                return file_path

            counter = 1
            candidate = new_path
            while os.path.exists(candidate):
                candidate = os.path.join(directory, f"{base} ({counter}).mp3")
                counter += 1

            os.rename(file_path, candidate)
            print(f"File renamed to: {os.path.basename(candidate)}")
            return candidate
        except Exception as e:
            print(f"Error applying metadata: {str(e)}")
            return None