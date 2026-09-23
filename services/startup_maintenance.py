"""Startup maintenance: FFmpeg auto-download + YouTube library auto-updates.

Runs in a background thread on app launch. Never raises -- all failures are
returned as status dicts so the app always starts even when offline.

Progress protocol: every function accepts ``progress_cb(event: dict)`` where
event looks like::

    {
        "stage": "ffmpeg" | "libs" | "done",
        "label": "human readable short label",
        "percent": 0-100 | None,
        "detail": "extra info (e.g. '12.4 / 86.0 MB')",
        "status": "working" | "done" | "error" | "skipped" | "up-to-date",
    }
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

# Official Windows essentials build (~80-90 MB). Stable URL, redirects to GitHub.
FFMPEG_ZIP_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

# YouTube search/download libraries we manage. Only packages already installed
# are touched -- nothing new is force-installed except yt-dlp itself.
LIB_CANDIDATES = ["yt-dlp", "youtubesearchpython", "youtube-search-python"]

_PYPI_TIMEOUT = 10


# ==========================
# Paths
# ==========================
def get_base_path():
    if hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_data_path():
    """Writable dir: next to the exe in frozen builds, project root in dev."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _candidate_ffmpeg_dirs():
    base = get_base_path()
    data = get_data_path()
    here = os.path.dirname(os.path.abspath(__file__))  # services/
    root = os.path.dirname(here)  # project root
    seen = []
    for d in (
        os.path.join(data, 'ffmpeg', 'bin'),
        os.path.join(base, 'ffmpeg', 'bin'),
        os.path.join(root, 'ffmpeg', 'bin'),
    ):
        if d not in seen:
            seen.append(d)
    return seen


