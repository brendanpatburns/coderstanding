"""Local server for the viewer: `coderstanding serve <repo>` (docs/DESIGN.md section 3).

Stdlib only. Serves `viewer/` and a small JSON API; all view logic is in views.py.
Binds 127.0.0.1. The local server is also the helper behind the hosted page (a static shell on
the lab, docs/DESIGN.md 12b): it lets that page's origin (userconfig.hosted_origin) call it cross-origin for
every read and write, and refuses writes from any other site. Maps never leave this PC.
"""

import json
import re
import threading
from collections import Counter
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .checks import WARNING_HELP
from .graph_json import external_to_json, write_graph
from .layout import read_layout, update_layout
from .apps import AppError, load_graph
from .hub import Hub
from .notes import FINGERPRINTED, HUMAN_AUTHOR, NODE_PREFIX, STATUSES, TEXT_FIELDS, WRITABLE_FIELDS, clean_value
from .textout import format_tour, tour_markdown, view_text
from .tours import AUDIO_TYPES, MAX_AUDIO_BYTES, open_tours
from .userconfig import hosted_origin
from .views import compute_view, effective_level, note_json, with_code_doc

HOST = "127.0.0.1"
DEFAULT_PORT = 8517
HUB_ROUTES = ("/api/apps", "/api/rescan")  # writes that need no per-app Api
VIEWER_DIR = Path(__file__).resolve().parents[2] / "viewer"
LOCAL_HOSTS = (HOST, "localhost")
JSON_TYPE = "application/json"
APP_PATH = re.compile(r"^/api/apps/([^/]+)$")  # the app manager: DELETE (remove), PATCH (rename)
TOUR_PATH = re.compile(r"^/api/tours/([^/]+?)(?:/(still-true|accept)|/audio/([^/]+))?$")


def _flag(params, name, default=True):
    value = params.get(name, [""])[0].lower()
    return default if value == "" else value not in ("0", "false", "no", "off")


def _zoom(params):
    """The zoom dict for compute_view, or None. `via` repeats once per hidden class id."""
    zoom_from = params.get("zoom_from", [""])[0]
    if not zoom_from:
        return None
    via = [v for v in params.get("via", []) if v]
    zoom = {"from": zoom_from, "to": params.get("zoom_to", [""])[0] or None,
            "level": int(params.get("zoom_level", ["1"])[0])}
    return {**zoom, "via": via} if via else zoom  # no via: the engine finds it


def view_args(params):
    """compute_view keyword arguments from /api/view query parameters (`expand` repeats)."""
    return {"level": int(params.get("level", ["1"])[0]),
            "view": params.get("view", ["all"])[0],
            "show": {"scene": _flag(params, "scene"), "external": _flag(params, "external"),
                     "test": _flag(params, "test", default=False)},
            "zoom": _zoom(params),
            "expand": [i for i in params.get("expand", []) if i],
            "focus": [i for i in params.get("focus", []) if i]}


def _edge_ref(graph, edge, other_id):
    other = graph.nodes.get(other_id)
    return {"id": other_id, "name": other.name if other else other_id, "kind": edge.kind,
            "calls": [{"member": c.member, "count": c.count} for c in edge.calls]}


