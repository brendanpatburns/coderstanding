"""Stack reader (docs/DESIGN.md 8): tech stack, outside dependencies, and outside nodes.

Reads project files (project.godot, addons, *.csproj, pom.xml, package.json, Python requirements,
Docker files, .gitmodules) into graph.stack, then maps the graph's outside refs to those
dependencies: a mapped ref becomes an edge to one `external` node per dependency, Godot
API names are counted as one "godot-api" entry, and the rest is listed as unresolved.
"""

import builtins
import json
import re
import sys
import xml.etree.ElementTree as ElementTree
from collections import Counter
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10: the same reader under its old name
    import tomli as tomllib

from .filewalk import DEFAULT_SKIP_DIRS, list_files
from .godot_files import autoloads, declared_class_name, main_scene, quoted_list, read_config, unquote
from .model import COMM_NAMESPACE, Call, Edge, Location, Node, merge_locations

RENDERERS = {"Forward Plus": "forward_plus", "Mobile": "mobile",
             "GL Compatibility": "gl_compatibility"}
ENGINE_LANGS = ("gdscript", "godot")
UNRESOLVED_TOP = 20
REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)(\[[^\]]*\])?\s*(.*)$")
JVM_LANGS = ("java", "kotlin")
JVM_STANDARD_PREFIXES = ("java.", "javax.", "kotlin.")
# Names Kotlin imports by default (kotlin.*, kotlin.collections.*, java.lang.*), written without a package
KOTLIN_DEFAULT_NAMES = {
    "List", "MutableList", "Map", "MutableMap", "Set", "MutableSet", "Collection", "Iterable",
    "Sequence", "Pair", "Triple", "Result", "Lazy", "Comparable", "Throwable", "Exception",
    "RuntimeException", "IllegalStateException", "IllegalArgumentException", "Thread",
    "Runnable", "StringBuilder", "Regex", "Number", "Enum", "Annotation", "Object", "Class",
}
GRADLE_DEP_RE = re.compile(
    r"""^\s*(\w+)\s*\(?\s*(?:platform\(\s*)?["']([^"':\s$]+):([^"':\s]+)(?::([^"'\s]+))?["']""", re.M)
GRADLE_PLUGIN_RES = (
    re.compile(r"""\bid\s*\(?\s*["']([^"']+)["']"""),
    re.compile(r"""\bapply\s+plugin\s*:\s*["']([^"']+)["']"""),
)
GRADLE_KOTLIN_SHORT_RE = re.compile(r"""\bkotlin\(\s*["']([^"']+)["']\s*\)""")  # kotlin("jvm")
GRADLE_INCLUDE_RE = re.compile(r"^\s*include\s*\(?([^\n)]*)", re.M)
GRADLE_ROOT_RE = re.compile(r"""rootProject\.name\s*=\s*["']([^"']+)["']""")
FROM_RE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", re.I | re.M)
COMPOSE_IMAGE_RE = re.compile(r"^\s*image:\s*[\"']?([^\"'\s#]+)", re.M)
SUBMODULE_RE = re.compile(r'^\[submodule\s+"([^"]+)"\]')


@dataclass
class Dep:
    kind: str  # addon, nuget, npm, pip, maven ("group:artifact"), image, submodule
    name: str
    version: str = ""
    file: str = ""
    info: dict = field(default_factory=dict)
    class_names: set = field(default_factory=set)  # addon classes, for name lookups

    @property
    def node_id(self):
        return f"{self.kind}:{self.name}"

    def to_json(self, usage):
        data = {"kind": self.kind, "name": self.name, "version": self.version,
                "file": self.file, **self.info}
        refs, used_by = usage.get(self.node_id, (0, []))
        if refs:
            data.update(refs=refs, used_by=used_by)
        return data


@dataclass
class Stack:
    godot: list = field(default_factory=list)
    dotnet: list = field(default_factory=list)
    deps: dict = field(default_factory=dict)  # node_id -> Dep
    central_versions: dict = field(default_factory=dict)  # NuGet Directory.Packages.props
    maven: list = field(default_factory=list)  # one entry per pom.xml / build.gradle(.kts) / settings.gradle(.kts)

    def add_dep(self, dep):
        self.deps.setdefault(dep.node_id, dep)


def read_text(root, rel):
    return (Path(root) / rel).read_text(encoding="utf-8", errors="replace")


def parent_dir(rel):
    return rel.rpartition("/")[0]


