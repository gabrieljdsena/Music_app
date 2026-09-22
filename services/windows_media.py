import json
import os
import base64
import hashlib
import io
import tempfile
import winsdk.windows.media as media
import winsdk.windows.media.playback as playback
from winsdk.windows.storage.streams import RandomAccessStreamReference
from winsdk.windows.foundation import Uri

class WindowsMediaOverlay:
    def __init__(self, api):
        self.api = api
        self._media_player = playback.MediaPlayer()
        self._smtc = self._media_player.system_media_transport_controls
        self._smtc.is_play_enabled = True
        self._smtc.is_pause_enabled = True
        self._smtc.is_next_enabled = True
        self._smtc.is_previous_enabled = True
        
        # Subscribe to button presses
        self._smtc.add_button_pressed(self.on_smtc_button_pressed)
        
    def on_smtc_button_pressed(self, sender, args):
        if args.button == media.SystemMediaTransportControlsButton.PLAY or args.button == media.SystemMediaTransportControlsButton.PAUSE:
            is_playing = self.api.play_button(None)
            if getattr(self.api, '_window', None) and is_playing is not None:
                self.api._window.evaluate_js(f"update_play_button_ui({'true' if is_playing else 'false'});")
                
        elif args.button == media.SystemMediaTransportControlsButton.NEXT:
            if hasattr(self.api, 'playback'):
                self.api.playback.play_next()
            else:
                self.api.play_next()
                
        elif args.button == media.SystemMediaTransportControlsButton.PREVIOUS:
            if hasattr(self.api, 'playback'):
                self.api.playback.play_prev()
            else:
                self.api.play_prev()

    def set_playing(self, is_playing):
        if is_playing:
            self._smtc.playback_status = media.MediaPlaybackStatus.PLAYING
        else:
            self._smtc.playback_status = media.MediaPlaybackStatus.PAUSED

    def set_stopped(self):
        self._smtc.playback_status = media.MediaPlaybackStatus.STOPPED

    @staticmethod
    def _save_cover(cover_art):
        """Save a cover to a temp JPEG file suitable for SMTC, or return None."""
        if not cover_art:
            return None

        # Accept a plain file path directly
        if isinstance(cover_art, str) and os.path.exists(cover_art):
            return cover_art

        if not (isinstance(cover_art, str) and cover_art.startswith('data:image')):
            return None

        try:
            header, encoded = cover_art.split(',', 1)
            mime = header.split(';')[0].split(':')[1] if ':' in header else 'image/jpeg'
            raw = base64.b64decode(encoded)
        except Exception as e:
            print(f" [Python] Failed to decode cover image: {e}")
            return None

        # Normalize to JPEG so SMTC can render it regardless of the source format (PNG/WebP...)
        data = raw
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(raw))
            img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=90)
            data = buf.getvalue()
        except Exception as e:
            print(f" [Python] Failed to convert cover to JPEG, using raw (mime={mime}): {e}")

        digest = hashlib.md5(data).hexdigest()[:16]
        thumb_path = os.path.join(tempfile.gettempdir(), f'music_player_cover_{digest}.jpg')
        if not os.path.exists(thumb_path):
            try:
                with open(thumb_path, "wb") as f:
                    f.write(data)
            except Exception as e:
                print(f" [Python] Failed to save cover file: {e}")
                return None
        return thumb_path

    def update_overlay(self, title, artist, cover_art=None):
        updater = self._smtc.display_updater
        updater.type = media.MediaPlaybackType.MUSIC
        updater.music_properties.title = str(title)
        updater.music_properties.artist = str(artist)

        # Set Thumbnail
        thumb_path = self._save_cover(cover_art)
        try:
            if thumb_path:
                file_uri = Uri(f"file:///{thumb_path.replace(os.sep, '/')}")
                updater.thumbnail = RandomAccessStreamReference.create_from_uri(file_uri)
            else:
                updater.thumbnail = None
        except Exception as e:
            print(f" [Python] Failed to set SMTC thumbnail: {e}")

        updater.update()