class Api:
    """One method per API route, for one app (see apps.App): its graph, notes and layout."""

    def __init__(self, app, graph=None):
        self.app = app
        self.state_dir = app.data
        self.live = graph is None  # a given graph (tests) is never rescanned
        self.graph = graph if graph is not None else self.load()
        self.notes_lock = threading.Lock()  # one notes read-modify-write at a time
        self.tours_lock = threading.Lock()  # one tour read-modify-write at a time

    def load(self):
        return load_graph(self.app)

    @property
    def notes(self):
        notes = self.app.load_notes()  # read per request, so `coderstanding set` shows up live
        self.app.checked(self.graph, notes)
        return notes

    def meta(self):
        notes = self.notes
        levels = Counter(effective_level(self.graph, notes, i)
                         for i, n in self.graph.nodes.items() if n.kind != "external")
        return {"repo": self.graph.repo, "scannedAt": self.graph.scanned_at,
                "levels": {str(k): v for k, v in sorted(levels.items())},
                "max_level": max(levels, default=1), "domains": bool(notes.domains()),
                "statuses": list(STATUSES), "tags": notes.tags(), "warning_help": WARNING_HELP}

    def refresh(self):
        """Rescan when files changed since the last scan (cheap mtime check)."""
        if self.live:
            self.graph = self.load()

    def view(self, params):
        self.refresh()
        return compute_view(self.graph, self.notes, **view_args(params))

    def text(self, params):
        """The view as slim text, first line the CLI command (the viewer's Copy for AI)."""
        self.refresh()
        args = view_args(params)
        return view_text(self.app.name, self.graph, self.notes,
                         select=params.get("select", [""])[0] or None, **args)

    def node(self, node_id):
        node = self.graph.nodes.get(node_id)
        if node is None:
            return None
        edges = self.graph.edges.values()
        return {"id": node.id, "name": node.name, "kind": node.kind, "lang": node.lang,
                "file": node.file, "line": node.line, "flags": node.flags,
                "base": node.base, "namespace": node.namespace, "level": node.level, "doc": node.doc,
                "members": [vars(m) for m in node.members],
                "external_refs": [external_to_json(e)
                                  for _, e in sorted(node.external_refs.items())],
                "out": [_edge_ref(self.graph, e, e.to) for e in edges if e.from_id == node_id],
                "in": [_edge_ref(self.graph, e, e.from_id) for e in edges if e.to == node_id]}

    def note_key(self, notes, body):
        """The body's note key, if it names something in the graph (ValueError otherwise)."""
        key = body.get("key")
        if not isinstance(key, str) or not notes.key_exists(key, self.graph):
            raise ValueError(f"unknown note key: {key!r}")
        return key

    def merged_note(self, notes, key):
        node = self.graph.nodes.get(key[len(NODE_PREFIX):]) if key.startswith(NODE_PREFIX) else None
        return {"key": key, **with_code_doc(note_json(notes, key, graph=self.graph), node.doc if node else "")}

    def put_notes(self, body):
        """Write WRITABLE_FIELDS as you. Empty clears; tags may be `a, b` text."""
        fields = body.get("fields")
        if not isinstance(fields, dict) or not set(fields) <= set(WRITABLE_FIELDS):
            raise ValueError(f"fields must be an object with some of {', '.join(WRITABLE_FIELDS)}")
        with self.notes_lock:
            notes = self.notes
            key = self.note_key(notes, body)
            notes.set(key, HUMAN_AUTHOR,
                      {name: clean_value(name, value) for name, value in fields.items()}, ai=False,
                      graph=self.graph)
            self.app.save_notes(notes)
            return self.merged_note(notes, key)

    def accept_notes(self, body):
        """Turn the AI draft values of the listed fields into yours (as HUMAN_AUTHOR)."""
        names = body.get("fields")
        if not isinstance(names, list) or not set(names) <= set(TEXT_FIELDS):
            raise ValueError(f"fields must be a list of some of {', '.join(TEXT_FIELDS)}")
        with self.notes_lock:
            notes = self.notes
            key = self.note_key(notes, body)
            for name in names:
                value, by = notes.get(key, name)
                if notes.is_ai(key, by):
                    notes.set(key, HUMAN_AUTHOR, {name: value}, ai=False, graph=self.graph)
            self.app.save_notes(notes)
            return self.merged_note(notes, key)

    def still_true(self, body):
        """Mark the listed fields (default: all text fields) "still true" for the code as it is."""
        names = body.get("fields", list(FINGERPRINTED))
        if not isinstance(names, list) or not set(names) <= set(FINGERPRINTED):
            raise ValueError(f"fields must be a list of some of {', '.join(FINGERPRINTED)}")
        with self.notes_lock:
            notes = self.notes
            key = self.note_key(notes, body)
            notes.confirm(key, self.graph, names)
            self.app.save_notes(notes)
            return self.merged_note(notes, key)

    # tours (DESIGN 7c)
    def tours(self):
        self.refresh()
        return open_tours(self.app, self.graph, self.notes)

    def tour_list(self, params):
        return self.tours().list_json(params.get("q", [""])[0])

    def tour(self, tour_id):
        tours = self.tours()
        return tours.tour_json(tours.get(tour_id), self.app.name)

    def tour_md(self, tour_id):
        return tour_markdown(self.app.name, self.tour(tour_id))

    def tour_text(self, tour_id, params):
        """`coderstanding tour <app> <id> [--step N]` as text (the same textout function)."""
        step = params.get("step", [""])[0]
        return format_tour(self.app.name, self.tour(tour_id), number=int(step) if step else None)

    def tour_audio(self, tour_id, key):
        return self.tours().audio_file(tour_id, key)

    def tour_write(self, change):
        """Run change(tours) under the lock; answers the changed tour as GET does."""
        with self.tours_lock:
            tours = self.tours()
            tour = change(tours)
            return tours.tour_json(tour, self.app.name)

    def put_tour(self, tour_id, body):
        return self.tour_write(lambda tours: tours.put(tour_id, body))

    def tour_still_true(self, tour_id, body):
        return self.tour_write(lambda tours: tours.still_true(tour_id, body.get("keys"))[0])

    def tour_accept(self, tour_id, body):
        return self.tour_write(lambda tours: tours.accept(tour_id, body.get("keys")))

    def put_audio(self, tour_id, key, data, content_type, dur=None):
        return self.tour_write(lambda tours: tours.set_audio(tour_id, key, data, content_type, dur))

    def delete_audio(self, tour_id, key):
        return self.tour_write(lambda tours: tours.delete_audio(tour_id, key))

    def stack(self):
        return self.graph.stack

    def layout(self):
        return {"items": [item for _, item in sorted(read_layout(self.state_dir).items())]}

    def put_layout(self, body):
        update_layout(self.state_dir, body.get("items", []), body.get("remove", []))
        return self.layout()