# --- Godot ---

def godot_renderer(config, features):
    written = unquote(config.get("rendering", {}).get("renderer/rendering_method", ""))
    from_features = next((RENDERERS[f] for f in features if f in RENDERERS), "")
    return written or from_features or "forward_plus"


def read_godot_project(stack, root, rel):
    config = read_config(read_text(root, rel))
    app = config.get("application", {})
    features = quoted_list(app.get("config/features", ""))
    stack.godot.append({
        "file": rel,
        "name": unquote(app.get("config/name", "")),
        "version": next((f for f in features if re.fullmatch(r"\d+(\.\d+)*", f)), ""),
        "renderer": godot_renderer(config, features),
        "csharp": "dotnet" in config or "C#" in features,
        "main_scene": main_scene(config),
        "autoloads": autoloads(config),
    })
    read_addons(stack, root, parent_dir(rel))


def read_addons(stack, root, godot_dir):
    addons = Path(root) / godot_dir / "addons"
    if not addons.is_dir():
        return
    for folder in sorted(p for p in addons.iterdir() if p.is_dir()):
        stack.add_dep(read_addon(root, folder))


def read_addon(root, folder):
    rel_dir = folder.relative_to(root).as_posix()
    cfg = folder / "plugin.cfg"
    plugin = read_config(cfg.read_text(encoding="utf-8", errors="replace")).get("plugin", {}) if cfg.is_file() else {}
    info = {"path": rel_dir}
    for key in ("name", "author"):
        if plugin.get(key):
            info["title" if key == "name" else key] = unquote(plugin[key])
    if any(folder.glob("*.gdextension")):
        info["gdextension"] = True
    classes = {declared_class_name(p.read_text(encoding="utf-8", errors="replace")) for p in folder.rglob("*.gd")}
    return Dep("addon", folder.name, unquote(plugin.get("version", "")),
               f"{rel_dir}/plugin.cfg" if cfg.is_file() else rel_dir, info,
               classes - {""})


# --- .NET ---

def xml_elements(root, rel):
    """(local tag name, element) for every element, XML namespaces dropped."""
    tree = ElementTree.fromstring(read_text(root, rel))
    return [(el.tag.rpartition("}")[2], el) for el in tree.iter()], tree


def attr_or_child(element, name):
    return element.get(name) or next((c.text or "" for c in element
                                      if c.tag.rpartition("}")[2] == name), "")


def read_csproj(stack, root, rel):
    elements, project = xml_elements(root, rel)
    frameworks = [el.text for tag, el in elements
                  if tag in ("TargetFramework", "TargetFrameworks") and el.text]
    packages = [(el.get("Include"), attr_or_child(el, "Version"))
                for tag, el in elements if tag == "PackageReference" and el.get("Include")]
    stack.dotnet.append({
        "file": rel, "sdk": project.get("Sdk", ""),
        "framework": ";".join(frameworks),
        "packages": [{"name": n, "version": v} for n, v in packages],
        "project_refs": [el.get("Include") for tag, el in elements
                         if tag == "ProjectReference" and el.get("Include")],
    })
    for name, version in packages:
        stack.add_dep(Dep("nuget", name, version, rel))


# --- Maven ---

POM_PROPERTY_RE = re.compile(r"\$\{([^}]+)\}")


def pom_child(element, name):
    """Text of the direct child `name` of a pom element (XML namespace dropped), else ""."""
    child = next((c for c in element if c.tag.rpartition("}")[2] == name), None)
    return (child.text or "").strip() if child is not None else ""


def pom_children(element, name):
    child = next((c for c in element if c.tag.rpartition("}")[2] == name), None)
    return list(child) if child is not None else []


