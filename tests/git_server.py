"""A git server over HTTP on loopback, for the tests of repositories: `git http-backend` behind a small HTTP server that
records each request's Authorization header and may require one."""

from __future__ import annotations

import os
import pathlib
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BACKEND = pathlib.Path(subprocess.run(["git", "--exec-path"], capture_output=True, text=True).stdout.strip())


def git(*args: str, cwd: pathlib.Path) -> str:
    environment = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    environment |= {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com"}
    environment |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    done = subprocess.run(["git", *args], cwd=cwd, env=environment, capture_output=True, text=True, check=True)
    return done.stdout.strip()


class GitServer:
    """Serves the bare repositories under `root` at http://127.0.0.1:<port>/<name>.git."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.authorizations: list[str | None] = []
        self.headers: list[dict[str, str]] = []  # every request's headers
        self.required: str | None = None  # an Authorization header every request must carry, when set
        self.down = False  # every request answered 503
        self.redirect: str | None = None  # every request answered 301 to this URL, when set
        self.broken: bytes | None = None  # every request answered 503 with this text, when set
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_GET(self) -> None:
                self.backend()

            def do_POST(self) -> None:
                self.backend()

            def backend(self) -> None:
                authorization = self.headers.get("Authorization")
                server.authorizations.append(authorization)
                server.headers.append(dict(self.headers.items()))
                if server.down:
                    self.send_error(503)
                    return
                if server.broken is not None:
                    self.send_response(503)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(server.broken)))
                    self.end_headers()
                    self.wfile.write(server.broken)
                    return
                if server.redirect is not None:
                    self.send_response(301)
                    self.send_header("Location", server.redirect + self.path)
                    self.end_headers()
                    return
                if server.required is not None and authorization != server.required:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="git"')
                    self.end_headers()
                    return
                path, _, query = self.path.partition("?")
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                environment = {
                    "PATH": os.environ["PATH"],
                    "GIT_PROJECT_ROOT": str(server.root),
                    "GIT_HTTP_EXPORT_ALL": "1",
                    "REQUEST_METHOD": self.command,
                    "PATH_INFO": path,
                    "QUERY_STRING": query,
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": str(len(body)),
                    "GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
                    "HTTP_CONTENT_ENCODING": self.headers.get("Content-Encoding", ""),
                    "REMOTE_ADDR": "127.0.0.1",
                }
                done = subprocess.run([str(BACKEND / "git-http-backend")], input=body, env=environment,
                                      capture_output=True, check=False)  # fmt: skip
                head, _, content = done.stdout.partition(b"\r\n\r\n")
                status = 200
                headers = []
                for line in head.decode("latin-1").split("\r\n"):
                    name, _, value = line.partition(":")
                    if name.lower() == "status":
                        status = int(value.strip().split()[0])
                    elif name:
                        headers.append((name, value.strip()))
                self.send_response(status)
                for name, value in headers:
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.http.server_port}/{name}.git"

    def repository(self, name: str, files: dict[str, str | bytes]) -> Repository:
        """A new repository served as `<name>.git`, its first commit holding `files`."""
        return Repository(self.root, name, files)

    def close(self) -> None:
        self.http.shutdown()
        self.http.server_close()


class Repository:
    """A working copy that pushes each commit to its bare repository, which the server serves."""

    def __init__(self, root: pathlib.Path, name: str, files: dict[str, str | bytes]) -> None:
        self.bare = root / f"{name}.git"
        self.work = root / f"{name}-work"
        git("init", "-q", "--bare", "--initial-branch=main", str(self.bare), cwd=root)
        git("config", "uploadpack.allowFilter", "true", cwd=self.bare)
        git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=self.bare)
        git("init", "-q", "--initial-branch=main", str(self.work), cwd=root)
        git("remote", "add", "origin", str(self.bare), cwd=self.work)
        self.commit(files)

    def commit(self, files: dict[str, str | bytes | None], symlinks: dict[str, str] | None = None) -> str:
        """Write (or, with None, remove) files and symlinks, commit, push; the new commit's hash."""
        for name, content in files.items():
            path = self.work / name
            if content is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content)
        for name, target in (symlinks or {}).items():
            path = self.work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target)
        git("add", "-A", cwd=self.work)
        git("commit", "-q", "-m", "change", "--allow-empty", cwd=self.work)
        git("push", "-q", "origin", "HEAD:main", "--tags", cwd=self.work)
        return git("rev-parse", "HEAD", cwd=self.work)

    def tag(self, name: str) -> None:
        git("tag", name, cwd=self.work)
        git("push", "-q", "origin", name, cwd=self.work)