class Handler(SimpleHTTPRequestHandler):
    """Static viewer files plus /api/* routes."""

    hub = None  # set by make_server
    allow_origin = None  # set by make_server
    api = None  # the Api for this request's ?app=, set per request

    def log_message(self, format, *args):  # keep the terminal quiet
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        if self.allowed_cross_origin():
            self.send_header("Access-Control-Allow-Origin", self.allow_origin)
            self.send_header("Vary", "Origin")
        super().end_headers()

    def local_host(self):
        """Asked for by a loopback name (blocks DNS rebinding of other names onto 127.0.0.1)."""
        return urlsplit("//" + self.headers.get("Host", "")).hostname in LOCAL_HOSTS

    def allowed_cross_origin(self):
        """The hosted page calling this local server."""
        origin = self.headers.get("Origin")
        return bool(origin) and origin == self.allow_origin

    def origin_ok(self):
        """Writes only from no Origin (CLI, curl), this server's own pages, or the hub's page."""
        origin = self.headers.get("Origin")
        own = f"http://{self.headers.get('Host', '')}"
        return self.local_host() and (origin is None or origin == own or self.allowed_cross_origin())

    def do_OPTIONS(self):
        """CORS preflight for the hosted page, with Chrome's Private Network Access header."""
        if not (self.local_host() and self.allowed_cross_origin()):
            return self.send_error_json(HTTPStatus.FORBIDDEN, "cross-site request refused")
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, content_type="text/plain; charset=utf-8", filename=None):
        body = text.encode() if isinstance(text, str) else text
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json({"error": message}, status)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def pick_app(self, params):
        """Set self.api from ?app= (default: the app serve was given, else the first)."""
        try:
            self.api = self.hub.api(params.get("app", [""])[0] or None)
        except KeyError as error:
            self.send_error_json(HTTPStatus.NOT_FOUND, error.args[0])
            return False
        except AppError as error:  # e.g. the app has no supported files now
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))
            return False
        except Exception as error:  # a bad map must still answer, not drop the connection
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(error).__name__}: {error}")
            return False
        return True

    def do_GET(self):
        url = urlsplit(self.path)
        if not url.path.startswith("/api/"):
            return super().do_GET()
        if not self.local_host():
            return self.send_error_json(HTTPStatus.FORBIDDEN, "unknown host name")
        params = parse_qs(url.query)
        if url.path == "/api/apps":
            return self.send_json(self.hub.listing(details=params.get("details") == ["1"]))
        if not self.pick_app(params):
            return
        try:
            self.route_get(url.path, params)
        except (ValueError, KeyError, TypeError) as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
        except Exception as error:  # AppError (a map went missing) or any bug: JSON, never a dropped connection
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(error) or type(error).__name__)

    def route_get(self, path, params):
        if path == "/api/meta":
            return self.send_json(self.api.meta())
        if path == "/api/view":
            return self.send_json(self.api.view(params))
        if path == "/api/text":
            return self.send_text(self.api.text(params))
        if path == "/api/stack":
            return self.send_json(self.api.stack())
        if path == "/api/layout":
            return self.send_json(self.api.layout())
        if path == "/api/tours":
            return self.send_json(self.api.tour_list(params))
        if path.startswith("/api/tours/"):
            return self.route_tour_get(path, params)
        if path.startswith("/api/node/"):
            node = self.api.node(unquote(path[len("/api/node/"):]))
            if node is None:
                return self.send_error_json(HTTPStatus.NOT_FOUND, "no such node")
            return self.send_json(node)
        self.send_error_json(HTTPStatus.NOT_FOUND, "no such route")

    def route_tour_get(self, path, params):
        """GET /api/tours/<id>, /api/tours/<id>.md, /api/tours/<id>.txt?step=N, /api/tours/<id>/audio/<key>."""
        match = TOUR_PATH.match(path)
        if not match or match[2]:
            return self.send_error_json(HTTPStatus.NOT_FOUND, "no such route")
        tour_id, key = unquote(match[1]), match[3] and unquote(match[3])
        try:
            if key:
                file, content_type = self.api.tour_audio(tour_id, key)
                return self.send_text(file.read_bytes(), content_type)
            if tour_id.endswith(".md"):
                tour_id = tour_id[:-len(".md")]
                return self.send_text(self.api.tour_md(tour_id), "text/markdown; charset=utf-8",
                                      f"{tour_id}.md")
            if tour_id.endswith(".txt"):
                return self.send_text(self.api.tour_text(tour_id[:-len(".txt")], params))
            return self.send_json(self.api.tour(tour_id))
        except KeyError as error:
            return self.send_error_json(HTTPStatus.NOT_FOUND, error.args[0])

    def tour_action(self, method, path, params):
        """(action, accepted body types) of a tour write route, or None (DESIGN 7c).
        Recordings come as raw audio (the JSON-only rule waived for these types); DELETE has
        no body (types None)."""
        match = TOUR_PATH.match(path)
        if not match:
            return None
        tour_id, verb, key = unquote(match[1]), match[2], match[3] and unquote(match[3])
        if key and method == "PUT":
            dur = params.get("dur", [""])[0]
            return (lambda data: self.api.put_audio(
                tour_id, key, data, self.headers.get_content_type(), float(dur) if dur else None)), \
                tuple(AUDIO_TYPES)
        if key and method == "DELETE":
            return (lambda _: self.api.delete_audio(tour_id, key)), None
        if verb and method == "POST":
            name = "tour_still_true" if verb == "still-true" else "tour_accept"
            return (lambda body: getattr(self.api, name)(tour_id, body)), (JSON_TYPE,)
        if not verb and not key and method == "PUT":
            return (lambda body: self.api.put_tour(tour_id, body)), (JSON_TYPE,)
        return None

    def do_PUT(self):
        self.route_write("PUT", "/api/layout", lambda body: self.api.put_layout(body),
                         "/api/notes", lambda body: self.api.put_notes(body))

    def do_POST(self):
        self.route_write("POST", "/api/notes/accept", lambda body: self.api.accept_notes(body),
                         "/api/notes/still-true", lambda body: self.api.still_true(body),
                         "/api/apps", lambda body: self.hub.add_app(body),
                         "/api/rescan", lambda body: self.hub.start_scan(self.app_name))

    def do_DELETE(self):
        self.route_write("DELETE")

    def do_PATCH(self):
        self.route_write("PATCH")

    def app_action(self, method, path, params):
        """(action, accepted body types) of an app manager route, or None."""
        match = APP_PATH.match(path)
        if not match:
            return None
        name = unquote(match[1])
        if method == "DELETE":
            return (lambda _: self.hub.remove(name, params)), None
        if method == "PATCH":
            return (lambda body: self.hub.rename(name, body)), (JSON_TYPE,)
        return None

    def discard_body(self, length, limit=4 * MAX_AUDIO_BYTES):
        """Read and drop a refused body so the client sees the answer, not a broken pipe;
        a huge one is left unread and the connection closed."""
        if length > limit:
            self.close_connection = True
            return
        while length > 0:
            chunk = self.rfile.read(min(length, 1 << 16))
            if not chunk:
                break
            length -= len(chunk)

    def read_body(self, types):
        """The request body: raw bytes for audio, else JSON (an empty body is {})."""
        if types and JSON_TYPE not in types:
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))
        return self.read_json()

    def route_write(self, method, *pairs):
        url = urlsplit(self.path)
        params = parse_qs(url.query)
        found = dict(zip(pairs[::2], pairs[1::2])).get(url.path)
        found = (found, (JSON_TYPE,)) if found else (
            self.tour_action(method, url.path, params) or self.app_action(method, url.path, params))
        if found is None:
            return self.send_error_json(HTTPStatus.NOT_FOUND, "no such route")
        action, types = found
        if not self.origin_ok():
            return self.send_error_json(HTTPStatus.FORBIDDEN, "cross-site request refused")
        if types and self.headers.get_content_type() not in types:  # no plain-form posts
            return self.send_error_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, f"send {' or '.join(types)}")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_AUDIO_BYTES:
            self.discard_body(length)
            return self.send_error_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                                        f"at most {MAX_AUDIO_BYTES} bytes")
        self.app_name = params.get("app", [""])[0] or None
        per_app = url.path not in HUB_ROUTES and not APP_PATH.match(url.path)
        if per_app and not self.pick_app(params):
            return
        try:
            self.send_json(action(self.read_body(types)))
        except AppError as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
        except Exception as error:  # never drop the connection
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(error) or type(error).__name__)