def read_pom(stack, root, rel):
    project = ElementTree.fromstring(read_text(root, rel))
    properties = {c.tag.rpartition("}")[2]: (c.text or "").strip() for c in pom_children(project, "properties")}
    parent = next((c for c in project if c.tag.rpartition("}")[2] == "parent"), None)
    group = pom_child(project, "groupId") or (pom_child(parent, "groupId") if parent is not None else "")
    version = pom_child(project, "version") or (pom_child(parent, "version") if parent is not None else "")
    properties.update({"project.groupId": group, "project.version": version})
    fill = lambda value: POM_PROPERTY_RE.sub(lambda m: properties.get(m.group(1), m.group(0)), value)
    managed = {(pom_child(d, "groupId"), pom_child(d, "artifactId")): fill(pom_child(d, "version"))
               for d in pom_children(next((c for c in project if c.tag.rpartition("}")[2] == "dependencyManagement"),
                                          ElementTree.Element("x")), "dependencies")}
    packages = []
    for dependency in pom_children(project, "dependencies"):
        key = (pom_child(dependency, "groupId"), pom_child(dependency, "artifactId"))
        if all(key):
            packages.append({"name": ":".join(key),
                             "version": fill(pom_child(dependency, "version")) or managed.get(key, ""),
                             "scope": pom_child(dependency, "scope") or "compile"})
    stack.maven.append({
        "file": rel, "name": pom_child(project, "name") or pom_child(project, "artifactId"),
        "group": group, "artifact": pom_child(project, "artifactId"), "version": version,
        "packaging": pom_child(project, "packaging") or "jar",
        "modules": [(m.text or "").strip() for m in pom_children(project, "modules")],
        "packages": packages,
    })
    for package in packages:
        stack.add_dep(Dep("maven", package["name"], package["version"], rel, {"scope": package["scope"]}))


def gradle_stack_name(plugins):
    """The stack a build file's plugins name: "Kotlin Android", "Kotlin JVM", "Android", ..."""
    kotlin = [p for p in plugins if p.startswith(("org.jetbrains.kotlin.", "kotlin"))]
    android = any(p.startswith("com.android.") for p in plugins)
    if any("multiplatform" in p for p in kotlin):
        return "Kotlin Multiplatform"
    if kotlin:
        return "Kotlin Android" if android else "Kotlin JVM"
    return "Android" if android else ("Java" if any(p.startswith("java") for p in plugins) else "")


def read_gradle(stack, root, rel):
    """build.gradle(.kts): plugins -> the stack name, `implementation("g:a:v")` lines -> `maven`
    deps. settings.gradle(.kts): the root project name and its `include`d modules. Plain regexes."""
    source = read_text(root, rel)
    if Path(rel).name.startswith("settings."):
        names = [m for line in GRADLE_INCLUDE_RE.findall(source)
                 for m in re.findall(r"""["']([^"']+)["']""", line)]
        found = GRADLE_ROOT_RE.search(source)
        stack.maven.append({"file": rel, "build": "gradle", "name": found.group(1) if found else "",
                            "modules": names})
        return
    plugins = [p for regex in GRADLE_PLUGIN_RES for p in regex.findall(source)]
    plugins += [f"org.jetbrains.kotlin.{p}" for p in GRADLE_KOTLIN_SHORT_RE.findall(source)]
    packages = [{"name": f"{group}:{name}", "version": version,
                 "scope": "test" if configuration.lower().startswith(("test", "androidtest")) else "compile"}
                for configuration, group, name, version in GRADLE_DEP_RE.findall(source)]
    stack.maven.append({"file": rel, "build": "gradle", "stack": gradle_stack_name(plugins),
                        "plugins": plugins, "packages": packages})
    for package in packages:
        stack.add_dep(Dep("maven", package["name"], package["version"], rel, {"scope": package["scope"]}))


def read_package_props(stack, root, rel):
    elements, _ = xml_elements(root, rel)
    for tag, el in elements:
        if tag == "PackageVersion" and el.get("Include"):
            stack.central_versions[el.get("Include")] = el.get("Version", "")


# --- JS / Python ---

def read_package_json(stack, root, rel):
    data = json.loads(read_text(root, rel))
    for section in ("dependencies", "peerDependencies", "devDependencies"):
        for name, version in (data.get(section) or {}).items():
            info = {"dev": True} if section == "devDependencies" else {}
            stack.add_dep(Dep("npm", name, str(version), rel, info))


def add_requirement(stack, line, rel):
    found = REQUIREMENT_RE.match(line.split("#", 1)[0].strip())
    if found:
        stack.add_dep(Dep("pip", found.group(1), found.group(3).split(";")[0].strip(), rel))


def read_requirements(stack, root, rel):
    for line in read_text(root, rel).splitlines():
        if not line.strip().startswith("-"):
            add_requirement(stack, line, rel)


def read_pyproject(stack, root, rel):
    data = tomllib.loads(read_text(root, rel))
    project = data.get("project", {})
    lines = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        lines.extend(extra)
    poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    lines.extend(f"{name} {spec if isinstance(spec, str) else ''}"
                 for name, spec in poetry.items() if name != "python")
    for line in lines:
        add_requirement(stack, line, rel)


