"""Dev server: renders every page from its cached payload on each request, so template edits show on reload."""

from __future__ import annotations

import json
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from radar import build


def serve(out: Path, port: int) -> None:
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(out), **kw)

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            name = self.path.split("?")[0].split("#")[0].strip("/") or "index.html"
            if name == "index.html":
                html = build.index_page(out)
            elif name.endswith(".html") and (out / name.replace(".html", ".json")).exists():
                html = build.render(json.loads((out / name.replace(".html", ".json")).read_text(encoding="utf-8")))
            else:
                return super().do_GET()
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    print(f"serving {out} on http://127.0.0.1:{port}/  (Ctrl-C to stop)")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
