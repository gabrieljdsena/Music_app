import os
from yt_dlp import YoutubeDL
import urllib.request
import urllib.parse
import json
import re
import settings
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, APIC, TCON

class MusicDownloader:
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

    def download_song(self, search, progress_callback=None):
        ffmpeg_path = os.path.join(os.path.dirname(__file__), 'ffmpeg', 'bin')
        
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
            'outtmpl': os.path.join(appdata_path, '%(title)s'),
            'ffmpeg_location': ffmpeg_path
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
                    
            if progress_callback:
                progress_callback({'status': 'processing_metadata'})
                
            # Remove anything inside parentheses, including the parentheses, and strip trailing spaces
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
            
            output_file = os.path.join(appdata_path, f"{info.get('title')}.mp3")
            self.apply_metadata(output_file, metadata)
            
            if progress_callback:
                progress_callback({'status': 'finished_all'})

            # Build the final filename after metadata rename
            final_title = metadata.get('title', info.get('title', 'Unknown'))
            final_filename = f"{final_title}.mp3"
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
    def apply_metadata(self,file_path, metadata):
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

            # Rename file to match title
            title = metadata.get('title', 'Unknown')
            directory = os.path.dirname(file_path)
            new_file_path = os.path.join(directory, f"{title}.mp3")
            
            # If file already exists with that name, don't rename
            if new_file_path != file_path:
                if os.path.exists(new_file_path):
                    os.remove(new_file_path)
                os.rename(file_path, new_file_path)
                print(f"File renamed to: {title}.mp3")

            return True
        except Exception as e:
            print(f"Error applying metadata: {str(e)}")
            return False