# --- Docker / git ---

def split_image(image):
    """"python:3.12-slim" -> ("python", "3.12-slim"); a registry port is not a tag."""
    image = image.split("@", 1)[0]
    name, colon, tag = image.rpartition(":")
    if colon and "/" not in tag:
        return name, tag
    return image, ""


def add_images(stack, images, rel):
    for image in images:
        name, tag = split_image(image)
        stack.add_dep(Dep("image", name, tag, rel))


def read_dockerfile(stack, root, rel):
    stages, images = set(), []
    for image, stage in FROM_RE.findall(read_text(root, rel)):
        if image not in stages and image != "scratch" and "$" not in image:
            images.append(image)
        stages.add(stage)
    add_images(stack, images, rel)


def read_compose(stack, root, rel):
    add_images(stack, COMPOSE_IMAGE_RE.findall(read_text(root, rel)), rel)


def read_gitmodules(stack, root, rel):
    current = None
    for line in read_text(root, rel).splitlines():
        header = SUBMODULE_RE.match(line.strip())
        if header:
            current = Dep("submodule", header.group(1), file=rel)
            stack.add_dep(current)
        elif current and "=" in line:
            key, _, value = line.partition("=")
            if key.strip() in ("path", "url"):
                current.info[key.strip()] = value.strip()


READERS = (
    ("project.godot", read_godot_project),
    ("*.csproj", read_csproj),
    ("pom.xml", read_pom),
    ("build.gradle", read_gradle), ("build.gradle.kts", read_gradle),
    ("settings.gradle", read_gradle), ("settings.gradle.kts", read_gradle),
    ("Directory.Packages.props", read_package_props),
    ("package.json", read_package_json),
    ("requirements*.txt", read_requirements),
    ("pyproject.toml", read_pyproject),
    ("Dockerfile", read_dockerfile), ("Dockerfile.*", read_dockerfile),
    ("*.Dockerfile", read_dockerfile),
    ("docker-compose*.yml", read_compose), ("docker-compose*.yaml", read_compose),
    ("compose.yml", read_compose), ("compose.yaml", read_compose),
    (".gitmodules", read_gitmodules),
)


def read_files(root, skip_dirs):
    stack = Stack()
    for rel in list_files(root, [pattern for pattern, _ in READERS], skip_dirs):
        name = rel.rpartition("/")[2]
        reader = next(fn for pattern, fn in READERS if fnmatch(name, pattern))
        try:
            reader(stack, root, rel)
        except (ValueError, ElementTree.ParseError, tomllib.TOMLDecodeError) as error:
            print(f"warning: stack reader could not read {rel}: {error}", file=sys.stderr)
    for dep in stack.deps.values():
        if dep.kind == "nuget" and not dep.version:
            dep.version = stack.central_versions.get(dep.name, "")
    return stack


# --- mapping outside refs ---

def npm_package(label):
    parts = label.split("/")
    return "/".join(parts[:2]) if label.startswith("@") else parts[0]


def pip_key(name):
    return re.sub(r"[-.]", "_", name.lower())


class DepIndex:
    """Finds the dependency an outside ref (as its label) belongs to."""

    def __init__(self, deps):
        by_kind = lambda kind: [d for d in deps if d.kind == kind]
        self.addon_dirs = [(d.info["path"] + "/", d) for d in by_kind("addon")]
        self.addon_classes = {name: d for d in by_kind("addon") for name in d.class_names}
        self.npm = {d.name: d for d in by_kind("npm")}
        self.pip = {pip_key(d.name): d for d in by_kind("pip")}
        self.nuget = sorted(by_kind("nuget"), key=lambda d: -len(d.name))
        self.maven = by_kind("maven")

    def dep_for(self, node, label):
        if "/" in label or label.endswith((".gd", ".tscn", ".cs")):
            return self.by_path(label)
        head = label.split(".", 1)[0]
        if head in self.addon_classes:
            return self.addon_classes[head]
        if node.lang in ("javascript", "typescript"):
            return self.npm.get(npm_package(label))
        if node.lang == "python":
            return self.pip.get(pip_key(head))
        if node.lang == "csharp":
            return self.nuget_for(label)
        if node.lang in JVM_LANGS:
            return self.maven_for(label)
        return None

    def by_path(self, label):
        addon = next((d for prefix, d in self.addon_dirs if label.startswith(prefix)), None)
        return addon or self.npm.get(npm_package(label))

    def maven_for(self, qualified):
        """The Maven / Gradle dependency owning a package or class name: its groupId, or its
        artifactId read as a package (`kotlinx-coroutines-core` also owns `kotlinx.coroutines`)
        is a prefix of the name; the longest prefix wins."""
        best = (0, None)
        for dep in self.maven:
            group, _, artifact = dep.name.partition(":")
            parts = artifact.replace("-", ".").split(".")
            prefixes = [group] + [".".join(parts[:n]) for n in range(len(parts), 1, -1)]
            for prefix in prefixes:
                if (qualified == prefix or qualified.startswith(prefix + ".")) and len(prefix) > best[0]:
                    best = (len(prefix), dep)
        return best[1]

    def nuget_for(self, namespace):
        return next((d for d in self.nuget
                     if namespace == d.name or namespace.startswith(d.name + ".")), None)


