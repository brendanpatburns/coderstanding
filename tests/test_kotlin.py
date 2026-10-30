from pathlib import Path

from coderstanding.language import FileContext
from coderstanding.languages.java import JavaModule
from coderstanding.languages.kotlin import KotlinModule
from coderstanding.scan import scan_repo
from helpers import calls, edge, write_files

FIXTURE = Path(__file__).parent / "fixtures" / "kotlin_sample"
CHAT = "com.example.chat"
UTIL = "com.example.util"


def scan():
    return scan_repo(FIXTURE, modules=[KotlinModule()])


def members(graph, node_id):
    return {m.name: m for m in graph.nodes[node_id].members}


def test_declarations_and_kinds():
    graph = scan()
    kinds = {i: n.kind for i, n in graph.nodes.items()}
    assert kinds[f"{CHAT}.ChatService"] == "class"
    assert kinds[f"{CHAT}.Repo"] == "interface"
    assert kinds[f"{CHAT}.Message"] == "record"  # data class
    assert kinds[f"{CHAT}.ChatService.Companion"] == "inner"
    assert kinds[f"{CHAT}.ChatService.Session"] == "inner"
    assert graph.nodes[f"{CHAT}.Event"].flags == ["abstract"]  # sealed
    assert graph.nodes[f"{CHAT}.Event.Closed"].kind == "inner"
    assert "singleton" in graph.nodes[f"{UTIL}.Registry"].flags  # object
    assert graph.nodes[f"{CHAT}.Repo"].doc == "Stores messages."


def test_members_have_end_lines():
    graph = scan()
    service = members(graph, f"{CHAT}.ChatService")
    assert (service["send"].kind, service["send"].line, service["send"].end) == ("method", 23, 28)
    assert service["repo"].kind == "property" and service["settings"].type == "Settings"
    assert members(graph, f"{CHAT}.ChatService.Companion")["create"].end == 35


def test_module_node_for_top_level_code_and_entry():
    graph = scan()
    module = graph.nodes[f"{UTIL}.UtilKt"]
    assert module.kind == "module" and module.flags == ["program-main", "entry"]
    assert members(graph, module.id)["shout"].kind == "function"
    assert members(graph, module.id)["main"].end == 25
    assert f"{CHAT}.ChatServiceKt" not in graph.nodes  # no top-level functions: no module node


def test_inheritance_edges():
    graph = scan()
    edge(graph, f"{CHAT}.ChatService", f"{CHAT}.Base", "generalization")
    edge(graph, f"{CHAT}.MemoryRepo", f"{CHAT}.Repo", "realization")
    edge(graph, f"{CHAT}.Event.Posted", f"{CHAT}.Event", "generalization")


def test_fields_injection_and_calls():
    graph = scan()
    service = f"{CHAT}.ChatService"
    held = edge(graph, service, f"{CHAT}.Repo", "association")  # constructor `val`
    edge(graph, service, f"{CHAT}.Settings", "composition")  # by lazy { Settings() }
    edge(graph, service, f"{UTIL}.Logger", "composition")  # property initializer
    assert calls(edge(graph, service, f"{UTIL}.Logger", "composition")) == {"info": 1}
    assert [(c.from_member, c.member) for c in held.calls] == [("<init>", "save"), ("send", "save")]
    edge(graph, service, f"{CHAT}.User", "association")  # Map<String, User?>
    edge(graph, service, f"{CHAT}.Message", "dependency")  # return type, local creation


def test_alias_import_and_plain_constructor_parameter():
    graph = scan()
    edge(graph, f"{CHAT}.ChatService", f"{UTIL}.Clock", "dependency")  # `clock: Time`, no val
    edge(graph, f"{CHAT}.ChatService.Companion", f"{UTIL}.Clock", "dependency")  # Time()


def test_companion_calls_and_static_use():
    graph = scan()
    edge(graph, f"{CHAT}.ChatService.Companion", f"{CHAT}.ChatService", "dependency")
    util = f"{UTIL}.UtilKt"
    assert calls(edge(graph, util, f"{CHAT}.ChatService", "dependency")) == {"create": 1}
    edge(graph, util, f"{CHAT}.MemoryRepo", "dependency")  # fully qualified constructor call
    edge(graph, f"{UTIL}.Registry", f"{CHAT}.ChatService", "association")


