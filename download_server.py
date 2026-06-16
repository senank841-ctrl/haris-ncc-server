import json
import subprocess
import sys
import os
import tempfile
import shutil
import shlex
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, quote

PORT = int(os.environ.get('PORT', 3001))

def has_ffmpeg():
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
        return True
    except Exception:
        return False

FFMPEG_AVAILABLE = has_ffmpeg()

class DownloadHandler(BaseHTTPRequestHandler):

    def send_error(self, code, message=None):
        # Always include CORS headers so the browser can read cross-origin errors
        self.send_response(code)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.end_headers()
        if message:
            self.wfile.write(message.encode('utf-8'))
        print(f'[DownloadServer] ERROR {code}: {message}')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        self._handle_download()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != '/download':
            self.send_error(404, 'Not found')
            return

        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            self.send_error(400, 'Empty request body')
            return

        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, 'Invalid JSON')
            return

        video_id = data.get('videoId', '')
        quality = str(data.get('quality', '720'))
        format_type = str(data.get('formatType', 'mp4'))

        if not video_id:
            self.send_error(400, 'Missing videoId')
            return

        youtube_url = f'https://www.youtube.com/watch?v={video_id}'
        self._do_download(youtube_url, quality, format_type)

    def _handle_download(self):
        parsed = urlparse(self.path)
        params = {}
        if parsed.query:
            for pair in parsed.query.split('&'):
                if '=' in pair:
                    k, v = pair.split('=', 1)
                    params[k] = v
        video_id = params.get('videoId', '')
        quality = str(params.get('quality', '720'))
        format_type = str(params.get('formatType', 'mp4'))
        if not video_id:
            self.send_error(400, 'Missing videoId parameter')
            return
        youtube_url = f'https://www.youtube.com/watch?v={video_id}'
        self._do_download(youtube_url, quality, format_type)

    def _do_download(self, youtube_url, quality, format_type='mp4'):
        vid = youtube_url.split('v=')[-1].split('&')[0]
        tmp_dir = tempfile.mkdtemp()
        output_template = os.path.join(tmp_dir, '%(title)s.%(ext)s')

        try:
            cmd = ['yt-dlp', '--concurrent-fragments', '5', '--extractor-retries', '10', '--sleep-requests', '1', '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36']

            # Use cookies file if present alongside the script
            cookies_path = os.path.join(os.path.dirname(__file__), 'cookies.txt')
            if os.path.isfile(cookies_path):
                cmd += ['--cookies', cookies_path]

            if format_type == 'mp3':
                print(f'[DownloadServer] Format: mp3 (audio only)')
                cmd += ['-x', '--audio-format', 'mp3', '--audio-quality', '0']
                cmd += ['-o', output_template, youtube_url]
            else:
                # mp4 — force compatible formats for reliable merging
                if quality == 'best':
                    format_str = 'bestvideo+bestaudio/best'
                elif FFMPEG_AVAILABLE:
                    format_str = f'bestvideo[ext=mp4][height<={quality}]+bestaudio[ext=m4a]/best[height<={quality}]'
                else:
                    format_str = f'best[height<={quality}]'
                print(f'[DownloadServer] Format: {format_str} (ffmpeg: {FFMPEG_AVAILABLE})')
                cmd += ['-f', format_str, '--merge-output-format', 'mp4']
                cmd += ['-o', output_template, '--no-simulate', youtube_url]

            print(f'[DownloadServer] Starting yt-dlp for video {vid}...')

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )

            print(f'[DownloadServer] yt-dlp exit code: {result.returncode}')

            if result.returncode != 0:
                stderr = result.stderr.strip()[:2000] or 'unknown error'
                print(f'[DownloadServer] yt-dlp error: {stderr}')
                self.send_error(500, stderr)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return

            files = os.listdir(tmp_dir)
            print(f'[DownloadServer] Files in tmp: {files}')

            ext_filter = ('.mp3',) if format_type == 'mp3' else ('.mp4', '.webm', '.mkv')
            video_files = [f for f in files if f.endswith(ext_filter)]
            if not video_files:
                self.send_error(500, 'No output file found. Files: ' + str(files))
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return

            filepath = os.path.join(tmp_dir, video_files[0])
            filesize = os.path.getsize(filepath)
            print(f'[DownloadServer] Sending file: {os.path.basename(filepath)} ({filesize} bytes)')

            content_type = 'audio/mpeg' if format_type == 'mp3' else 'video/mp4'
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Type', content_type)
            safe_filename = quote(os.path.basename(filepath))
            self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{safe_filename}")
            self.send_header('Content-Length', str(filesize))
            self.end_headers()

            with open(filepath, 'rb') as f:
                shutil.copyfileobj(f, self.wfile)

            print(f'[DownloadServer] File sent successfully')
            shutil.rmtree(tmp_dir, ignore_errors=True)

        except subprocess.TimeoutExpired:
            print('[DownloadServer] yt-dlp timed out')
            self.send_error(500, 'Download timed out')
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except ConnectionAbortedError:
            print('[DownloadServer] Client disconnected')
            pass
        except Exception as e:
            print(f'[DownloadServer] Unexpected error: {e}')
            try:
                self.send_error(500, str(e))
            except Exception:
                pass
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def log_message(self, format, *args):
        try:
            msg = format % args
        except Exception:
            msg = f'{format} {args}'
        sys.stderr.write(f'[DownloadServer] {msg}\n')


if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', PORT), DownloadHandler)
    print(f'Haris NCC Download Server running on http://0.0.0.0:{PORT}')
    print('Press Ctrl+C to stop')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nServer stopped.')
        server.server_close()