def is_engine_name(node, label):
    """Godot API names: upper-case outside names in Godot code (counted, not listed)."""
    if "/" in label or not label[:1].isupper():
        return False
    if node.lang in ENGINE_LANGS:
        return True
    return node.lang == "csharp" and "Godot" in node.imports and not label.startswith("System")


STANDARD_PYTHON = set(sys.stdlib_module_names) | set(dir(builtins))


def is_standard_name(node, label):
    """The language's own library (Python stdlib and builtins): neither a package nor unknown."""
    if node.lang in JVM_LANGS:
        return label.startswith(JVM_STANDARD_PREFIXES) or (
            node.lang == "kotlin" and label in KOTLIN_DEFAULT_NAMES)
    return node.lang == "python" and label.split(".", 1)[0] in STANDARD_PYTHON


def is_unresolved(node, label):
    """An outside name that is no dependency, engine API or standard library (warnings too)."""
    return not (is_engine_name(node, label) or is_standard_name(node, label))


# --- communications nodes: sockets and network endpoints (DESIGN 8) ---

GODOT_COMM = ("WebSocketPeer", "WebSocketMultiplayerPeer", "ENetMultiplayerPeer", "ENetConnection",
              "StreamPeerTCP", "StreamPeerTLS", "TCPServer", "PacketPeerUDP", "UDPServer",
              "HTTPClient", "HTTPRequest", "WebRTCPeerConnection", "WebRTCMultiplayerPeer")
CSHARP_COMM = ("TcpListener", "TcpClient", "UdpClient", "Socket", "HttpClient", "ClientWebSocket",
               "HttpListener")
# Per language: a type name -> the endpoint's type name; or, for a module whose functions make the
# endpoint, {called function: type name}. The one fixed list of what counts as a socket.
COMM_TYPES = {
    "gdscript": {name: name for name in GODOT_COMM},
    "csharp": {name: name for name in GODOT_COMM + CSHARP_COMM},
    "python": {
        "socket": {"socket": "socket.socket"}, "socket.socket": "socket.socket",
        "websockets": {"serve": "websockets.serve", "connect": "websockets.connect"},
        "asyncio": {"start_server": "asyncio.start_server"},
        "HTTPServer": "http.server.HTTPServer", "http.server.HTTPServer": "http.server.HTTPServer",
        "aiohttp": {"ClientSession": "aiohttp.ClientSession"},
        "aiohttp.ClientSession": "aiohttp.ClientSession",
        "aiohttp.web": {"Application": "aiohttp.web.Application"},
        "aiohttp.web.Application": "aiohttp.web.Application",
        "discord": {"Client": "discord.Client"}, "discord.Client": "discord.Client",
    },
    "javascript": {
        "WebSocket": "WebSocket", "net": {"createServer": "net.createServer"},
        "http": {"createServer": "http.createServer"},
        "https": {"createServer": "https.createServer"},
    },
}
COMM_TYPES["typescript"] = COMM_TYPES["javascript"]
MESSAGE_LANGS = ("javascript", "typescript")  # event names are literal first arguments there
CSHARP_NAMESPACES = "System."  # `System.Net.Sockets.TcpListener` is looked up as `TcpListener`
LISTENER_TYPES = {"TCPServer", "UDPServer", "TcpListener", "HttpListener", "http.server.HTTPServer",
                  "websockets.serve", "asyncio.start_server", "net.createServer",
                  "http.createServer", "https.createServer", "aiohttp.web.Application",
                  "http.server"}
