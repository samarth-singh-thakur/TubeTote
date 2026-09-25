"""TubeTote — a simple Tkinter GUI for downloading videos with yt-dlp."""

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import yt_dlp

try:
    import imageio_ffmpeg

    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG = None

DEFAULT_DIR = "/Users/samarthsinghthakur/Downloads/Assets"

CUSTOM = "Custom (pick from format list)"

# name -> (format selector, merge into mp4?, audio codec to convert to or None)
PRESETS = {
    "Best video + audio": ("bestvideo*+bestaudio/best", True, None),
    "Best video only (no audio)": ("bestvideo/best", False, None),
    "Best audio only (original)": ("bestaudio/best", False, None),
    "Best audio → MP3": ("bestaudio/best", False, "mp3"),
    "Best audio → M4A": ("bestaudio/best", False, "m4a"),
    "2160p (4K)": ("bestvideo*[height<=2160]+bestaudio/best[height<=2160]", True, None),
    "1440p": ("bestvideo*[height<=1440]+bestaudio/best[height<=1440]", True, None),
    "1080p": ("bestvideo*[height<=1080]+bestaudio/best[height<=1080]", True, None),
    "720p": ("bestvideo*[height<=720]+bestaudio/best[height<=720]", True, None),
    "480p": ("bestvideo*[height<=480]+bestaudio/best[height<=480]", True, None),
    "360p": ("bestvideo*[height<=360]+bestaudio/best[height<=360]", True, None),
    "Smallest file": ("worstvideo*+worstaudio/worst", True, None),
    CUSTOM: (None, False, None),
}

COLUMNS = (
    ("id", "ID", 70),
    ("ext", "Ext", 50),
    ("kind", "Type", 95),
    ("res", "Resolution", 90),
    ("fps", "FPS", 45),
    ("vcodec", "Video codec", 110),
    ("acodec", "Audio codec", 90),
    ("tbr", "Bitrate", 75),
    ("size", "Size", 80),
    ("note", "Note", 120),
)


class Cancelled(Exception):
    pass


