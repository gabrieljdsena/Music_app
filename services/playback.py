import os
import json
import math
import random
import time
import sqlite3
import threading
import pygame
import settings
from mutagen.id3 import ID3
import base64

# Custom pygame event fired the instant the music stream ends (gapless handoff)
MUSIC_END_EVENT = pygame.USEREVENT + 1
# Ramp granularity: 50 ms steps keep volume changes smooth (no zipper noise)
_XFADE_STEP_SECONDS = 0.05
_XFADE_MAX_SECONDS = 12.0

class PlaybackController:
    def __init__(self, api):
        self.api = api
        self.next_songs = []
        self.prev_songs = []
        self.unshuffled_song_list = []
        self.current_playlist_id = None
        self.is_custom_queue = False
        
        self.current_time_offset = 0
        self.last_play_time = 0
        self.pause_time = 0
        self.fallback_to_general_list = True

        # --- Crossfade / gapless state ---
        # _out is the output currently carrying the song: 'music'
        # (pygame.mixer.music) or 'chan' (dedicated Sound channel holding
        # the song after a completed crossfade).
        try:
            self.crossfade_enabled = bool(getattr(settings, 'crossfade_enabled', False))
        except Exception:
            self.crossfade_enabled = False
        try:
            self.crossfade_seconds = float(getattr(settings, 'crossfade_seconds', 5) or 0)
        except (TypeError, ValueError):
            self.crossfade_seconds = 5.0
        self._out = 'music'
        self._xfade_chan = None
        self._xfade_sound = None
        self._xfade_start = 0.0
        self._xfade_pause_pos = 0.0
        self._ramping = False
        self._xfade_gen = 0
        self._finish_ramp_now = False
        self._ramp_start_time = 0.0
        self._xfade_lock = threading.RLock()
        self._threads_started = False

    # ==========================
    # Queue persistence
    # ==========================
    # ==========================
    # Queue source persistence
    # ==========================
    #: Where the current queue is playing from. Stored as JSON so a restart
    #: can rebuild the queue from the right place (playlist, daily mix,
    #: artist, album, ...). Anything else falls back to the general list.
    VALID_SOURCE_TYPES = (
        'playlist', 'daily_mix', 'all_songs',
        'artist', 'album', 'recently_played', 'recently_downloaded',
        'podcast',
    )

    def _base_for(self, song):
        """Songs folder vs podcasts folder for a song dict (or current song)."""
        try:
            if isinstance(song, dict) and song.get('IsPodcast'):
                return settings.podcasts_path
            if song is None and (getattr(self.api, 'last_song', None) or {}).get('IsPodcast'):
                return settings.podcasts_path
        except Exception:
            pass
        return settings.path

    def _audio_path(self, song):
        """Full audio path for a song dict."""
        return os.path.join(self._base_for(song), str(song.get('File')))

    def _persist_source(self, source):
        """Persist the playback context ({'type':..., 'id':...} or None)."""
        try:
            with sqlite3.connect(self.api.db_path) as conn:
                conn.execute(
                    "UPDATE Settings SET queue_source = ? WHERE id = 1",
                    (json.dumps(source) if source else None,),
                )
        except Exception as e:
            print(f" [Python] Error persisting queue source: {e}")

    def _read_source(self):
        """Read back the persisted playback context (None if absent/invalid)."""
        try:
            with sqlite3.connect(self.api.db_path) as conn:
                row = conn.execute("SELECT queue_source FROM Settings WHERE id = 1").fetchone()
            if not row or not row[0]:
                return None
            src = json.loads(row[0])
            if not isinstance(src, dict) or src.get('type') not in self.VALID_SOURCE_TYPES:
                return None
            return src
        except Exception:
            return None

    def _persist_queue(self):
        """Persist only customized queues (playlist/home queues are re-derivable)."""
        try:
            with sqlite3.connect(self.api.db_path) as conn:
                if self.is_custom_queue:
                    files = [s.get('File') for s in self.next_songs if s.get('File')]
                    conn.execute(
                        "UPDATE Settings SET queue_songs = ?, custom_queue = 1 WHERE id = 1",
                        (json.dumps(files),)
                    )
                else:
                    conn.execute(
                        "UPDATE Settings SET queue_songs = NULL, custom_queue = 0 WHERE id = 1"
                    )
        except Exception as e:
            print(f" [Python] Error persisting queue: {e}")

    def restore_saved_queue(self, current_song):
        """Restore a persisted customized queue on startup.

        Returns True when the queue was restored, False to let the caller fall
        back to the previous populate_* behavior.
        """
        try:
            with sqlite3.connect(self.api.db_path) as conn:
                row = conn.execute(
                    "SELECT queue_songs, custom_queue FROM Settings WHERE id = 1"
                ).fetchone()
        except Exception as e:
            print(f" [Python] Error reading saved queue: {e}")
            return False

        if not row or not row[1]:
            return False

        try:
            files = json.loads(row[0] or '[]')
        except Exception:
            files = []

        restored = []
        for file_name in files:
            # Songs first, then podcasts: queues may mix both libraries.
            file_path = os.path.join(settings.path, file_name)
            is_podcast = False
            if not os.path.exists(file_path):
                pod_path = os.path.join(settings.podcasts_path, file_name)
                if os.path.exists(pod_path):
                    file_path = pod_path
                    is_podcast = True
            if os.path.exists(file_path):
                song = self.api.metadata.get_song_metadata(file_path, file_name)
                if is_podcast:
                    song['IsPodcast'] = True
                restored.append(song)

        if not restored:
            return False

        self.next_songs = restored
        self.prev_songs = []
        self.unshuffled_song_list = list(restored)
        self.is_custom_queue = True
        self.fallback_to_general_list = False
        self.current_playlist_id = None

        if getattr(self.api, '_window', None):
            self.api._window.evaluate_js(f"""
                window.is_custom_queue = true;
                window.queue_songs = {json.dumps(self.next_songs)};
                if (typeof window.update_queue_ui === 'function') {{
                    window.update_queue_ui();
                }}
            """)
        return True

    def populate_queue(self, current_song):
        self.next_songs.clear()
        self.fallback_to_general_list = True
        self.prev_songs.clear()
        self.current_playlist_id = None
        self.is_custom_queue = False
        self.unshuffled_song_list = list(self.api.song_list)
        # The general library list is the context from here on.
        self._persist_source({'type': 'all_songs', 'id': None})

        found = False
        for song in self.api.song_list:
            if found:
                self.next_songs.append(song)
            elif song.get('File') == current_song.get('File'):
                found = True
            else:
                self.prev_songs.append(song)

        if not found and len(self.api.song_list) > 0:
            self.next_songs = list(self.api.song_list)
            self.prev_songs.clear()

        try:
            with sqlite3.connect(self.api.db_path) as conn:
                conn.execute("UPDATE Settings SET current_playlist = NULL")
        except Exception as e:
            print(f" [Python] Database error clearing current_playlist: {e}")

        if getattr(self.api, '_window', None):
            self.api._window.evaluate_js(f"""
                window.is_custom_queue = false;
                window.queue_songs = {json.dumps(self.next_songs)};
                if (typeof window.update_queue_ui === 'function') {{
                    window.update_queue_ui();
                }}
            """)

    def populate_queue_from_list(self, current_song, song_list, playlist_id=None, source=None):
        self.next_songs.clear()
        self.fallback_to_general_list = False
        self.prev_songs.clear()
        self.current_playlist_id = playlist_id
        self.is_custom_queue = False
        self.unshuffled_song_list = list(song_list)

        if source is not None:
            self._persist_source(source)
        elif playlist_id is not None:
            self._persist_source({'type': 'playlist', 'id': playlist_id})
        # else: internal re-derivation (e.g. unshuffle) keeps the stored source

        found = False
        for song in song_list:
            if found:
                self.next_songs.append(song)
            elif song.get('File') == current_song.get('File'):
                found = True
            else:
                self.prev_songs.append(song)

        try:
            with sqlite3.connect(self.api.db_path) as conn:
                conn.execute("UPDATE Settings SET current_playlist = ?", (playlist_id,))
        except Exception as e:
            print(f" [Python] Database error saving current_playlist: {e}")
                
        if getattr(self.api, '_window', None):
            self.api._window.evaluate_js(f"""
                window.is_custom_queue = false;
                window.queue_songs = {json.dumps(self.next_songs)};
                if (typeof window.update_queue_ui === 'function') {{
                    window.update_queue_ui();
                }}
            """)
            
    def jump_to_queue_index(self, index):
        if 0 <= index < len(self.next_songs):
            self.next_songs = self.next_songs[index+1:]
            self._persist_queue()

    def add_to_queue(self, song_data):
        self.next_songs.append(song_data)
        self.is_custom_queue = True
        self._persist_queue()
        if getattr(self.api, '_window', None):
            self.api._window.evaluate_js(f"""
                window.queue_songs = {json.dumps(self.next_songs)};
                if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
            """)

    def next_to_queue(self, song_data):
        self.next_songs.insert(0, song_data)
        self.is_custom_queue = True
        self._persist_queue()
        if getattr(self.api, '_window', None):
            self.api._window.evaluate_js(f"""
                window.queue_songs = {json.dumps(self.next_songs)};
                if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
            """)

    def clear_queue(self):
        self.next_songs.clear()
        self.fallback_to_general_list = False
        self.is_custom_queue = False
        self._persist_queue()
        if getattr(self.api, '_window', None):
            self.api._window.evaluate_js(f"""
                window.queue_songs = [];
                if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
            """)
        
    def remove_from_queue(self, index):
        if 0 <= index < len(self.next_songs):
            self.next_songs.pop(index)
            self.is_custom_queue = True
            self._persist_queue()
            if getattr(self.api, '_window', None):
                self.api._window.evaluate_js(f"""
                    window.queue_songs = {json.dumps(self.next_songs)};
                    if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                """)
            
    def reorder_queue(self, old_index, new_index):
        if 0 <= old_index < len(self.next_songs) and 0 <= new_index <= len(self.next_songs):
            item = self.next_songs.pop(old_index)
            self.next_songs.insert(new_index, item)
            self.is_custom_queue = True
            self._persist_queue()
            if getattr(self.api, '_window', None):
                self.api._window.evaluate_js(f"""
                    window.queue_songs = {json.dumps(self.next_songs)};
                    if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                """)
    
    def toggle_shuffle(self):
        self.api.shuffle = not getattr(self.api, 'shuffle', False)
        if self.api.shuffle and self.next_songs:
            random.shuffle(self.next_songs)
            self._persist_queue()
            if getattr(self.api, '_window', None):
                self.api._window.evaluate_js(f"""
                    window.queue_songs = {json.dumps(self.next_songs)};
                    if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                """)
        elif not self.api.shuffle:
            if self.api.last_song and self.api.last_song.get('File'):
                if self.unshuffled_song_list:
                    self.populate_queue_from_list(self.api.last_song, self.unshuffled_song_list, self.current_playlist_id)
                elif self.current_playlist_id is not None:
                    playlist_songs = self.api.db.get_playlist_songs(self.current_playlist_id)
                    if playlist_songs:
                        self.populate_queue_from_list(self.api.last_song, playlist_songs, self.current_playlist_id)
                    else:
                        self.populate_queue(self.api.last_song)
                else:
                    self.populate_queue(self.api.last_song)
        return self.api.shuffle

    # ==========================
    # Crossfade / gapless engine
    # ==========================
    def _user_volume(self):
        try:
            return max(0.0, min(1.0, float(settings.volume)))
        except (TypeError, ValueError):
            return 1.0

    def _ensure_threads(self):
        """Start the end-of-stream + crossfade monitors once the mixer is ready."""
        if self._threads_started:
            return
        try:
            if not pygame.mixer.get_init():
                return
        except Exception:
            return
        try:
            pygame.mixer.music.set_endevent(MUSIC_END_EVENT)
        except Exception as e:
            print(f" [Python] Could not set music endevent: {e}")
            return
        self._threads_started = True
        threading.Thread(target=self._end_event_loop, daemon=True, name="xfade-endevent").start()
        threading.Thread(target=self._xfade_monitor_loop, daemon=True, name="xfade-monitor").start()

    def _ramp_alive(self):
        """Whether a ramp is in flight AND still within its time budget.

        A ramp owns its track ending, so end-detection must defer to it.
        The budget (ramp length + margin) guarantees an orphaned ramp can
        never wedge the safety nets forever.
        """
        with self._xfade_lock:
            if not self._ramping:
                return False
            try:
                budget = max(1.0, min(_XFADE_MAX_SECONDS, float(self.crossfade_seconds or 0))) + 5.0
            except (TypeError, ValueError):
                budget = 10.0
            return (time.time() - self._ramp_start_time) < budget

    def _recover_stale_ramp(self):
        with self._xfade_lock:
            if self._ramping:
                try:
                    budget = max(1.0, min(_XFADE_MAX_SECONDS, float(self.crossfade_seconds or 0))) + 5.0
                except (TypeError, ValueError):
                    budget = 10.0
                if (time.time() - self._ramp_start_time) >= budget:
                    self._ramping = False
                    self._finish_ramp_now = False
                    return True
        return False

    def _end_event_loop(self):
        """Gapless handoff: advance the moment the music stream ends."""
        while True:
            try:
                for event in pygame.event.get():
                    try:
                        if event.type == MUSIC_END_EVENT:
                            self._on_music_ended()
                    except Exception as e:
                        print(f" [Python] Music-end handler error: {e}")
                time.sleep(0.05)
            except Exception:
                time.sleep(0.2)

    def _on_music_ended(self):
        if self._out != 'music':
            return
        if not getattr(self.api, 'playing', False):
            return
        with self._xfade_lock:
            ramping = self._ramping
            if ramping:
                # A ramp was in flight; finish it immediately instead of
                # advancing a second time.
                self._finish_ramp_now = True
                return
        # Ignore stale endevents (e.g. posted by an explicit stop() of an
        # already-finished stream): only advance if the current song really
        # had time to finish.
        try:
            duration = float((self.api.last_song or {}).get('Duration') or 0)
        except (TypeError, ValueError):
            duration = 0
        try:
            offset = float(self.current_time_offset or 0)
        except (TypeError, ValueError):
            offset = 0
        if duration > 0 and (time.time() - self.last_play_time - offset) < duration - 0.5:
            return
        self.play_next(auto=True)

    def _xfade_monitor_loop(self):
        while True:
            try:
                time.sleep(0.2)
                if not getattr(self.api, 'playing', False):
                    continue
                if getattr(self.api, 'first_play', False):
                    continue
                with self._xfade_lock:
                    out = self._out
                    ramping = self._ramping
                if ramping:
                    if self._recover_stale_ramp():
                        ramping = False
                    else:
                        continue
                if out == 'music':
                    if self._music_ended():
                        self.play_next(auto=True)
                        continue
                    self._maybe_start_crossfade_from_music()
                else:
                    self._maybe_advance_from_channel()
            except Exception as e:
                print(f" [Python] Crossfade monitor error: {e}")
                time.sleep(1.0)

    def _music_ended(self):
        """Fast end-of-stream detection (backup for the endevent thread).

        Returns True only when the stream genuinely finished: not busy,
        no position, past startup grace, and played time reached the end.
        """
        try:
            if pygame.mixer.music.get_busy():
                return False
            if pygame.mixer.music.get_pos() != -1:
                return False
        except Exception:
            return False
        if time.time() - self.last_play_time < 1.0:
            return False  # just (re)started; transient idle state
        try:
            duration = float((self.api.last_song or {}).get('Duration') or 0)
        except (TypeError, ValueError):
            duration = 0
        try:
            offset = float(self.current_time_offset or 0)
        except (TypeError, ValueError):
            offset = 0
        elapsed = time.time() - self.last_play_time - offset
        if duration > 0 and elapsed < duration - 0.5:
            return False  # ended suspiciously early; ignore
        return True

    def _crossfade_target(self):
        """Next song eligible for a crossfade, or None."""
        if not self.crossfade_enabled or self.crossfade_seconds <= 0:
            return None
        if getattr(self.api, 'repeat', False):
            return None
        if not self.next_songs:
            return None
        nxt = self.next_songs[0]
        cur = (self.api.last_song or {}).get('File')
        if not nxt.get('File') or nxt.get('File') == cur:
            return None
        if not os.path.exists(os.path.join(self._base_for(nxt), str(nxt.get('File')))):
            return None
        return nxt

    def _maybe_start_crossfade_from_music(self):
        try:
            duration = float((self.api.last_song or {}).get('Duration') or 0)
        except (TypeError, ValueError):
            return
        if duration <= self.crossfade_seconds:
            return  # too short to overlap; the endevent handoff covers it
        try:
            pos = self.get_current_pos()
        except Exception:
            return
        remaining = duration - pos
        if remaining <= 0.5 or remaining > self.crossfade_seconds:
            return
        nxt = self._crossfade_target()
        if nxt is None:
            return
        self._enrich_cover(nxt)
        self._start_ramp(to_channel=True, next_song=nxt)

    def _maybe_advance_from_channel(self):
        try:
            duration = float((self.api.last_song or {}).get('Duration') or 0)
        except (TypeError, ValueError):
            duration = 0
        elapsed = time.time() - self._xfade_start
        if duration > 0:
            if elapsed < duration - 0.05:
                return
        else:
            try:
                if self._xfade_chan is not None and self._xfade_chan.get_busy():
                    return
            except Exception:
                pass
        nxt = self._crossfade_target()
        if nxt is not None and duration > self.crossfade_seconds:
            self._enrich_cover(nxt)
            self._start_ramp(to_channel=False, next_song=nxt)
            return
        # Plain gapless advance back through the music module
        with self._xfade_lock:
            self._out = 'music'
        try:
            if self._xfade_chan is not None:
                self._xfade_chan.stop()
        except Exception:
            pass
        self._xfade_sound = None
        self.play_next(auto=True)

    @staticmethod
    def _enrich_cover(song):
        """Attach embedded cover art to a queue song dict (mutates in place)."""
        try:
            if song.get('CoverArt'):
                return
            base = settings.podcasts_path if song.get('IsPodcast') else settings.path
            file_path = os.path.join(base, str(song.get('File')))
            if not os.path.exists(file_path):
                return
            tags = ID3(file_path)
            for key in tags.keys():
                if key.startswith('APIC'):
                    apic = tags[key]
                    img_data = base64.b64encode(apic.data).decode('utf-8')
                    song['CoverArt'] = f"data:{apic.mime};base64,{img_data}"
                    break
        except Exception:
            pass

    def _start_ramp(self, to_channel, next_song):
        with self._xfade_lock:
            if self._ramping or not getattr(self.api, 'playing', False):
                return
            self._xfade_gen += 1
            gen = self._xfade_gen
            self._ramping = True
            self._ramp_start_time = time.time()
            self._finish_ramp_now = False
        threading.Thread(
            target=self._run_ramp, args=(gen, to_channel, next_song),
            daemon=True, name="xfade-ramp",
        ).start()

    def _run_ramp(self, gen, to_channel, next_song):
        """Overlap outgoing output into the next song, then commit."""
        try:
            seconds = max(0.5, min(_XFADE_MAX_SECONDS, float(self.crossfade_seconds or 0)))
        except (TypeError, ValueError):
            self._abort_ramp(gen)
            return
        nxt_path = os.path.join(self._base_for(next_song), str(next_song.get('File')))
        chan = None
        try:
            if to_channel:
                try:
                    sound = pygame.mixer.Sound(nxt_path)
                except Exception as e:
                    print(f" [Python] Crossfade load failed: {e}")
                    self._abort_ramp(gen)
                    return
                chan = pygame.mixer.find_channel(True)
                if chan is None:
                    print(" [Python] Crossfade: no free channel, skipping overlap.")
                    self._abort_ramp(gen)
                    return
                with self._xfade_lock:
                    if gen != self._xfade_gen:
                        stolen = True
                    else:
                        stolen = False
                        self._xfade_sound = sound
                        self._xfade_chan = chan
                if stolen:
                    try:
                        chan.stop()
                    except Exception:
                        pass
                    return
                chan.set_volume(0.0)
                chan.play(sound)
            else:
                # Pop first so a concurrent queue edit aborts before audio starts.
                try:
                    popped = self.next_songs.pop(0)
                    if popped.get('File') != next_song.get('File'):
                        self.next_songs.insert(0, popped)
                        raise ValueError("queue changed during crossfade")
                except ValueError:
                    self._abort_ramp(gen)
                    return
                except Exception as e:
                    print(f" [Python] Crossfade queue error: {e}")
                    self._abort_ramp(gen)
                    return
                try:
                    pygame.mixer.music.load(nxt_path)
                    pygame.mixer.music.play()
                    pygame.mixer.music.set_volume(0.0)
                except Exception as e:
                    print(f" [Python] Crossfade load failed: {e}")
                    self.next_songs.insert(0, popped)
                    self._abort_ramp(gen)
                    return
                with self._xfade_lock:
                    if gen != self._xfade_gen:
                        stolen = True
                    else:
                        stolen = False
                if stolen:
                    try:
                        pygame.mixer.music.stop()
                    except Exception:
                        pass
                    self.next_songs.insert(0, popped)
                    return
                self._persist_queue()
                self.last_play_time = time.time()
                self._commit_new_song_state(next_song)
        except Exception as e:
            print(f" [Python] Crossfade start error: {e}")
            self._abort_ramp(gen)
            return

        steps = max(30, min(240, int(seconds / _XFADE_STEP_SECONDS)))
        step_sleep = seconds / steps
        half_pi = math.pi / 2.0
        for i in range(1, steps + 1):
            time.sleep(step_sleep)
            with self._xfade_lock:
                if gen != self._xfade_gen:
                    return  # cancelled; the canceller owns cleanup
                finish_now = self._finish_ramp_now
            if finish_now:
                break
            if not getattr(self.api, 'playing', False):
                break  # pausing; snap to the new song, the toggle then pauses it
            try:
                vol = self._user_volume()
                t = i / steps
                # Equal-power crossfade (what mixers/DAWs use): cos^2 + sin^2
                # stays 1 so perceived loudness holds steady. A linear ramp
                # dips ~3 dB in the middle: the old track seems to vanish
                # while the new one creeps in.
                out_gain = math.cos(t * half_pi)
                in_gain = math.sin(t * half_pi)
                if to_channel:
                    pygame.mixer.music.set_volume(vol * out_gain)
                    if chan is not None:
                        chan.set_volume(vol * in_gain)
                else:
                    pygame.mixer.music.set_volume(vol * in_gain)
                    with self._xfade_lock:
                        live_chan = self._xfade_chan
                    if live_chan is not None:
                        live_chan.set_volume(vol * out_gain)
            except Exception:
                pass

        with self._xfade_lock:
            if gen != self._xfade_gen:
                return
            self._finish_ramp_now = False
        try:
            vol = self._user_volume()
            if to_channel:
                try:
                    pygame.mixer.music.set_volume(0.0)
                except Exception:
                    pass
                if chan is not None:
                    try:
                        chan.set_volume(vol)
                    except Exception:
                        pass
                self._complete_ramp_to_channel(gen, next_song)
            else:
                try:
                    pygame.mixer.music.set_volume(vol)
                except Exception:
                    pass
                self._complete_ramp_to_music(gen)
        except Exception as e:
            print(f" [Python] Crossfade finish error: {e}")
            self._abort_ramp(gen)

    def _abort_ramp(self, gen):
        """Exit a ramp without committing (cleanup already done by caller)."""
        with self._xfade_lock:
            if gen == self._xfade_gen:
                self._ramping = False
                self._finish_ramp_now = False

    def _cancel_crossfade(self):
        """Abort any ramp and route audio back through the music module.

        Called before manual actions (explicit play, next/prev, seek) so
        they always operate on a single well-defined output.
        """
        with self._xfade_lock:
            self._xfade_gen += 1
            ramping = self._ramping
            out = self._out
            chan = self._xfade_chan
        if chan is not None:
            try:
                chan.stop()
            except Exception:
                pass
        if ramping or out == 'chan':
            try:
                pygame.mixer.music.set_volume(self._user_volume())
            except Exception:
                pass
        with self._xfade_lock:
            self._out = 'music'
            self._ramping = False
            self._finish_ramp_now = False
        # Note: a stale ramp thread may still be sleeping; it will see the
        # generation mismatch on wake and exit without touching state.

    def _settle_ramp(self):
        """If a ramp is in flight, finish it instantly (bounded wait)."""
        with self._xfade_lock:
            if not self._ramping:
                return
            self._finish_ramp_now = True
        deadline = time.time() + 1.5
        while time.time() < deadline:
            with self._xfade_lock:
                if not self._ramping:
                    break
            time.sleep(0.02)

    def sync_output_volume(self):
        """Apply the user volume to whichever output is active."""
        vol = self._user_volume()
        try:
            pygame.mixer.music.set_volume(vol)
        except Exception:
            pass
        try:
            with self._xfade_lock:
                ramping = self._ramping
                out = self._out
                chan = self._xfade_chan
            if not ramping and out == 'chan' and chan is not None:
                chan.set_volume(vol)
        except Exception:
            pass

    def _commit_new_song_state(self, next_song, shift_queue_ui=True):
        """Shared bookkeeping when a new song takes over (DB/history/UI)."""
        if self.api.last_song and self.api.last_song.get('File'):
            self.prev_songs.append(self.api.last_song)
        self.api.current_filename = next_song.get('File')
        self.api.last_song = next_song
        self.api.first_play = False
        self.last_play_time = time.time()
        self.current_time_offset = 0
        self.pause_time = 0
        try:
            with sqlite3.connect(self.api.db_path) as conn:
                conn.execute("UPDATE Settings SET current_song = ?", (self.api.current_filename,))
                # Podcasts stay out of the music history (daily mix, recents).
                if not next_song.get('IsPodcast'):
                    conn.execute("INSERT INTO Music_History (song_file) VALUES (?)", (self.api.current_filename,))
                    if self.current_playlist_id is not None:
                        conn.execute("INSERT INTO Playlist_History (playlist_id) VALUES (?)", (self.current_playlist_id,))
        except Exception as e:
            print(f" [Python] Database error: {e}")
        if hasattr(self.api, 'media_controls'):
            try:
                self.api.media_controls.update_overlay(
                    next_song.get('Title', 'Unknown'),
                    next_song.get('Artist', 'Unknown'),
                    next_song.get('CoverArt')
                )
            except Exception as e:
                print(f" [Python] Media overlay error: {e}")
        if getattr(self.api, '_window', None) and shift_queue_ui:
            try:
                self.api._window.evaluate_js(f"""
                    if (window.queue_songs && window.queue_songs.length > 0) {{
                        window.queue_songs.shift();
                        if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                    }}
                    window.current_playing_song = {json.dumps(next_song)};
                    if (typeof window.set_active_song_ui === 'function') {{ window.set_active_song_ui(window.current_playing_song); }}
                """)
            except Exception as e:
                print(f" [Python] queue UI error: {e}")
            try:
                self.api._window.evaluate_js(f"if (typeof window.playing_view === 'function') window.playing_view({json.dumps(next_song)})")
            except Exception as e:
                print(f" [Python] playing_view JS error: {e}")
            try:
                self.api._window.evaluate_js(f"if (typeof window.plaiyng_info === 'function') window.plaiyng_info({json.dumps(next_song)})")
            except Exception as e:
                print(f" [Python] plaiyng_info JS error: {e}")
            try:
                self.api._window.evaluate_js("if (typeof window.update_play_button_ui === 'function') { window.update_play_button_ui(true); }")
            except Exception as e:
                print(f" [Python] update_play_button_ui JS error: {e}")
        if hasattr(self.api, 'media_controls'):
            try:
                self.api.media_controls.set_playing(True)
            except Exception:
                pass
        self.api.playing = True

    def _complete_ramp_to_channel(self, gen, next_song):
        """Finish a music -> channel ramp: the channel now owns playback."""
        try:
            popped = self.next_songs.pop(0)
            if popped.get('File') != next_song.get('File'):
                self.next_songs.insert(0, popped)
                raise ValueError("queue changed during crossfade")
            self._persist_queue()
        except ValueError:
            try:
                if self._xfade_chan is not None:
                    self._xfade_chan.stop()
            except Exception:
                pass
            try:
                pygame.mixer.music.set_volume(self._user_volume())
            except Exception:
                pass
            with self._xfade_lock:
                if gen == self._xfade_gen:
                    self._ramping = False
            return
        except Exception as e:
            print(f" [Python] Crossfade queue error: {e}")
            with self._xfade_lock:
                if gen == self._xfade_gen:
                    self._ramping = False
            return
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        with self._xfade_lock:
            if gen == self._xfade_gen:
                self._out = 'chan'
                self._xfade_start = time.time()
                self._xfade_pause_pos = 0.0
                self._ramping = False
        self._commit_new_song_state(next_song)

    def _complete_ramp_to_music(self, gen):
        """Finish a channel -> music ramp: stop the old channel output."""
        try:
            if self._xfade_chan is not None:
                self._xfade_chan.stop()
        except Exception:
            pass
        self._xfade_sound = None
        with self._xfade_lock:
            if gen == self._xfade_gen:
                self._out = 'music'
                self._ramping = False

    def get_current_pos(self):
        if self.api.first_play:
            return 0

        # Crossfaded output lives on a Sound channel (no get_pos there),
        # so track it with wall time.
        if self._out == 'chan':
            if not self.api.playing:
                return self._xfade_pause_pos
            return max(0.0, time.time() - self._xfade_start)
             
        if not self.api.playing:
            return self.pause_time
                    
        pos = pygame.mixer.music.get_pos()
        
        if pos == -1:
            # A live ramp (or the endevent path) owns the track ending: never
            # hijack it here, or the fading-in song gets killed and restarted.
            if not self._ramp_alive() and self.api.playing and (time.time() - self.last_play_time > 3.0):
                self.play_next(auto=True)
            return 0
            
        return self.current_time_offset + (pos / 1000.0)

    def play_button(self, current_song=None, opening=False):
        self._ensure_threads()
        if current_song is None:
            if self.api.first_play:
                self._cancel_crossfade()
                if self.api.current_filename and os.path.exists(os.path.join(self._base_for(None), str(self.api.current_filename))):
                    try:
                        pygame.mixer.music.load(os.path.join(self._base_for(None), str(self.api.current_filename)))
                        pygame.mixer.music.play()
                        self.api.playing = True
                        self.api.first_play = False
                        if hasattr(self.api, 'media_controls'):
                            self.api.media_controls.set_playing(True)
                        return True
                    except Exception as e:
                        print(f" [Python] Force play error: {e}")
                return None
            if self.api.playing:
                # A pause during a ramp finishes the ramp first so the
                # settled output is what actually gets paused.
                self._settle_ramp()
                self.pause_time = self.get_current_pos()
                if self._out == 'chan':
                    self._xfade_pause_pos = self.pause_time
                if self._out == 'chan' and self._xfade_chan is not None:
                    try:
                        self._xfade_chan.pause()
                    except Exception:
                        pass
                else:
                    pygame.mixer.music.pause()
                self.api.playing = False
                if hasattr(self.api, 'media_controls'):
                    self.api.media_controls.set_playing(False)
            else:
                self._settle_ramp()
                if self._out == 'chan' and self._xfade_chan is not None:
                    try:
                        self._xfade_chan.unpause()
                    except Exception:
                        pass
                    self._xfade_start = time.time() - self.pause_time
                elif pygame.mixer.music.get_pos() == -1:
                    pygame.mixer.music.play()
                    self.current_time_offset = 0
                    self.last_play_time = time.time()
                else:
                    pygame.mixer.music.unpause()
                    try:
                        offset = float(self.current_time_offset or 0)
                    except (TypeError, ValueError):
                        offset = 0
                    self.last_play_time = time.time() - self.pause_time - offset
                self.api.playing = True
                if hasattr(self.api, 'media_controls'):
                    self.api.media_controls.set_playing(True)
            return self.api.playing

        self._cancel_crossfade()
        # Mark the switch instant so end-detection grace covers the
        # stop -> load -> play window.
        self.last_play_time = time.time()

        self.api.current_filename = current_song.get('File')
        last_filename = self.api.last_song.get('File')

        if not current_song.get('Duration'):
            # Lightweight entries (e.g. podcast listings) carry no duration:
            # read it now so progress and history math work.
            try:
                _full = self.api.metadata.get_song_metadata(
                    os.path.join(self._base_for(current_song), str(current_song.get('File'))),
                    str(current_song.get('File')))
                if _full.get('Duration'):
                    current_song['Duration'] = _full.get('Duration')
                if not current_song.get('CoverArt') and _full.get('CoverArt'):
                    current_song['CoverArt'] = _full.get('CoverArt')
            except Exception:
                pass
        
        if 'CoverArt' not in current_song or not current_song['CoverArt']:
            file_path = os.path.join(self._base_for(current_song), self.api.current_filename)
            if os.path.exists(file_path):
                try:
                    song_file = ID3(file_path)
                    for key in song_file.keys():
                        if key.startswith('APIC'):
                            apic = song_file[key]
                            img_data = base64.b64encode(apic.data).decode('utf-8')
                            current_song['CoverArt'] = f"data:{apic.mime};base64,{img_data}"
                            break
                except Exception:
                    pass

        if str(last_filename) != str(self.api.current_filename):
            # Stopping an idle stream can post a phantom endevent; skip it.
            try:
                if pygame.mixer.music.get_busy() or pygame.mixer.music.get_pos() != -1:
                    pygame.mixer.music.stop()
            except Exception:
                try:
                    pygame.mixer.music.stop()
                except Exception:
                    pass
            self.api.first_play = True
            self.current_time_offset = 0
        else:
            last_instance = self.api.last_song.get('_instanceId') or self.api.last_song.get('_historyId')
            curr_instance = current_song.get('_instanceId') or current_song.get('_historyId')
            if last_instance and curr_instance and str(last_instance) != str(curr_instance):
                pygame.mixer.music.stop()
                self.api.first_play = True
                self.current_time_offset = 0

        if self.api.first_play:
            if getattr(self.api, '_window', None):
                try:
                    self.api._window.evaluate_js(f"if (typeof window.playing_view === 'function') window.playing_view({json.dumps(current_song)})")
                except Exception as e:
                    print(f" [Python] playing_view JS error (page may not be ready yet): {e}")

            if getattr(self.api, '_window', None):
                try:
                    self.api._window.evaluate_js(f"if (typeof window.plaiyng_info === 'function') window.plaiyng_info({json.dumps(current_song)})")
                except Exception as e:
                    print(f" [Python] plaiyng_info JS error (page may not be ready yet): {e}")
            try:
                import logging
                logging.getLogger('hathor').info(
                    "play_button file=%r opening=%s", self.api.current_filename, opening)
                pygame.mixer.music.load(os.path.join(self._base_for(current_song), str(self.api.current_filename)))
            except Exception as e:
                import logging
                logging.getLogger('hathor').exception(
                    "Could not load song file %r", self.api.current_filename)
                print(f" [Python] Could not load song file {self.api.current_filename!r}: {e}")
                if getattr(self.api, '_window', None):
                    try:
                        self.api._window.evaluate_js(
                            "if (typeof Notyf !== 'undefined') { "
                            "new Notyf({ position: { x: 'right', y: 'bottom' } })"
                            ".error('Audio file not found. It may have been moved or deleted.'); }"
                        )
                    except Exception:
                        pass
                return False
            pygame.mixer.music.set_volume(settings.volume)
            pygame.mixer.music.play()
            self.current_time_offset = 0
            self.last_play_time = time.time()
            
            if opening:
                pygame.mixer.music.pause()
                self.api.playing = False
                self.pause_time = 0
                if hasattr(self.api, 'media_controls'):
                    self.api.media_controls.set_playing(False)
                if getattr(self.api, '_window', None):
                    try:
                        self.api._window.evaluate_js("if (typeof window.update_play_button_ui === 'function') { window.update_play_button_ui(false); }")
                        self.api._window.evaluate_js("if (typeof window.stop_visualizer === 'function') { window.stop_visualizer(); }")
                    except Exception as e:
                        print(f" [Python] update_play_button_ui JS error: {e}")
            else:
                self.api.playing = True
                if hasattr(self.api, 'media_controls'):
                    self.api.media_controls.set_playing(True)
                if getattr(self.api, '_window', None):
                    try:
                        self.api._window.evaluate_js("if (typeof window.update_play_button_ui === 'function') { window.update_play_button_ui(true); }")
                    except Exception as e:
                        print(f" [Python] update_play_button_ui JS error: {e}")

            try:
                with sqlite3.connect(self.api.db_path) as conn:
                    conn.execute("UPDATE Settings SET current_song = ?", (self.api.current_filename,))
                    # Podcasts stay out of the music history (daily mix, recents).
                    if not opening and not current_song.get('IsPodcast'):
                        conn.execute("INSERT INTO Music_History (song_file) VALUES (?)", (self.api.current_filename,))
                        if self.current_playlist_id is not None:
                            conn.execute("INSERT INTO Playlist_History (playlist_id) VALUES (?)", (self.current_playlist_id,))
            except Exception as e:
                print(f" [Python] Database error: {e}")
            
            if hasattr(self.api, 'media_controls'):
                # Never let a flaky OS overlay break playback of a loaded song.
                try:
                    self.api.media_controls.update_overlay(
                        current_song.get('Title', 'Unknown'),
                        current_song.get('Artist', 'Unknown'),
                        current_song.get('CoverArt')
                    )
                except Exception as e:
                    print(f" [Python] Media overlay error (playback unaffected): {e}")

        elif self.api.playing:
            self.pause_time = self.get_current_pos()
            pygame.mixer.music.pause()
            self.api.playing = False
            if hasattr(self.api, 'media_controls'):
                self.api.media_controls.set_playing(False)
        else:
            if pygame.mixer.music.get_pos() == -1:
                pygame.mixer.music.play()
                self.current_time_offset = 0
                self.last_play_time = time.time()
            else:
                pygame.mixer.music.unpause()
            self.api.playing = True
            if hasattr(self.api, 'media_controls'):
                self.api.media_controls.set_playing(True)

        self.api.last_song = current_song
        self.api.first_play = False
        return self.api.playing

    def play_next(self, auto=False):
        self._ensure_threads()
        self._cancel_crossfade()
        if getattr(self.api, 'repeat', False) and auto and self.api.last_song and self.api.last_song.get('File'):
            pygame.mixer.music.stop()
            self.api.first_play = True
            self.current_time_offset = 0
            self.play_button(self.api.last_song)
            return

        if self.next_songs:
            next_song = self.next_songs.pop(0)
            self._persist_queue()
            if self.api.last_song and self.api.last_song.get('File'):
                self.prev_songs.append(self.api.last_song)
            
            if getattr(self.api, '_window', None):
                self.api._window.evaluate_js(f"""
                    if (window.queue_songs && window.queue_songs.length > 0) {{
                        window.queue_songs.shift();
                        if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                    }}
                    window.current_playing_song = {json.dumps(next_song)};
                    if (typeof window.set_active_song_ui === 'function') {{ window.set_active_song_ui(window.current_playing_song); }}
                """)
            self.play_button(next_song)
        else:
            self.api.playing = False
            self.pause_time = 0
            if hasattr(self.api, 'media_controls'):
                self.api.media_controls.set_stopped()
            self.current_time_offset = 0
            if getattr(self.api, '_window', None):
                try:
                    self.api._window.evaluate_js("if (typeof window.update_play_button_ui === 'function') { window.update_play_button_ui(false); }")
                except Exception as e:
                    print(f" [Python] update_play_button_ui JS error: {e}")

    def play_prev(self):
        self._ensure_threads()
        self._cancel_crossfade()
        if self.prev_songs:
            prev_song = self.prev_songs.pop()
            
            if self.api.last_song and self.api.last_song.get('File'):
                self.next_songs.insert(0, self.api.last_song)
                self._persist_queue()
                
            if getattr(self.api, '_window', None):
                self.api._window.evaluate_js(f"""
                    if (window.queue_songs && window.current_playing_song) {{
                        window.queue_songs.unshift(window.current_playing_song);
                        if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                    }}
                    window.current_playing_song = {json.dumps(prev_song)};
                    if (typeof window.set_active_song_ui === 'function') {{ window.set_active_song_ui(window.current_playing_song); }}
                """)
                
            self.play_button(prev_song)
        else:
            prev_song = None
            if self.api.current_filename and getattr(self.api, 'song_list', None):
                for i, song in enumerate(self.api.song_list):
                    if song.get('File') == self.api.current_filename:
                        if i - 1 >= 0:
                            prev_song = self.api.song_list[i - 1]
                        break
            
            if prev_song:
                if self.api.last_song and self.api.last_song.get('File'):
                    self.next_songs.insert(0, self.api.last_song)
                    self._persist_queue()
                    
                if getattr(self.api, '_window', None):
                    self.api._window.evaluate_js(f"""
                        if (window.queue_songs && window.current_playing_song) {{
                            window.queue_songs.unshift(window.current_playing_song);
                            if (typeof window.update_queue_ui === 'function') {{ window.update_queue_ui(); }}
                        }}
                        window.current_playing_song = {json.dumps(prev_song)}; 
                        if (typeof window.set_active_song_ui === 'function') {{ window.set_active_song_ui(window.current_playing_song); }}
                    """)
                self.play_button(prev_song)
            else:
                # Restart the current song from the top (always via music).
                self._cancel_crossfade()
                try:
                    pygame.mixer.music.load(os.path.join(self._base_for(self.api.last_song), str(self.api.current_filename)))
                except Exception as e:
                    print(f" [Python] Could not load song file for restart: {e}")
                pygame.mixer.music.play(0, 0.0)
                self.current_time_offset = 0
                self.last_play_time = time.time()
                if not self.api.playing:
                    pygame.mixer.music.pause()