CLIENT_TYPES = {"HTTPClient", "HTTPRequest", "HttpClient", "ClientWebSocket", "TcpClient",
                "UdpClient", "aiohttp.ClientSession", "websockets.connect", "WebSocket",
                "discord.Client", "http.client"}
LISTEN_CALLS = {"listen", "bind", "create_server", "serve", "serve_forever", "start_server",
                "createServer"}
CONNECT_CALLS = {"create_client", "request", "open"}
ROLE_LISTENS, ROLE_CONNECTS, ROLE_BOTH = "listens", "connects", "listens and connects"


def comm_for(node, label, member=""):
    """The communications type an outside name (a field's type, or a ref's label plus the
    function called on it) stands for in this node's language, else None."""
    table = COMM_TYPES.get(node.lang)
    if table is None:
        return None
    if node.lang == "csharp" and label.startswith(CSHARP_NAMESPACES):
        label = label.rsplit(".", 1)[-1]
    entry = table.get(label)
    if entry is None and "." in label:
        label, _, member = label.rpartition(".")
        entry = table.get(label)
    return entry.get(member) if isinstance(entry, dict) else entry


def is_constant_name(name):
    """`STATUS_CONNECTED`, `METHOD_GET`: a constant read, not a use of the socket."""
    return name.isupper()


@dataclass
class CommUse:
    """What one class does with one communications type."""
    details: set = field(default_factory=set)
    calls: list = field(default_factory=list)
    at: list = field(default_factory=list)
    messages: list = field(default_factory=list)

    @property
    def real(self):
        """Created, or called through: a type check (`is` / `as`, a bare local) is neither."""
        return "create" in self.details or bool(self.calls)

    def role(self, comm_type):
        names = {c.member for c in self.calls}
        listens = bool(names & LISTEN_CALLS)
        connects = bool(names & CONNECT_CALLS) or any(n.lower().startswith("connect") for n in names)
        if listens and connects:
            return ROLE_BOTH
        if listens or connects:
            return ROLE_LISTENS if listens else ROLE_CONNECTS
        if comm_type in LISTENER_TYPES:
            return ROLE_LISTENS
        return ROLE_CONNECTS if comm_type in CLIENT_TYPES else ""


def comm_uses(node):
    """comm type -> CommUse, from the node's outside refs (no field info here)."""
    uses = {}
    for edge in node.external_refs.values():
        if edge.kind in ("generalization", "realization"):
            continue
        if edge.kind == "signal":
            comm_type = comm_for(node, edge.to)
            if comm_type and node.lang in MESSAGE_LANGS:
                use = uses.setdefault(comm_type, CommUse())
                use.messages += [d for d in edge.detail if d not in use.messages]  # the event name
            continue
        created = comm_for(node, edge.to)
        if created and "create" in edge.detail:
            use = uses.setdefault(created, CommUse())
            use.details.add("create")
            use.at = merge_locations(use.at, edge.at)
        for call in edge.calls:
            comm_type = comm_for(node, edge.to, call.member)
            if comm_type and not is_constant_name(call.member):
                use = uses.setdefault(comm_type, CommUse())
                use.calls.append(Call(call.member, call.count, call.at, call.from_member))
                use.at = merge_locations(use.at, call.at)
    return {t: u for t, u in uses.items() if u.real}


def comm_fields(node):
    """comm type -> names of the node's fields of that type."""
    found = {}
    for member in node.members:
        comm_type = comm_for(node, member.type) if member.kind == "field" and member.type else None
        if comm_type:
            found.setdefault(comm_type, []).append(member)
    return found


def comm_name(field_name, comm_type, role):
    name = f"{field_name} · {comm_type}" if field_name else comm_type
    return f"{name} ({role})" if role else name


def comm_id(owner_id, comm_type, field_name=""):
    """The one id scheme for communications nodes: `comm:<owner>#<field>` for a held socket,
    else `comm:<owner>@<type>`."""
    return f"comm:{owner_id}" + (f"#{field_name}" if field_name else f"@{comm_type}")


