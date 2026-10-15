#!/usr/bin/env python3
"""Storage dashboard: serves index.html, /api/storage and /api/breakdown (JSON)."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST, PORT = "0.0.0.0", 8085
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "breakdown-cache.json")
RESCAN_HOUR = 2  # rescan every drive once a day at 02:00 local time

CATEGORIES = {
    "Videos": "mkv mp4 avi mov wmv flv webm m4v mpg mpeg m2ts vob 3gp divx",
    "Music": "mp3 flac wav aac m4a ogg opus wma aiff ape mid",
    "Pictures": "jpg jpeg png gif bmp tif tiff webp heic raw cr2 nef arw dng svg psd xcf",
    "Text & documents": "txt md pdf doc docx odt rtf xls xlsx ods ppt pptx odp csv json xml html htm "
                        "log epub mobi tex yaml yml ini conf cfg srt sub",
    "Programs & code": "exe dll so a o bin msi deb rpm appimage jar class pyc py js sh c h cpp hpp "
                       "rs go java php rb pl ko lib",
    "Archives & disk images": "zip rar 7z tar gz tgz bz2 xz zst iso img qcow2 vmdk vdi bak",
}
EXT = {e: cat for cat, exts in CATEGORIES.items() for e in exts.split()}
ORDER = list(CATEGORIES) + ["Other", "Unreadable / system"]


def unescape(s):
    # /proc/mounts escapes spaces etc. as octal, e.g. \040
    return s.encode().decode("unicode_escape")


def label_for(mount, device):
    if mount == "/":
        return "System"
    if mount == "/boot/efi":
        return "EFI Boot"
    if device.startswith("/dev/md"):
        return "RAID (%s)" % os.path.basename(device)
    return os.path.basename(mount.rstrip("/"))


def storage():
    seen, out = set(), []
    with open("/proc/mounts") as f:
        for line in f:
            dev, mount, fstype = line.split()[:3]
            if not dev.startswith("/dev/") or dev.startswith("/dev/loop"):
                continue
            dev = os.path.realpath(dev)
            if dev in seen:
                continue
            mount = unescape(mount)
            try:
                st = os.statvfs(mount)
            except OSError:
                continue
            seen.add(dev)
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            used = total - st.f_bfree * st.f_frsize
            # match df: percent of (used + available)
            pct = round(100 * used / (used + free), 1) if used + free else 0
            out.append({
                "label": label_for(mount, dev), "mount": mount, "device": dev,
                "fstype": fstype, "total": total, "used": used, "free": free,
                "pct": pct,
            })
    out.sort(key=lambda d: -d["total"])
    return out


# ---- per-category breakdown (background scan, cached) ----------------------

state = {"results": {}, "scanning": None, "force": False}
wake = threading.Event()
lock = threading.Lock()


def scan(mount):
    """Sum disk usage by category for one filesystem, without crossing mounts."""
    sums = dict.fromkeys(ORDER, 0)
    root_dev = os.stat(mount).st_dev
    hardlinks = set()
    stack = [mount]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if st.st_dev != root_dev:
                    continue
                mode = st.st_mode & 0o170000
                if mode == 0o040000:  # directory
                    stack.append(e.path)
                elif mode == 0o100000:  # regular file
                    if st.st_nlink > 1:
                        key = (st.st_dev, st.st_ino)
                        if key in hardlinks:
                            continue
                        hardlinks.add(key)
                    name = e.name
                    ext = name.rsplit(".", 1)[-1].lower() if "." in name[1:] else ""
                    cat = EXT.get(ext)
                    if cat is None:
                        cat = "Programs & code" if not ext and st.st_mode & 0o111 else "Other"
                    sums[cat] += st.st_blocks * 512
    return sums


def save_cache():
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state["results"], f)
    os.replace(tmp, CACHE)


def last_scheduled():
    """Timestamp of the most recent 02:00 local time."""
    now = time.localtime()
    t = time.mktime(now[:3] + (RESCAN_HOUR, 0, 0, 0, 0, -1))
    return t if t <= time.time() else t - 86400


def scanner():
    try:
        with open(CACHE) as f:
            state["results"] = json.load(f)
    except (OSError, ValueError):
        pass
    while True:
        force, state["force"] = state["force"], False
        for d in storage():
            mount = d["mount"]
            prev = state["results"].get(mount)
            if prev and prev["updated"] >= last_scheduled() and not force:
                continue
            state["scanning"] = mount
            try:
                sums = scan(mount)
            except Exception:
                continue
            finally:
                state["scanning"] = None
            # whatever df counts as used but we could not see (unreadable dirs, metadata, journal)
            sums["Unreadable / system"] = max(0, d["used"] - sum(sums.values()))
            with lock:
                state["results"][mount] = {"updated": time.time(), "categories": sums}
                save_cache()
        wake.wait(600)
        wake.clear()


def breakdown():
    with lock:
        return {"scanning": state["scanning"], "pending": state["force"], "order": ORDER,
                "results": dict(state["results"])}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/storage":
            body, ctype = json.dumps(storage()).encode(), "application/json"
        elif path == "/api/breakdown":
            body, ctype = json.dumps(breakdown()).encode(), "application/json"
        elif path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                body, ctype = f.read(), "text/html; charset=utf-8"
        elif path in ("/favicon.svg", "/favicon.ico"):
            with open(os.path.join(HERE, "favicon.svg"), "rb") as f:
                body, ctype = f.read(), "image/svg+xml"
        elif path in ("/icon.svg", "/icon.png"):
            name = path[1:]
            with open(os.path.join(HERE, name), "rb") as f:
                body = f.read()
            ctype = "image/svg+xml" if name.endswith("svg") else "image/png"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split("?")[0] != "/api/rescan":
            self.send_error(404)
            return
        state["force"] = True
        wake.set()
        body = b'{"ok": true}'
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=scanner, daemon=True).start()
    print("Serving on http://%s:%d" % (HOST, PORT))
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
