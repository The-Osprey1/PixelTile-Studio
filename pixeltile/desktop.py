"""Native Windows desktop window (pywebview) wrapping the PixelTile Studio web UI."""
from __future__ import annotations

import base64
import os
import threading
from http.server import ThreadingHTTPServer

from .webapp import Handler


def start_server() -> tuple[ThreadingHTTPServer, int]:
    """Start the local HTTP server on an OS-assigned free port on 127.0.0.1."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, port


class DesktopApi:
    """JS-to-Python bridge exposed to pywebview for native Save As dialogs."""

    def __init__(self) -> None:
        self._window = None

    def attach(self, window) -> None:
        self._window = window

    def save_bytes(self, filename: str, b64_data: str) -> dict[str, object]:
        import webview

        if self._window is None:
            return {"saved": False}
        ext = os.path.splitext(filename)[1].lower()
        file_types = (
            ("ZIP Archive (*.zip)",) if ext == ".zip"
            else ("3MF Model (*.3mf)",) if ext == ".3mf"
            else ("All files (*.*)",)
        )
        dialog_type = getattr(webview, "FileDialog", None)
        mode = dialog_type.SAVE if dialog_type is not None else webview.SAVE_DIALOG
        result = self._window.create_file_dialog(mode, save_filename=filename, file_types=file_types)
        if not result:
            return {"saved": False}
        path = result[0] if isinstance(result, (list, tuple)) else str(result)
        raw = base64.b64decode(b64_data)
        with open(path, "wb") as f:
            f.write(raw)
        return {"saved": True, "path": path}


def launch() -> None:
    import webview

    srv, port = start_server()
    api = DesktopApi()
    try:
        win = webview.create_window(
            "PixelTile Studio",
            f"http://127.0.0.1:{port}/",
            js_api=api,
            width=1360,
            height=860,
            min_size=(1024, 680),
        )
        api.attach(win)
        webview.start()
    finally:
        srv.shutdown()
        srv.server_close()
