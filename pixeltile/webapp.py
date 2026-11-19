"""Local studio web server (stdlib only): serves the UI in pixeltile/static and the conversion API."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import urllib.parse
from collections import OrderedDict
from dataclasses import astuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .palette import BAMBU_PLA
from .pipeline import Result, Settings, run, sample_artwork_png

STATIC_DIR = Path(__file__).with_name("static")
_STATIC_TYPES = {"app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8"}
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_PREFS_BYTES = 256 * 1024
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

_FIELDS = {
    "width_mm": float,
    "pixel_mm": float,
    "max_tile_colors": int,
    "max_tile_size_mm": float,
    "auto_colors": int,
    "contrast": float,
    "saturation": float,
    "dither": lambda v: v == "1",
    "connector_keys": lambda v: v == "1",
    "wall_mount": lambda v: v == "1",
    "palette": str,
    "backing_color": str,
    "backing_mm": float,
    "color_depth_mm": float,
}


def _settings(query: str) -> Settings:
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    kw = {}
    for k, conv in _FIELDS.items():
        if k in q and q[k] != "":
            try:
                kw[k] = conv(q[k])
            except ValueError:
                raise ValueError(f"{k}: {q[k]!r} is not a valid number") from None
    if "palette" in kw and not kw["palette"].strip():
        del kw["palette"]
    return Settings(**kw)


def _b64_png(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


class _ResultCache:
    """The last few full results, so repeated downloads don't rerun the whole pipeline."""

    def __init__(self, size: int = 2) -> None:
        self._items: OrderedDict[str, Result] = OrderedDict()
        self._lock = threading.Lock()
        self._size = size

    def get(self, key: str) -> Result | None:
        with self._lock:
            r = self._items.get(key)
            if r is not None:
                self._items.move_to_end(key)
            return r

    def put(self, key: str, r: Result) -> None:
        with self._lock:
            self._items[key] = r
            self._items.move_to_end(key)
            while len(self._items) > self._size:
                self._items.popitem(last=False)


_cache = _ResultCache()


def _cache_key(body: bytes, s: Settings) -> str:
    return hashlib.sha256(body).hexdigest() + repr(astuple(s))


def _full_result(body: bytes, s: Settings) -> Result:
    key = _cache_key(body, s)
    r = _cache.get(key)
    if r is None:
        r = run(body, s, allow_palette_file=False)
        _cache.put(key, r)
    return r


# --- Saved preferences (owned spools, color corrections, last settings) ---------------------
# Stored in a file rather than browser storage: the desktop app uses a new port on every launch,
# which would give the page a fresh, empty browser storage each time.

_prefs_lock = threading.Lock()


def prefs_path() -> Path:
    override = os.environ.get("PIXELTILE_CONFIG_DIR")
    if override:
        return Path(override) / "prefs.json"
    if os.name == "nt":
        root = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(root) / "PixelTile Studio" / "prefs.json"
    root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(root) / "pixeltile" / "prefs.json"


