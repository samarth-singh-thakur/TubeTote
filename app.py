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

QUALITY_FORMATS = {
    "Best (video + audio)": "bestvideo*+bestaudio/best",
    "1080p": "bestvideo*[height<=1080]+bestaudio/best[height<=1080]",
    "720p": "bestvideo*[height<=720]+bestaudio/best[height<=720]",
    "480p": "bestvideo*[height<=480]+bestaudio/best[height<=480]",
    "Audio only (MP3)": "bestaudio/best",
}


class Cancelled(Exception):
    pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TubeTote")
        self.geometry("640x420")
        self.minsize(520, 380)

        self.events = queue.Queue()
        self.worker = None
        self.cancel_flag = threading.Event()

        self.url_var = tk.StringVar()
        self.dir_var = tk.StringVar(value=DEFAULT_DIR)
        self.quality_var = tk.StringVar(value="Best (video + audio)")
        self.playlist_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()
        self.after(100, self._poll_events)

    # ---------- UI ----------

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Video URL:").grid(row=0, column=0, sticky="w", **pad)
        url_entry = ttk.Entry(frm, textvariable=self.url_var)
        url_entry.grid(row=0, column=1, columnspan=2, sticky="ew", **pad)
        url_entry.focus()
        url_entry.bind("<Return>", lambda e: self.start_download())

        ttk.Label(frm, text="Save to:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.dir_var).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Browse…", command=self.choose_dir).grid(row=1, column=2, **pad)

        ttk.Label(frm, text="Quality:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Combobox(
            frm,
            textvariable=self.quality_var,
            values=list(QUALITY_FORMATS),
            state="readonly",
        ).grid(row=2, column=1, sticky="w", **pad)
        ttk.Checkbutton(frm, text="Download whole playlist", variable=self.playlist_var).grid(
            row=2, column=2, sticky="w", **pad
        )

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=3, sticky="ew", **pad)
        self.download_btn = ttk.Button(btns, text="Download", command=self.start_download)
        self.download_btn.pack(side="left")
        self.cancel_btn = ttk.Button(btns, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=8)
        ttk.Button(btns, text="Open folder", command=self.open_folder).pack(side="right")

        self.progress = ttk.Progressbar(frm, maximum=100)
        self.progress.grid(row=4, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(frm, textvariable=self.status_var).grid(row=5, column=0, columnspan=3, sticky="w", **pad)

        self.log = tk.Text(frm, height=8, state="disabled", wrap="word")
        self.log.grid(row=6, column=0, columnspan=3, sticky="nsew", **pad)
        frm.rowconfigure(6, weight=1)

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

    # ---------- download ----------

    def start_download(self):
        if self.worker and self.worker.is_alive():
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Please paste a YouTube URL.")
            return
        out_dir = self.dir_var.get().strip() or DEFAULT_DIR
        os.makedirs(out_dir, exist_ok=True)

        self.cancel_flag.clear()
        self.progress["value"] = 0
        self.download_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.status_var.set("Starting…")
        self.write_log(f"Downloading: {url}")

        self.worker = threading.Thread(
            target=self._download,
            args=(url, out_dir, self.quality_var.get(), self.playlist_var.get()),
            daemon=True,
        )
        self.worker.start()

    def cancel(self):
        self.cancel_flag.set()
        self.status_var.set("Cancelling…")

    def _download(self, url, out_dir, quality, playlist):
        audio_only = quality.startswith("Audio")
        opts = {
            "format": QUALITY_FORMATS[quality],
            "outtmpl": os.path.join(out_dir, "%(title)s [%(id)s].%(ext)s"),
            "noplaylist": not playlist,
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocess_hook],
            "quiet": True,
            "no_warnings": True,
        }
        if FFMPEG:
            opts["ffmpeg_location"] = FFMPEG
            if audio_only:
                opts["postprocessors"] = [
                    {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
                ]
            else:
                opts["merge_output_format"] = "mp4"
        else:
            # Without ffmpeg we cannot merge separate streams; use a single-file format.
            opts["format"] = "bestaudio/best" if audio_only else "best"

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
                elif kind == "done":
                    self.progress["value"] = 100
                    self.status_var.set("Done!")
                    self.write_log(f"Saved to {payload}")
                    self._reset_buttons()
                elif kind == "cancelled":
                    self.status_var.set("Cancelled")
                    self.write_log("Download cancelled.")
                    self._reset_buttons()
                elif kind == "error":
                    self.status_var.set("Error")
                    self.write_log(f"ERROR: {payload}")
                    self._reset_buttons()
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _reset_buttons(self):
        self.download_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")


if __name__ == "__main__":
    App().mainloop()
