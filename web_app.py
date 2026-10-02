"""Local browser interface for TubeTote. Run with python web_app.py."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from app import App, CUSTOM, DEFAULT_DIR, FFMPEG, PRESETS, format_kind, human_size
import yt_dlp


class Service:
    _download = App._download
    _progress_hook = App._progress_hook
    _postprocess_hook = App._postprocess_hook

    def __init__(self):
        self.events = queue.Queue()
        self.cancel_flag = threading.Event()
        self.lock = threading.RLock()
        self.worker = None
        self.state = dict(busy=False, status='Ready for your next download', progress=0,
                          logs=[], formats=[], fetched_url=None, video=None)

    def snapshot(self):
        with self.lock:
            while not self.events.empty():
                kind, payload = self.events.get_nowait()
                if kind == 'progress':
                    self.state.update(progress=payload[0], status=payload[1])
                elif kind == 'status':
                    self.state['status'] = payload
                elif kind == 'log':
                    self.state['logs'].append(payload)
                else:
                    self.state['busy'] = False
                    if kind == 'done':
                        self.state.update(progress=100, status='Download complete')
                        self.state['logs'].append(f'Saved to {payload}')
                    elif kind == 'cancelled':
                        self.state['status'] = 'Cancelled'
                        self.state['logs'].append('Download cancelled.')
                    elif kind == 'error':
                        self.state['status'] = 'Something went wrong'
                        self.state['logs'].append(f'ERROR: {payload}')
                self.state['logs'] = self.state['logs'][-200:]
            return dict(self.state)

    def start(self, data, fetch=False):
        with self.lock:
            self.snapshot()
            if self.worker and self.worker.is_alive():
                raise ValueError('An operation is already running.')
            url = str(data.get('url', '')).strip()
            if urlparse(url).scheme not in ('http', 'https') or not urlparse(url).netloc:
                raise ValueError('Paste a valid video URL starting with https://.')
            self.cancel_flag.clear()
            if fetch:
                target, args = self.fetch, (url,)
                self.state.update(formats=[], video=None, fetched_url=None)
            else:
                preset = data.get('preset', 'Best video + audio')
                if preset not in PRESETS:
                    raise ValueError('Choose a valid quality preset.')
                fmt, merge, codec = PRESETS[preset]
                if preset == CUSTOM:
                    if url != self.state['fetched_url']:
                        raise ValueError('Fetch formats for this URL first.')
                    selected = list(dict.fromkeys(data.get('selected', [])))
                    kinds = {f['id']: f['kind'] for f in self.state['formats']}
                    if not selected or any(fid not in kinds for fid in selected):
                        raise ValueError('Select one or more available formats.')
                    if len(selected) == 2 and sorted(kinds[f] for f in selected) == ['audio only', 'video only']:
                        fmt = '+'.join(sorted(selected, key=lambda f: kinds[f] == 'audio only'))
                    else:
                        fmt = ','.join(f'{f}+bestaudio' if kinds[f] == 'video only' and data.get('add_audio', True) else f for f in selected)
                    if data.get('convert_mp3'):
                        if any(kinds[f] != 'audio only' for f in selected):
                            raise ValueError('For MP3 conversion, select only audio-only rows, or choose Best audio → MP3.')
                        codec = 'mp3'
                    if data.get('playlist'):
                        fmt += '/bestaudio/best' if codec else '/bestvideo*+bestaudio/best'
                if codec and not FFMPEG:
                    raise ValueError('Audio conversion requires FFmpeg. Install imageio-ffmpeg and restart TubeTote.')
                directory = str(data.get('directory', '')).strip() or DEFAULT_DIR
                os.makedirs(directory, exist_ok=True)
                target = self._download
                args = (url, directory, fmt, merge, codec, bool(data.get('playlist')), ',' in fmt)
                self.state['logs'].append(f'Downloading: {url} [format: {fmt}]')
            self.state.update(busy=True, progress=0, status='Fetching formats…' if fetch else 'Starting download…')
            self.worker = threading.Thread(target=target, args=args, daemon=True)
            self.worker.start()

    def fetch(self, url):
        try:
            with yt_dlp.YoutubeDL(dict(quiet=True, no_warnings=True, noplaylist=True, skip_download=True)) as ydl:
                info = ydl.extract_info(url, download=False)
            if info.get('_type') == 'playlist':
                entries = [e for e in info.get('entries') or [] if e]
                if not entries:
                    raise ValueError('Playlist has no videos.')
                info = entries[0]
            formats = []
            duration = info.get('duration') or 0
            for f in reversed(info.get('formats') or []):
                kind = format_kind(f)
                if kind == 'other':
                    continue
                size = f.get('filesize') or f.get('filesize_approx') or (f.get('tbr', 0) or 0) * 125 * duration
                formats.append(dict(id=f['format_id'], ext=f.get('ext', ''), kind=kind,
                    resolution=(f"{f['abr']:.0f} kbps" if f.get('abr') else 'audio') if kind == 'audio only' else f.get('resolution', ''),
                    fps=f.get('fps') or '', vcodec=f.get('vcodec') or '', acodec=f.get('acodec') or '',
                    bitrate=f"{f['tbr']:.0f}k" if f.get('tbr') else '', size=human_size(size), note=f.get('format_note') or ''))
            with self.lock:
                if self.cancel_flag.is_set():
                    self.events.put(('cancelled', None))
                else:
                    self.state.update(formats=formats, fetched_url=url,
                        video=dict(title=info.get('title', ''), duration=duration, uploader=info.get('uploader', '')),
                        status='Formats loaded. Choose your quality.', busy=False)
        except Exception as exc:
            self.events.put(('error', str(exc)))


service = Service()
STATIC = Path(__file__).parent / 'static'


class Handler(BaseHTTPRequestHandler):
    def respond(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/state':
            self.respond(service.snapshot())
        elif path == '/api/config':
            self.respond(dict(presets=list(PRESETS), directory=DEFAULT_DIR, custom=CUSTOM))
        elif path in ('/', '/style.css', '/app.js'):
            file = STATIC / ('index.html' if path == '/' else path[1:])
            self.send_response(200)
            self.send_header('Content-Type', {'html':'text/html', 'css':'text/css', 'js':'text/javascript'}[file.suffix[1:]] + '; charset=utf-8')
            self.end_headers()
            self.wfile.write(file.read_bytes())
        else:
            self.respond({'error':'Not found'}, 404)

    def do_POST(self):
        # Only the local UI may trigger downloads or native folder actions.
        origin = self.headers.get('Origin')
        host = self.headers.get('Host', '')
        if host not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}') or (origin and origin != f'http://{host}'):
            self.respond({'error':'Local requests only'}, 403)
            return
        try:
            if 'application/json' not in self.headers.get('Content-Type', ''):
                raise ValueError('JSON request required.')
            length = int(self.headers.get('Content-Length', 0))
            if length > 65536:
                raise ValueError('Request too large.')
            data = json.loads(self.rfile.read(length) or b'{}')
            if self.path == '/api/fetch':
                service.start(data, fetch=True)
            elif self.path == '/api/download':
                service.start(data)
            elif self.path == '/api/cancel':
                service.cancel_flag.set()
                with service.lock:
                    service.state['status'] = 'Cancelling…'
            elif self.path == '/api/browse':
                if sys.platform != 'darwin':
                    raise ValueError('Enter a folder path manually on this platform.')
                result = subprocess.run(['osascript', '-e', 'POSIX path of (choose folder with prompt "Save TubeTote downloads to")'], capture_output=True, text=True)
                self.respond({'directory':result.stdout.strip() if result.returncode == 0 else ''})
                return
            elif self.path == '/api/open-folder':
                directory = str(data.get('directory', '')).strip() or DEFAULT_DIR
                os.makedirs(directory, exist_ok=True)
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', directory])
            else:
                self.respond({'error':'Not found'}, 404)
                return
            self.respond({'ok':True})
        except Exception as exc:
            self.respond({'error':str(exc)}, 400)

    def log_message(self, *_):
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    url = f'http://127.0.0.1:{args.port}'
    print(f'TubeTote is running at {url}', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        service.cancel_flag.set()
        server.server_close()