def load_prefs() -> dict:
    try:
        data = json.loads(prefs_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_prefs(data: dict) -> None:
    path = prefs_path()
    with _prefs_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def _index_html() -> bytes:
    config = {"fields": list(_FIELDS), "spool_count": len(BAMBU_PLA)}
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    page = page.replace("__CONFIG__", json.dumps(config)).replace("__N__", str(len(BAMBU_PLA)))
    return page.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code: int, msg: str) -> None:
        self._send(code, msg.encode("utf-8"), "text/plain; charset=utf-8")

    def _json(self, obj) -> None:
        self._send(200, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        """Only answer requests addressed to this machine (blocks DNS-rebinding pages)."""
        host = self.headers.get("Host")
        if host is None:
            return True
        name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        return name.strip("[]").lower() in _LOCAL_HOSTS

    def _origin_ok(self) -> bool:
        """Browsers send Origin on POST; refuse posts coming from other web sites."""
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # not a browser (scripts, tests)
        u = urllib.parse.urlparse(origin)
        return u.scheme == "http" and u.netloc == self.headers.get("Host")

    def do_GET(self):
        if not self._host_ok():
            self._text(403, "forbidden")
            return
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            self._send(200, _index_html(), "text/html; charset=utf-8")
        elif u.path.startswith("/static/") and u.path[len("/static/"):] in _STATIC_TYPES:
            name = u.path[len("/static/"):]
            self._send(200, (STATIC_DIR / name).read_bytes(), _STATIC_TYPES[name])
        elif u.path == "/api/palette":
            self._json([{"name": n, "hex": h} for n, h in BAMBU_PLA])
        elif u.path == "/api/prefs":
            self._json(load_prefs())
        elif u.path == "/api/sample":
            q = urllib.parse.parse_qs(u.query)
            name = q.get("name", ["wave"])[0]
            try:
                self._send(200, sample_artwork_png(name), "image/png")
            except ValueError as e:
                self._text(400, str(e))
        else:
            self._text(404, "not found")

    def do_POST(self):
        if not (self._host_ok() and self._origin_ok()):
            self._text(403, "forbidden")
            return
        u = urllib.parse.urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._text(400, "bad Content-Length")
            return
        limit = MAX_PREFS_BYTES if u.path == "/api/prefs" else MAX_UPLOAD_BYTES
        if length < 0 or length > limit:
            self.close_connection = True
            self._text(413, f"request too large (limit {limit // (1024 * 1024) or 1} MB)")
            return
        body = self.rfile.read(length)

        if u.path == "/api/prefs":
            try:
                data = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self._text(400, "preferences must be JSON")
                return
            if not isinstance(data, dict):
                self._text(400, "preferences must be a JSON object")
                return
            save_prefs(data)
            self._json({"saved": True})
            return
        if u.path not in ("/api/inspect", "/api/preview", "/api/export", "/api/file"):
            self._text(404, "not found")
            return
        if not body:
            self._text(400, "no image uploaded")
            return

        try:
            s = _settings(u.query)
            if u.path == "/api/inspect":
                self._inspect(body, s)
            elif u.path == "/api/preview":
                r = _cache.get(_cache_key(body, s)) or run(body, s, allow_palette_file=False, build_files=False)
                self._send(200, r.tile_map_png, "image/png")
            elif u.path == "/api/export":
                self._send(200, _full_result(body, s).zip_bytes(), "application/zip")
            else:
                self._file(body, s, urllib.parse.parse_qs(u.query))
        except (ValueError, UnidentifiedImageError, Image.DecompressionBombError) as e:
            self._text(400, str(e) or e.__class__.__name__)
        except Exception as e:  # noqa: BLE001 - report anything else instead of dropping the connection
            self._text(500, f"internal error: {e}")

    def _inspect(self, body: bytes, s: Settings) -> None:
        # Previews skip writing .3mf files; reuse a full result if a download already made one.
        r = _cache.get(_cache_key(body, s)) or run(body, s, allow_palette_file=False, build_files=False)
        self._json({
            "grid": list(r.grid),
            "tile_size_mm": list(r.tile_size_mm),
            "schedule": r.schedule,
            "readme": r.readme,
            "warnings": r.warnings,
            "filament": r.filament,
            "swatches": r.swatch_names,
            "has_connectors": r.has_connectors,
            "preview_b64": _b64_png(r.preview_png),
            "tile_map_b64": _b64_png(r.tile_map_png),
        })

    def _file(self, body: bytes, s: Settings, q: dict) -> None:
        target = os.path.basename(q.get("file", [""])[0])
        r = _full_result(body, s)
        if target in r.swatches:
            data = r.swatches[target]
        elif target == "connectors.3mf":
            data = r.connectors_3mf
        else:
            data = r.tiles.get(target, b"")
        if not data:
            self._text(404, "file not found")
            return
        self._send(200, data, "model/3mf", {"Content-Disposition": f'attachment; filename="{target}"'})

    def log_message(self, *a):
        pass


def serve(port: int = 8765) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"PixelTile UI: http://127.0.0.1:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
