# TubeTote

A local browser download studio powered by the existing yt-dlp downloader.

## Run

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh
```

Open http://127.0.0.1:8765. The launcher opens your default browser automatically. Keep the terminal running; press Ctrl+C to stop. Use `./run.sh --port 8766` to choose another port, or `--no-browser` to skip automatically opening the browser.

All original presets, playlist downloads, MP3/M4A conversion, multi-format downloads, stream merging, progress, logs, and cancellation are retained. Fetch formats before selecting custom streams. A video-only and audio-only pair merges; other multiple selections download separate files. The Save to field accepts a local folder path. Browse uses the native macOS folder picker; Open folder opens the destination on your computer. Cancellation is cooperative and may wait for extraction or processing to return.

Downloads go to the computer running Python. This server listens only on localhost and is intended for local use. The interface uses plain browser JavaScript, so no Node or frontend build step is needed. Fonts have a system fallback when offline.

The original desktop UI remains available with `.venv/bin/python app.py`.

To use audio in Premiere Pro, choose **Best audio → MP3**, or fetch formats, select audio-only rows, and enable **Convert selected audio formats to MP3 (192 kbps)**. Custom MP3 conversion rejects video rows to avoid unexpected audio-only output.