def test_extension_function_refs_receiver():
    graph = scan()
    ext = edge(graph, f"{UTIL}.UtilKt", f"{CHAT}.Message", "dependency")
    assert ext.detail == ["extension"]


def test_stdlib_names_are_not_unresolved():
    graph = scan()
    assert graph.stack["unresolved"]["refs"] == 0


def test_gradle_stack():
    stack = scan().stack
    build, settings = stack["maven"]
    assert build["stack"] == "Kotlin Android"
    assert build["plugins"] == ["com.android.application", "org.jetbrains.kotlin.jvm"]
    assert settings["name"] == "chat-sample" and settings["modules"] == [":app", ":core"]
    deps = {d["name"]: d for d in stack["deps"] if d["kind"] == "maven"}
    assert deps["com.squareup.okhttp3:okhttp"]["version"] == "4.12.0"
    assert deps["junit:junit"]["scope"] == "test"
    assert "androidx.core" not in str(deps)  # version-catalog references are not read


def test_outside_name_maps_to_gradle_dependency(tmp_path):
    (tmp_path / "build.gradle").write_text("dependencies {\n  implementation 'com.squareup.okhttp3:okhttp:4.12.0'\n}\n")
    (tmp_path / "A.kt").write_text("package a\nimport okhttp3.OkHttpClient\nimport com.squareup.okhttp3.Call\n"
                                   "class A(val c: Call, val k: OkHttpClient)\n")
    graph = scan_repo(tmp_path, modules=[KotlinModule()])
    assert ("a.A", "maven:com.squareup.okhttp3:okhttp", "association") in graph.edges


def test_script_and_gradle_build_files(tmp_path):
    (tmp_path / "tool.kts").write_text("fun run() {}\nrun()\n")
    (tmp_path / "build.gradle.kts").write_text("plugins { id(\"x\") }\n")
    graph = scan_repo(tmp_path, modules=[KotlinModule()])
    assert [n.flags for n in graph.nodes.values()] == [["script-main", "entry"]]


def test_jvm_static_main_in_companion_and_application(tmp_path):
    (tmp_path / "A.kt").write_text("package a\nclass Launcher {\n    companion object {\n"
                                   "        @JvmStatic fun main(args: Array<String>) {\n        }\n    }\n}\n"
                                   "class App : Application()\n")
    graph = scan_repo(tmp_path, modules=[KotlinModule()])
    assert "program-main" in graph.nodes["a.Launcher"].flags
    assert "program-main" in graph.nodes["a.App"].flags


def test_broken_file_does_not_raise(tmp_path):
    (tmp_path / "A.kt").write_text("class {{{ fun ((\n")
    KotlinModule().parse(FileContext(tmp_path, "A.kt"))


def test_kotlin_and_java_link_through_shared_ids(tmp_path):
    write_files(tmp_path, {
        "src/shop/Base.java": "package shop;\npublic class Base {\n    public void run() {}\n}\n",
        "src/shop/Plugin.kt": "package shop\ninterface Plugin {\n    fun start()\n}\n",
        "src/shop/Job.java": "package shop;\npublic class Job implements Plugin {\n"
                             "    public void start() {}\n}\n",
        "src/shop/Worker.kt": "package shop\nclass Worker : Base() {\n    val job: Job = Job()\n"
                              "    fun go() {\n        job.start()\n    }\n}\n",
    })
    graph = scan_repo(tmp_path, modules=[JavaModule(), KotlinModule()])
    edge(graph, "shop.Worker", "shop.Base", "generalization")  # Kotlin extends Java
    edge(graph, "shop.Job", "shop.Plugin", "realization")  # Java implements Kotlin
    held = edge(graph, "shop.Worker", "shop.Job", "composition")  # Kotlin holds a Java type
    assert calls(held) == {"start": 1}
    assert not any(n.external_refs for n in graph.nodes.values())
