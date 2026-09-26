import json
import sqlite3
import threading
import time
import uuid

import settings


class DownloadManager:
    """Background download queue with a bounded concurrency pool and persisted job log."""

    def __init__(self, api):
        self.api = api
        self._queue = []
        self._active = 0
        self._scheduler_started = False
        self._scheduler = None
        self._idle_cb = None
        self._lock = threading.Lock()
        self._events = {}

    # ==========================
    # Submission
    # ==========================
    def submit(self, data, block=True):
        """Enqueue a download.

        ``data`` matches the legacy payload: either a string (YouTube URL or
        search query) or a dict with url/title/artist.

        When ``block`` is True the caller waits for the download to finish and
        receives the legacy result (dict on success, error string on failure).
        """
        payload = data if isinstance(data, dict) else {'url': data if isinstance(data, str) else ''}
        url = str(payload.get('url') or '').strip()
        title = str(payload.get('title') or '')
        artist = str(payload.get('artist') or '')
        is_podcast = bool(payload.get('is_podcast'))

        if not url:
            url = f"{title} {artist} audio".strip()
        if not url:
            return "Download failed: empty search or url"

        qid = uuid.uuid4().hex
        job = {
            'id': qid,
            'key': str(payload.get('url') or title or url),
            'data': payload,
            'url': url,
            'title': title,
            'artist': artist,
            'is_podcast': is_podcast,
            'status': 'queued',
            'progress': 0,
            'error': None,
            'filename': None,
            'result': None,
            'event': None,
        }
        if block:
            job['event'] = threading.Event()
            self._events[qid] = job['event']

        with self._lock:
            self._queue.append(job)
        self._insert(job)
        self._ensure_scheduler()

        if block:
            ev = self._events[qid]
            ev.wait()
            self._events.pop(qid, None)
            return job['result']
        return True

    def retry(self, data):
        """Re-enqueue a previously failed job or an arbitrary download spec."""
        payload = data if isinstance(data, dict) else {}
        if payload.get('job_id'):
            row = self._fetch_job(payload.get('job_id'))
            if row:
                payload = {'url': row[0], 'title': row[1], 'artist': row[2],
                           'is_podcast': bool(row[3]) if len(row) > 3 else False}
            else:
                return False
        url = str(payload.get('url') or '').strip()
        if not url and payload.get('title'):
            url = f"{payload.get('title')} {payload.get('artist') or ''} audio".strip()
        if not url:
            return False
        self.submit(payload, block=False)
        return True

    # ==========================
    # Scheduler
    # ==========================
    def set_idle_callback(self, callback):
        with self._lock:
            self._idle_cb = callback

    def _concurrency_limit(self):
        try:
            limit = int(getattr(settings, 'limit_downloads', 3))
        except (TypeError, ValueError):
            limit = 3
        return max(1, min(limit, 20))

    def _ensure_scheduler(self):
        if self._scheduler_started:
            return
        self._scheduler_started = True
        self._scheduler = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._scheduler.start()

    def _scheduler_loop(self):
        while True:
            job = None
            callback = None
            with self._lock:
                limit = self._concurrency_limit()
                if self._queue and self._active < limit:
                    job = self._queue.pop(0)
                    self._active += 1
                elif not self._queue and self._active == 0 and self._idle_cb:
                    callback = self._idle_cb
                    self._idle_cb = None

            if job:
                threading.Thread(target=self._run_job, args=(job,), daemon=True).start()
            elif callback:
                try:
                    callback()
                except Exception as e:
                    print(f" [Download] Idle callback error: {e}")
                time.sleep(0.25)
            else:
                time.sleep(0.25)

    # ==========================
    # Job execution
    # ==========================
    def _run_job(self, job):
        try:
            self._update(job, 'downloading', progress=0)

            def progress_callback(d):
                status = d.get('status')
                if status == 'downloading':
                    total = d.get('total_bytes') or d.get('total_bytes_estimate')
                    downloaded = d.get('downloaded_bytes')
                    if total and downloaded:
                        self._update(job, 'downloading', progress=(downloaded / total) * 90)
                        self._push_progress(job, (downloaded / total) * 90, None)
                elif status == 'finished':
                    self._update(job, 'downloading', progress=90)
                    self._push_progress(job, 90, 'Processing...')
                elif status == 'processing_metadata':
                    self._update(job, 'downloading', progress=95)
                    self._push_progress(job, 95, 'Metadata...')
                elif status == 'finished_all':
                    self._update(job, 'downloading', progress=100)
                    self._push_progress(job, 100, 'Done!')

            result = self.api.downloader.download_song(
                job['url'],
                progress_callback,
                dest_path=settings.podcasts_path if job.get('is_podcast') else None,
                is_podcast=bool(job.get('is_podcast')),
            )

            if isinstance(result, dict) and result.get('status') == 'success':
                job['result'] = result
                job['filename'] = result.get('filename')
                self._save_song_record(result, is_podcast=bool(job.get('is_podcast')))
                self._update(job, 'done', progress=100)
            else:
                error = str(result) if isinstance(result, str) else "Download failed"
                job['result'] = error
                job['error'] = error
                self._update(job, 'failed', progress=100)
        except Exception as e:
            print(f" [Python] Download error: {e}")
            error = f"Download failed: {e}"
            job['result'] = error
            job['error'] = error
            self._update(job, 'failed', progress=100)
        finally:
            if job['event']:
                job['event'].set()
            with self._lock:
                self._active = max(0, self._active - 1)

    def _save_song_record(self, result, is_podcast=False):
        table = 'Podcasts' if is_podcast else 'Songs'
        try:
            with sqlite3.connect(self.api.db_path) as conn:
                conn.execute(
                    f"""INSERT INTO {table} (file, downloaded_link, title, artist, date_download)
                       VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(file) DO UPDATE SET
                           downloaded_link = excluded.downloaded_link,
                           title = excluded.title,
                           artist = excluded.artist,
                           date_download = CURRENT_TIMESTAMP""",
                    (result.get('filename'), result.get('source_url'), result.get('title'), result.get('artist'))
                )
        except Exception as db_err:
            print(f" [Python] DB error saving download: {db_err}")

    # ==========================
    # Job log (persistence)
    # ==========================
    def _insert(self, job):
        try:
            with sqlite3.connect(self.api.db_path) as conn:
                try:
                    conn.execute("ALTER TABLE Download_Queue ADD COLUMN is_podcast INTEGER DEFAULT 0")
                except Exception:
                    pass
                conn.execute(
                    "INSERT INTO Download_Queue (qid, url, title, artist, status, progress, is_podcast) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (job['id'], job['url'], job['title'], job['artist'], job['status'], job['progress'],
                     1 if job.get('is_podcast') else 0)
                )
        except Exception as e:
            print(f" [Download] DB insert error: {e}")

    def _update(self, job, status, progress=None):
        job['status'] = status
        if progress is not None:
            job['progress'] = progress
        try:
            with sqlite3.connect(self.api.db_path) as conn:
                conn.execute(
                    "UPDATE Download_Queue SET status = ?, progress = ?, error = ?, filename = ?, updated_at = CURRENT_TIMESTAMP WHERE qid = ?",
                    (job['status'], job['progress'], job['error'], job['filename'], job['id'])
                )
        except Exception as e:
            print(f" [Download] DB update error: {e}")

    def _fetch_job(self, job_id):
        try:
            with sqlite3.connect(self.api.db_path) as conn:
                try:
                    conn.execute("ALTER TABLE Download_Queue ADD COLUMN is_podcast INTEGER DEFAULT 0")
                except Exception:
                    pass
                row = conn.execute(
                    "SELECT url, title, artist, is_podcast FROM Download_Queue WHERE qid = ? ORDER BY id DESC LIMIT 1",
                    (str(job_id),)
                ).fetchone()
            return row
        except Exception as e:
            print(f" [Download] fetch job error: {e}")
            return None

    def get_jobs(self, limit=15):
        try:
            limit = max(1, min(int(limit), 100))
            with sqlite3.connect(self.api.db_path) as conn:
                rows = conn.execute(
                    "SELECT id, qid, url, title, artist, status, progress, error, filename, created_at, updated_at "
                    "FROM Download_Queue ORDER BY id DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [
                {
                    'id': r[0], 'qid': r[1], 'url': r[2], 'title': r[3], 'artist': r[4],
                    'status': r[5], 'progress': r[6], 'error': r[7], 'filename': r[8],
                    'created_at': r[9], 'updated_at': r[10],
                }
                for r in rows
            ]
        except Exception as e:
            print(f" [Python] Error loading download jobs: {e}")
            return []

    # ==========================
    # JS progress feed
    # ==========================
    def _push_progress(self, job, percent, text):
        window = getattr(self.api, '_window', None)
        if not window:
            return
        key = json.dumps(job['key'])
        percent_js = 'null' if percent is None else repr(float(percent))
        text_js = 'null' if text is None else json.dumps(str(text))
        code = (
            f"if (typeof window.update_download_progress === 'function') "
            f"window.update_download_progress({key}, {percent_js}, {text_js});"
        )
        try:
            window.evaluate_js(code)
        except Exception:
            pass