def find_ffmpeg():
    """Return {'found': bool, 'exe': path|None, 'dir': dir|None}."""
    # 1. Anything on PATH wins (user-managed install).
    which = shutil.which('ffmpeg')
    if which:
        return {'found': True, 'exe': which, 'dir': os.path.dirname(which)}

    # 2. Bundled / previously auto-downloaded copies.
    for d in _candidate_ffmpeg_dirs():
        exe = os.path.join(d, 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
        if os.path.isfile(exe):
            return {'found': True, 'exe': exe, 'dir': d}
    return {'found': False, 'exe': None, 'dir': None}


def resolve_ffmpeg_location():
    """Dir string suitable for yt-dlp's ``ffmpeg_location``, or None for PATH."""
    found = find_ffmpeg()
    if found['found'] and found['dir']:
        # yt-dlp accepts a directory or a full exe path; dir keeps ffprobe adjacent.
        return found['dir']
    return None


def ffmpeg_install_dir():
    """Where to place an auto-downloaded copy (always writable)."""
    return _candidate_ffmpeg_dirs()[0]


# ==========================
# FFmpeg download
# ==========================
def _emit(progress_cb, stage, label, percent=None, detail="", status="working"):
    if not progress_cb:
        return
    try:
        progress_cb({
            'stage': stage,
            'label': label,
            'percent': percent,
            'detail': detail or "",
            'status': status,
        })
    except Exception as e:
        print(f" [Startup] progress callback error: {e}")


def _download_with_progress(url, dest_path, progress_cb):
    req = urllib.request.Request(url, headers={'User-Agent': 'Hathor-MusicPlayer/1.0'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        total = resp.getheader('Content-Length')
        total = int(total) if total and str(total).isdigit() else 0
        downloaded = 0
        chunk = 256 * 1024
        with open(dest_path, 'wb') as f:
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                f.write(buf)
                downloaded += len(buf)
                if total > 0:
                    pct = min(99.0, (downloaded / total) * 100.0)
                    _emit(progress_cb, 'ffmpeg', 'Downloading FFmpeg…',
                          percent=round(pct, 1),
                          detail=f"{downloaded / 1048576:.1f} / {total / 1048576:.1f} MB")
                else:
                    _emit(progress_cb, 'ffmpeg', 'Downloading FFmpeg…',
                          percent=None,
                          detail=f"{downloaded / 1048576:.1f} MB")
    return dest_path


def _extract_ffmpeg_bins(zip_path, target_bin_dir, progress_cb):
    _emit(progress_cb, 'ffmpeg', 'Extracting FFmpeg…', percent=100,
          detail='Unpacking ffmpeg.exe / ffprobe.exe')
    os.makedirs(target_bin_dir, exist_ok=True)
    wanted = {'ffmpeg.exe': None, 'ffprobe.exe': None}
    with zipfile.ZipFile(zip_path, 'r') as z:
        names = z.namelist()
        # Essentials zip layout: ffmpeg-<ver>-essentials_build/bin/ffmpeg.exe
        for name in names:
            low = name.lower().replace('\\', '/')
            base = low.rsplit('/', 1)[-1]
            if base in wanted and low.endswith('/bin/' + base):
                wanted[base] = name
        if not wanted['ffmpeg.exe']:
            # Fallback: any ffmpeg.exe in the archive.
            for name in names:
                if name.lower().replace('\\', '/').endswith('ffmpeg.exe'):
                    wanted['ffmpeg.exe'] = name
                    break
        if not wanted['ffmpeg.exe']:
            raise RuntimeError('ffmpeg.exe not found inside downloaded archive')
        for exe_name, arc_name in wanted.items():
            if not arc_name:
                continue  # ffprobe is nice-to-have
            dest = os.path.join(target_bin_dir, exe_name)
            with z.open(arc_name) as src, open(dest, 'wb') as dst:
                shutil.copyfileobj(src, dst)
    return os.path.join(target_bin_dir, 'ffmpeg.exe')


def ensure_ffmpeg(progress_cb=None):
    """Make sure ffmpeg exists; download it on Windows when missing."""
    found = find_ffmpeg()
    if found['found']:
        _emit(progress_cb, 'ffmpeg', 'FFmpeg ready', percent=100,
              detail=found['exe'], status='done')
        return {'status': 'ok', 'path': found['exe'], 'downloaded': False}

    if os.name != 'nt':
        msg = 'FFmpeg not found — please install it via your package manager'
        print(f" [Startup] {msg}")
        _emit(progress_cb, 'ffmpeg', 'FFmpeg missing', percent=100,
              detail=msg, status='error')
        return {'status': 'error', 'error': msg, 'downloaded': False}

    target_dir = ffmpeg_install_dir()
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as e:
        msg = f'Cannot create {target_dir}: {e}'
        _emit(progress_cb, 'ffmpeg', 'FFmpeg missing', percent=100,
              detail=msg, status='error')
        return {'status': 'error', 'error': msg, 'downloaded': False}

    tmp_zip = None
    try:
        print(" [Startup] FFmpeg not found, downloading essentials build…")
        _emit(progress_cb, 'ffmpeg', 'Downloading FFmpeg…', percent=0,
              detail='Connecting…')
        fd, tmp_zip = tempfile.mkstemp(suffix='-ffmpeg.zip')
        os.close(fd)
        _download_with_progress(FFMPEG_ZIP_URL, tmp_zip, progress_cb)
        exe_path = _extract_ffmpeg_bins(tmp_zip, target_dir, progress_cb)

        # Sanity check: it should at least answer --version.
        try:
            subprocess.run([exe_path, '-version'], capture_output=True, timeout=15)
        except Exception as e:
            print(f" [Startup] ffmpeg sanity check warning: {e}")

        print(f" [Startup] FFmpeg installed to {exe_path}")
        _emit(progress_cb, 'ffmpeg', 'FFmpeg installed', percent=100,
              detail=exe_path, status='done')
        return {'status': 'ok', 'path': exe_path, 'downloaded': True}
    except Exception as e:
        msg = f'FFmpeg auto-download failed: {e}'
        print(f" [Startup] {msg}")
        _emit(progress_cb, 'ffmpeg', 'FFmpeg download failed', percent=100,
              detail=str(e)[:160], status='error')
        return {'status': 'error', 'error': str(e), 'downloaded': False}
    finally:
        if tmp_zip and os.path.exists(tmp_zip):
            try:
                os.remove(tmp_zip)
            except Exception:
                pass


# ==========================
# Library updates (yt-dlp et al.)
# ==========================
def _version_tuple(version):
    return tuple(int(p) for p in re.findall(r'\d+', str(version or '')))


def get_current_version(package):
    try:
        from importlib import metadata as _md
        return _md.version(package)
    except Exception:
        return None


def get_latest_version(package):
    try:
        req = urllib.request.Request(
            f"https://pypi.org/pypi/{package}/json",
            headers={'User-Agent': 'Hathor-MusicPlayer/1.0'})
        with urllib.request.urlopen(req, timeout=_PYPI_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data['info']['version']
    except Exception as e:
        print(f" [Startup] Could not check latest {package}: {e}")
        return None


def installed_youtube_libs():
    """Subset of LIB_CANDIDATES that are actually installed."""
    found = []
    for pkg in LIB_CANDIDATES:
        if get_current_version(pkg):
            if pkg not in found:
                found.append(pkg)
    if 'yt-dlp' not in found:
        # yt-dlp is required for downloads; track it even if missing so we
        # can report/attempt an install in dev mode.
        found.insert(0, 'yt-dlp')
    return found


def check_libraries_status():
    """Read-only version comparison, no installs. For settings/Retry UI."""
    results = []
    for pkg in installed_youtube_libs():
        current = get_current_version(pkg)
        latest = get_latest_version(pkg)
        if not current:
            results.append({'package': pkg, 'current': None, 'latest': latest,
                            'status': 'missing'})
        elif not latest:
            results.append({'package': pkg, 'current': current, 'latest': None,
                            'status': 'unknown'})
        elif _version_tuple(current) >= _version_tuple(latest):
            results.append({'package': pkg, 'current': current, 'latest': latest,
                            'status': 'up-to-date'})
        else:
            results.append({'package': pkg, 'current': current, 'latest': latest,
                            'status': 'outdated'})
    return results


def _pip_upgrade(package):
    subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '--upgrade', package],
        check=True, capture_output=True, timeout=180)


def ensure_libraries_updated(progress_cb=None):
    """Check PyPI and pip-upgrade outdated YouTube libs (dev mode only)."""
    packages = installed_youtube_libs()
    results = []
    total = max(1, len(packages))
    frozen = getattr(sys, 'frozen', False)

    for i, pkg in enumerate(packages):
        base_pct = (i / total) * 100.0
        _emit(progress_cb, 'libs', f'Checking {pkg}…', percent=round(base_pct, 1),
              detail='', status='working')
        current = get_current_version(pkg)
        latest = get_latest_version(pkg)

        if not current:
            if frozen:
                results.append({'package': pkg, 'current': None, 'latest': latest,
                                'status': 'missing-skipped-frozen'})
                _emit(progress_cb, 'libs', f'{pkg} missing', percent=100,
                      detail='Frozen build — reinstall the app to update',
                      status='skipped')
                continue
            try:
                _emit(progress_cb, 'libs', f'Installing {pkg}…',
                      percent=round(base_pct + 50 / total, 1), detail='')
                _pip_upgrade(pkg)
                current = get_current_version(pkg)
                results.append({'package': pkg, 'current': current, 'latest': latest,
                                'status': 'installed'})
                _emit(progress_cb, 'libs', f'{pkg} installed ({current})',
                      percent=round((i + 1) / total * 100, 1),
                      detail=str(current or ''), status='done')
            except Exception as e:
                results.append({'package': pkg, 'current': None, 'latest': latest,
                                'status': 'error', 'error': str(e)[:200]})
                _emit(progress_cb, 'libs', f'{pkg} install failed',
                      percent=round((i + 1) / total * 100, 1),
                      detail=str(e)[:120], status='error')
            continue

        if not latest:
            results.append({'package': pkg, 'current': current, 'latest': None,
                            'status': 'unknown'})
            _emit(progress_cb, 'libs', f'{pkg} {current} (offline?)',
                  percent=round((i + 1) / total * 100, 1),
                  detail='Could not reach PyPI', status='skipped')
            continue

        if _version_tuple(current) >= _version_tuple(latest):
            results.append({'package': pkg, 'current': current, 'latest': latest,
                            'status': 'up-to-date'})
            _emit(progress_cb, 'libs', f'{pkg} up to date ({current})',
                  percent=round((i + 1) / total * 100, 1),
                  detail=str(current), status='done')
            print(f" [Startup] {pkg} is up to date ({current}).")
            continue

        # Outdated.
        if frozen:
            results.append({'package': pkg, 'current': current, 'latest': latest,
                            'status': 'outdated-skipped-frozen'})
            _emit(progress_cb, 'libs', f'{pkg} update available ({latest})',
                  percent=round((i + 1) / total * 100, 1),
                  detail='Frozen build — update ships with next release',
                  status='skipped')
            print(f" [Startup] {pkg} {current} -> {latest} available "
                  f"(skipped: frozen build).")
            continue

        try:
            _emit(progress_cb, 'libs', f'Updating {pkg} {current} → {latest}…',
                  percent=round(base_pct + 30 / total, 1), detail='')
            print(f" [Startup] Updating {pkg} {current} -> {latest}…")
            _pip_upgrade(pkg)
            new_current = get_current_version(pkg) or latest
            results.append({'package': pkg, 'current': new_current, 'latest': latest,
                            'status': 'updated', 'previous': current})
            _emit(progress_cb, 'libs', f'{pkg} updated to {new_current}',
                  percent=round((i + 1) / total * 100, 1),
                  detail=str(new_current), status='done')
            print(f" [Startup] {pkg} updated to {new_current}.")
            # Make the fresh code live without a restart when possible.
            try:
                import importlib as _il
                if pkg == 'yt-dlp':
                    _mod = _il.import_module('yt_dlp')
                    _il.reload(_mod)
            except Exception as e:
                print(f" [Startup] Could not hot-reload {pkg}: {e}")
        except Exception as e:
            results.append({'package': pkg, 'current': current, 'latest': latest,
                            'status': 'error', 'error': str(e)[:200]})
            _emit(progress_cb, 'libs', f'{pkg} update failed',
                  percent=round((i + 1) / total * 100, 1),
                  detail=str(e)[:120], status='error')
            print(f" [Startup] {pkg} auto-update failed: {e}")

    return results


# ==========================
# Orchestrator
# ==========================
def run_startup_checks(progress_cb=None):
    """Run FFmpeg + library checks. Never raises; returns a summary dict."""
    ffmpeg_result = ensure_ffmpeg(progress_cb)
    libs_result = ensure_libraries_updated(progress_cb)

    all_ok = (
        ffmpeg_result.get('status') == 'ok'
        and all(r.get('status') in ('up-to-date', 'updated', 'installed')
                for r in libs_result)
    )
    summary = {'ffmpeg': ffmpeg_result, 'libraries': libs_result, 'all_ok': all_ok}
    _emit(progress_cb, 'done',
          'All set — everything is up to date' if all_ok else 'Startup check finished',
          percent=100,
          detail='' if all_ok else 'See details — the app is ready anyway',
          status='done' if all_ok else 'working')
    return summary
