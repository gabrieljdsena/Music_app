import json
import os
import base64
import winsdk.windows.media as media
import winsdk.windows.media.playback as playback
from winsdk.windows.storage.streams import RandomAccessStreamReference
from winsdk.windows.foundation import Uri
import tempfile

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

    def update_overlay(self, title, artist, cover_art=None):
        updater = self._smtc.display_updater
        updater.type = media.MediaPlaybackType.MUSIC
        updater.music_properties.title = str(title)
        updater.music_properties.artist = str(artist)
        
        # Set Thumbnail
        if cover_art:
            try:
                temp_dir = tempfile.gettempdir()
                thumb_path = os.path.join(temp_dir, 'music_player_thumb.jpg')
                valid_thumb = False
                
                if cover_art.startswith('data:image'):
                    try:
                        header, encoded = cover_art.split(",", 1)
                        data = base64.b64decode(encoded)
                        with open(thumb_path, "wb") as f:
                            f.write(data)
                        valid_thumb = True
                    except Exception as e:
                        print(f" [Python] Failed to decode/save base64 thumbnail: {e}")
                elif os.path.exists(cover_art):
                    thumb_path = cover_art
                    valid_thumb = True
                    
                if valid_thumb:
                    # Verify file exists before creating URI
                    if os.path.exists(thumb_path):
                        file_uri = Uri(f"file:///{thumb_path.replace(os.sep, '/')}")
                        updater.thumbnail = RandomAccessStreamReference.create_from_uri(file_uri)
            except Exception as e:
                print(f" [Python] Failed to set thumbnail: {e}")
                import traceback
                traceback.print_exc()
        
        updater.update()