def make_server(api_or_hub, port=DEFAULT_PORT, viewer_dir=VIEWER_DIR, host=HOST,
                allow_origin=None):
    """An Api (one fixed app, as tests use it) or a Hub (every registered app)."""
    hub = api_or_hub if isinstance(api_or_hub, Hub) else Hub(None, fixed={api_or_hub.app.name: api_or_hub})
    handler = type("BoundHandler", (Handler,), {"hub": hub, "allow_origin": allow_origin})
    return ThreadingHTTPServer((host, port), partial(handler, directory=str(viewer_dir)))


def serve(app=None, port=DEFAULT_PORT, allow_origin=None):
    """app: open on this one (registered or not); None: the first registered app.
    allow_origin (or env CODERSTANDING_ALLOW_ORIGIN): the hosted page origin let in cross-origin;
    default: userconfig.hosted_origin() (env CODERSTANDING_ALLOW_ORIGIN, then hosted.json)."""
    hub = Hub(Api, initial=app)
    if not hub.known_apps():
        raise AppError("no apps registered: `coderstanding add <name> <folder>` first")
    first = hub.api()  # scans it now if stale, so a bad repo fails here
    print(f"{len(hub.known_apps())} apps; {first.app.name}: {len(first.graph.nodes)} nodes, "
          f"{len(first.graph.edges)} edges ({first.app.data})")
    allow_origin = allow_origin or hosted_origin()
    server = make_server(hub, port, allow_origin=allow_origin)
    print(f"Coderstanding: http://{HOST}:{server.server_port}/  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