def human_size(n):
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def format_kind(f):
    has_v = f.get("vcodec") not in (None, "none")
    has_a = f.get("acodec") not in (None, "none")
    if has_v and has_a:
        return "video + audio"
    if has_v:
        return "video only"
    if has_a:
        return "audio only"
    return "other"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TubeTote")
        self.geometry("900x640")
        self.minsize(720, 520)

        self.events = queue.Queue()
        self.worker = None
        self.cancel_flag = threading.Event()
        self.formats = {}  # format_id -> kind, for the currently fetched URL
        self.fetched_url = None

        self.url_var = tk.StringVar()
        self.dir_var = tk.StringVar(value=DEFAULT_DIR)
        self.quality_var = tk.StringVar(value="Best video + audio")
        self.playlist_var = tk.BooleanVar(value=False)
        self.add_audio_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready")
        self.title_var = tk.StringVar(value="Paste a URL and click “Fetch formats” to see every available format.")

        self._build_ui()
        self.url_var.trace_add("write", lambda *_: self._clear_formats())
        self.after(100, self._poll_events)

    # ---------- UI ----------

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Video URL:").grid(row=0, column=0, sticky="w", **pad)
        url_entry = ttk.Entry(frm, textvariable=self.url_var)
        url_entry.grid(row=0, column=1, sticky="ew", **pad)
        url_entry.focus()
        url_entry.bind("<Return>", lambda e: self.start_download())
        self.fetch_btn = ttk.Button(frm, text="Fetch formats", command=self.fetch_formats)
        self.fetch_btn.grid(row=0, column=2, sticky="ew", **pad)

        ttk.Label(frm, text="Save to:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.dir_var).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Browse…", command=self.choose_dir).grid(row=1, column=2, sticky="ew", **pad)

        ttk.Label(frm, text="Quality:").grid(row=2, column=0, sticky="w", **pad)
        opts = ttk.Frame(frm)
        opts.grid(row=2, column=1, columnspan=2, sticky="ew", **pad)
        self.quality_box = ttk.Combobox(
            opts, textvariable=self.quality_var, values=list(PRESETS), state="readonly", width=30
        )
        self.quality_box.pack(side="left")
        self.quality_box.bind("<<ComboboxSelected>>", self._on_preset_change)
        ttk.Checkbutton(opts, text="Whole playlist", variable=self.playlist_var).pack(side="left", padx=12)
        ttk.Checkbutton(
            opts, text="Add best audio to video-only picks", variable=self.add_audio_var
        ).pack(side="left")

        # Format list
        ttk.Label(frm, textvariable=self.title_var, wraplength=820).grid(
            row=3, column=0, columnspan=3, sticky="w", **pad
        )
        table = ttk.Frame(frm)
        table.grid(row=4, column=0, columnspan=3, sticky="nsew", **pad)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table, columns=[c[0] for c in COLUMNS], show="headings", selectmode="extended", height=10
        )
        for key, label, width in COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w", stretch=key == "note")
        vsb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        ttk.Label(
            frm,
            text="Tip: select one row, or ⌘-click a video-only row plus an audio-only row to merge them. "
            "Other multi-selections download each format as a separate file.",
            foreground="gray",
            wraplength=820,
        ).grid(row=5, column=0, columnspan=3, sticky="w", padx=8)
        frm.rowconfigure(4, weight=3)

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=3, sticky="ew", **pad)
        self.download_btn = ttk.Button(btns, text="Download", command=self.start_download)
        self.download_btn.pack(side="left")
        self.cancel_btn = ttk.Button(btns, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=8)
        ttk.Button(btns, text="Open folder", command=self.open_folder).pack(side="right")

        self.progress = ttk.Progressbar(frm, maximum=100)
        self.progress.grid(row=7, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(frm, textvariable=self.status_var).grid(row=8, column=0, columnspan=3, sticky="w", **pad)

        self.log = tk.Text(frm, height=6, state="disabled", wrap="word")
        self.log.grid(row=9, column=0, columnspan=3, sticky="nsew", **pad)
        frm.rowconfigure(9, weight=1)

    def choose_dir(self):
        path = filedialog.askdirectory(initialdir=self.dir_var.get() or DEFAULT_DIR)
        if path:
            self.dir_var.set(path)

    def open_folder(self):
        path = self.dir_var.get()
        os.makedirs(path, exist_ok=True)
        os.system(f'open "{path}"')

    def write_log(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.download_btn.configure(state=state)
        self.fetch_btn.configure(state=state)
        self.cancel_btn.configure(state="normal" if busy else "disabled")

    # ---------- format list ----------

    def _clear_formats(self):
        if self.fetched_url is None:
            return
        self.fetched_url = None
        self.formats = {}
        self.tree.delete(*self.tree.get_children())
        self.title_var.set("URL changed — click “Fetch formats” to list its formats.")
        if self.quality_var.get() == CUSTOM:
            self.quality_var.set("Best video + audio")

    def _on_preset_change(self, _event=None):
        if self.quality_var.get() != CUSTOM and self.tree.selection():
            self.tree.selection_remove(*self.tree.selection())

    def _on_tree_select(self, _event=None):
        if self.tree.selection():
            self.quality_var.set(CUSTOM)

    def fetch_formats(self):
        if self.worker and self.worker.is_alive():
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Please paste a YouTube URL.")
            return
        self.cancel_flag.clear()
        self._set_busy(True)
        self.status_var.set("Fetching formats…")
        self.worker = threading.Thread(target=self._fetch, args=(url,), daemon=True)
        self.worker.start()

    def _fetch(self, url):
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info.get("_type") == "playlist":
                entries = [e for e in info.get("entries") or [] if e]
                if not entries:
                    raise ValueError("Playlist has no videos.")
                info = entries[0]
            self.events.put(("formats", (url, info)))
        except Exception as e:
            self.events.put(("error", str(e)))

    def _show_formats(self, url, info):
        self.tree.delete(*self.tree.get_children())
        self.formats = {}
        duration = info.get("duration") or 0
        for f in reversed(info.get("formats") or []):  # yt-dlp lists worst → best
            kind = format_kind(f)
            if kind == "other":  # storyboards / thumbnails
                continue
            fid = f["format_id"]
            self.formats[fid] = kind
            size = f.get("filesize") or f.get("filesize_approx")
            if not size and f.get("tbr") and duration:
                size = f["tbr"] * 1000 / 8 * duration
            res = f.get("resolution") or ""
            if kind == "audio only":
                res = f"{f['abr']:.0f} kbps" if f.get("abr") else "audio"
            self.tree.insert(
                "",
                "end",
                iid=fid,
                values=(
                    fid,
                    f.get("ext", ""),
                    kind,
                    res,
                    f"{f['fps']:g}" if f.get("fps") else "",
                    "" if kind == "audio only" else (f.get("vcodec") or ""),
                    "" if kind == "video only" else (f.get("acodec") or ""),
                    f"{f['tbr']:.0f}k" if f.get("tbr") else "",
                    human_size(size),
                    f.get("format_note") or "",
                ),
            )
        self.fetched_url = url
        mins, secs = divmod(int(duration), 60)
        self.title_var.set(f"{info.get('title', '')}  ({mins}:{secs:02d}) — {len(self.formats)} formats")

    def _custom_format(self):
        """Build a yt-dlp format string from the rows selected in the table."""
        sel = list(self.tree.selection())
        if not sel:
            return None
        kinds = [self.formats.get(fid) for fid in sel]
        if len(sel) == 2 and sorted(kinds) == ["audio only", "video only"]:
            video = sel[kinds.index("video only")]
            audio = sel[kinds.index("audio only")]
            return f"{video}+{audio}"
        parts = []
        for fid, kind in zip(sel, kinds):
            if kind == "video only" and self.add_audio_var.get():
                parts.append(f"{fid}+bestaudio")
            else:
                parts.append(fid)
        return ",".join(parts)

    # ---------- download ----------

    def start_download(self):
        if self.worker and self.worker.is_alive():
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Please paste a YouTube URL.")
            return
        preset = self.quality_var.get()
        fmt, merge, audio_codec = PRESETS[preset]
        if preset == CUSTOM:
            fmt = self._custom_format()
            if not fmt:
                messagebox.showwarning(
                    "No format selected", "Click “Fetch formats”, then select one or more rows in the list."
                )
                return
            if self.playlist_var.get():
                # Format IDs can differ between videos, so fall back to best for the others.
                fmt += "/bestvideo*+bestaudio/best"
        out_dir = self.dir_var.get().strip() or DEFAULT_DIR
        os.makedirs(out_dir, exist_ok=True)

        self.cancel_flag.clear()
        self.progress["value"] = 0
        self._set_busy(True)
        self.status_var.set("Starting…")
        self.write_log(f"Downloading: {url}  [format: {fmt}]")

        self.worker = threading.Thread(
            target=self._download,
            args=(url, out_dir, fmt, merge, audio_codec, self.playlist_var.get(), "," in fmt),
            daemon=True,
        )
        self.worker.start()

    def cancel(self):
        self.cancel_flag.set()
        self.status_var.set("Cancelling…")

    def _download(self, url, out_dir, fmt, merge, audio_codec, playlist, multiple):
        # Several formats of one video would share a filename, so tag each with its format ID.
        name = "%(title)s [%(id)s].f%(format_id)s.%(ext)s" if multiple else "%(title)s [%(id)s].%(ext)s"
        opts = {
            "format": fmt,
            "outtmpl": os.path.join(out_dir, name),
            "noplaylist": not playlist,
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocess_hook],
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
        }
        if FFMPEG:
            opts["ffmpeg_location"] = FFMPEG
            if audio_codec:
                opts["postprocessors"] = [
                    {"key": "FFmpegExtractAudio", "preferredcodec": audio_codec, "preferredquality": "192"}
                ]
            elif merge:
                opts["merge_output_format"] = "mp4"
        elif "+" in fmt:
            # Without ffmpeg we cannot merge separate streams; use a single-file format.
            opts["format"] = "best"

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            self.events.put(("done", out_dir))
        except Cancelled:
            self.events.put(("cancelled", None))
        except Exception as e:
            if self.cancel_flag.is_set():
                self.events.put(("cancelled", None))
            else:
                self.events.put(("error", str(e)))

    def _progress_hook(self, d):
        if self.cancel_flag.is_set():
            raise Cancelled()
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes", 0)
            pct = done / total * 100 if total else 0
            speed = d.get("_speed_str", "").strip()
            eta = d.get("_eta_str", "").strip()
            name = os.path.basename(d.get("filename", ""))
            self.events.put(("progress", (pct, f"{pct:5.1f}%  {speed}  ETA {eta}  —  {name}")))
        elif status == "finished":
            self.events.put(("log", f"Downloaded: {os.path.basename(d.get('filename', ''))}"))

    def _postprocess_hook(self, d):
        if d.get("status") == "started":
            self.events.put(("status", f"Processing ({d.get('postprocessor')})…"))

    # ---------- event loop bridge ----------

    def _poll_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    pct, text = payload
                    self.progress["value"] = pct
                    self.status_var.set(text)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "log":
                    self.write_log(payload)
                elif kind == "formats":
                    if self.cancel_flag.is_set():
                        self.status_var.set("Cancelled")
                    else:
                        url, info = payload
                        self._show_formats(url, info)
                        self.status_var.set("Formats loaded — pick a preset or select rows in the list.")
                    self._set_busy(False)
                elif kind == "done":
                    self.progress["value"] = 100
                    self.status_var.set("Done!")
                    self.write_log(f"Saved to {payload}")
                    self._set_busy(False)
                elif kind == "cancelled":
                    self.status_var.set("Cancelled")
                    self.write_log("Download cancelled.")
                    self._set_busy(False)
                elif kind == "error":
                    self.status_var.set("Error")
                    self.write_log(f"ERROR: {payload}")
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)


if __name__ == "__main__":
    App().mainloop()