def add_comm_node(graph, owner, comm_type, field_member, use, label=""):
    """One node for a socket the owner holds (id `comm:<owner>#<field>`) or only makes and
    calls (`comm:<owner>@<type>`), and the owner's edge to it. `label` names a node that is held
    by no field (an outside HTTP host): it takes the field's place in the id and name."""
    field_name = field_member.name if field_member else label
    node_id = comm_id(owner.id, comm_type, field_name)
    role = use.role(comm_type)
    line = field_member.line if field_member else (use.at[0].line if use.at else owner.line)
    at = Location(owner.file, line)
    info = {"type": comm_type, "role": role, "owner": owner.id, "field": field_name,
            "messages": sorted(use.messages), "at": at.to_json()}
    graph.add_node(Node(id=node_id, name=comm_name(field_name, comm_type, role), kind="external",
                        lang="", file="", line=at.line, namespace=COMM_NAMESPACE, comm=info))
    detail = (["field"] if field_member else []) + sorted(use.details)
    graph.add_edge(Edge(owner.id, node_id, "composition" if field_member else "dependency",
                        detail=detail, at=merge_locations([at], use.at),
                        calls=[Call(c.member, c.count, c.at, c.from_member) for c in use.calls]))


def add_comm_nodes(graph, owner):
    """Every communications node of one class: fields typed as a socket, and sockets made or
    called in code. With several fields of one type they share the type's calls and role."""
    uses = comm_uses(owner)
    fields = comm_fields(owner)
    for comm_type in sorted(fields.keys() | uses.keys()):
        use = uses.get(comm_type, CommUse())
        for member in fields.get(comm_type) or [None]:
            add_comm_node(graph, owner, comm_type, member, use)


def outside_node_id(graph, dep):
    if dep.node_id not in graph.nodes:
        graph.add_node(Node(id=dep.node_id, name=dep.name, kind="external", lang="",
                            file=dep.file, namespace=dep.kind))
    return dep.node_id


@dataclass
class Usage:
    engine_refs: int = 0
    engine_users: set = field(default_factory=set)
    unresolved: Counter = field(default_factory=Counter)


def map_outside_refs(graph, index):
    usage = Usage()
    for node in list(graph.nodes.values()):
        add_comm_nodes(graph, node)
        for key, edge in list(node.external_refs.items()):
            dep = index.dep_for(node, edge.to)
            if dep:
                del node.external_refs[key]
                edge.to = outside_node_id(graph, dep)
                graph.add_edge(edge)
            elif is_engine_name(node, edge.to):
                usage.engine_refs += len(edge.at)
                usage.engine_users.add(node.id)
            elif is_unresolved(node, edge.to):
                usage.unresolved[edge.to] += len(edge.at)
        add_import_edges(graph, index, node)
    graph.fold_held_edges()
    return usage


def add_import_edges(graph, index, node):
    """A C# `using` of a NuGet package's namespace: the class uses that package."""
    for namespace in node.imports:
        dep = index.nuget_for(namespace)
        if dep:
            graph.add_edge(Edge(node.id, outside_node_id(graph, dep), "dependency",
                                detail=["import"], at=[Location(node.file, node.line)]))


def dep_usage(graph):
    """node_id -> (ref count, sorted users) for every outside node."""
    usage = {}
    for edge in graph.edges.values():
        if graph.nodes.get(edge.to) and graph.nodes[edge.to].kind == "external":
            refs, users = usage.get(edge.to, (0, set()))
            usage[edge.to] = (refs + len(edge.at), users | {edge.from_id})
    return {k: (refs, sorted(users)) for k, (refs, users) in usage.items()}


def stack_json(graph, stack, usage):
    per_dep = dep_usage(graph)
    deps = [d.to_json(per_dep) for _, d in sorted(stack.deps.items())]
    if usage.engine_refs:
        deps.append({"kind": "godot-api", "name": "godot-api", "refs": usage.engine_refs,
                     "used_by": sorted(usage.engine_users)})
    return {
        "languages": dict(sorted(Counter(n.lang for n in graph.nodes.values() if n.lang).items())),
        "godot": stack.godot,
        "dotnet": stack.dotnet,
        "maven": stack.maven,
        "deps": deps,
        "unresolved": {"refs": sum(usage.unresolved.values()),
                       "top": usage.unresolved.most_common(UNRESOLVED_TOP)},
    }


def read_stack(root, graph, skip_dirs=DEFAULT_SKIP_DIRS):
    """Fill graph.stack and turn outside refs to known dependencies into outside nodes."""
    stack = read_files(root, skip_dirs)
    for dep in stack.deps.values():
        if dep.kind == "addon":
            outside_node_id(graph, dep)
    usage = map_outside_refs(graph, DepIndex(list(stack.deps.values())))
    graph.stack = stack_json(graph, stack, usage)
    return graph.stack
