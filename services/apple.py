import json
import urllib.request
import urllib.parse


class AppleService:
    """Lookup artist artwork from the Apple iTunes Search API."""

    def __init__(self, api):
        self.api = api

    def _request_json(self, url):
        headers = {'User-Agent': 'MyMusicPlayer/1.0'}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                if response.status == 200:
                    return json.loads(response.read().decode())
        except Exception as e:
            print(f" [Python] Apple API request failed: {e}")
        return None

    def get_album_image(self, album, artist=None):
        """Return a high-res album cover URL from the Apple iTunes Search API, or None."""
        if not album or not str(album).strip():
            return None
        album = str(album).strip()
        artist = str(artist or "").strip() or None

        term = f"{album} {artist}" if artist else album
        query_string = urllib.parse.urlencode({
            "term": term,
            "entity": "album",
            "limit": 10
        })
        data = self._request_json(f"https://itunes.apple.com/search?{query_string}")
        if not data:
            return None
        return self._pick_album_artwork(data.get("results"), album, artist)

    @staticmethod
    def _pick_album_artwork(results, album, artist):
        """Prefer an exact album/artist match, then an exact album match, else Apple's top pick."""
        target_album = album.strip().lower()
        target_artist = (artist or "").strip().lower()

        def art(r):
            a = r.get("artworkUrl100")
            return a.replace("100x100bb", "600x600bb") if a else None

        if target_artist:
            for r in results or []:
                if (r.get("collectionName") or "").strip().lower() == target_album \
                        and (r.get("artistName") or "").strip().lower() == target_artist:
                    a = art(r)
                    if a:
                        return a
        for r in results or []:
            if (r.get("collectionName") or "").strip().lower() == target_album:
                a = art(r)
                if a:
                    return a
        for r in results or []:
            a = art(r)
            if a:
                return a
        return None

    @staticmethod
    def _pick_artist_id(results, artist):
        """Prefer an exact artist-name match, otherwise fall back to Apple's top result."""
        target = artist.strip().lower()
        for r in results or []:
            if (r.get("artistName") or "").strip().lower() == target and r.get("artistId"):
                return r.get("artistId")
        for r in results or []:
            if r.get("artistId"):
                return r.get("artistId")
        return None

    def get_artist_image(self, artist):
        """Return a high-res artist image URL, or None if not found."""
        if not artist or not str(artist).strip():
            return None
        artist = str(artist).strip()

        # 1. Find the matching iTunes artist.
        query_string = urllib.parse.urlencode({
            "term": artist,
            "entity": "musicArtist",
            "limit": 10
        })
        data = self._request_json(f"https://itunes.apple.com/search?{query_string}")
        if not data:
            return None
        artist_id = self._pick_artist_id(data.get("results"), artist)
        if not artist_id:
            return None

        # 2. Pull the artist's most-popular album artwork as their profile image.
        album_data = self._request_json(
            f"https://itunes.apple.com/lookup?id={artist_id}&entity=album&limit=5"
        )
        if not album_data:
            return None
        for item in album_data.get("results") or []:
            artwork = item.get("artworkUrl100")
            if artwork:
                return artwork.replace("100x100bb", "600x600bb")
        return None