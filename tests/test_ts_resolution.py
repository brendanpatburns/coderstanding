"""TypeScript projects: path aliases, barrels, default exports, JSX, decorators (DESIGN 4.4b),
and the privacy-safe `diagnose` summary. Fixture: tests/fixtures/ts_app."""

import json
from pathlib import Path

import pytest

from coderstanding import tsconfig
from coderstanding.cli import main
from coderstanding.diagnose import Anonymiser, diagnose_text
from coderstanding.languages.javascript import JavaScriptModule
from coderstanding.scan import scan_repo

FIXTURE = Path(__file__).parent / "fixtures" / "ts_app"
S = "src/"


@pytest.fixture(scope="module")
def graph():
    return scan_repo(FIXTURE, use_cache=False)


def edge(graph, from_id, to, kind):
    return graph.edges.get((from_id, to, kind))


def cls(path, name):
    return f"{path}.{name}"


# --- per pattern -----------------------------------------------------------------------

def test_tsconfig_paths_and_base_url_from_extends(graph):
    user_service = cls(S + "services/user.service.ts", "UserService")
    api = cls(S + "services/api.service.ts", "ApiService")
    held = edge(graph, user_service, api, "composition") or edge(graph, user_service, api, "association")
    assert held is not None  # `@services/api.service` + constructor(private readonly api: ApiService)
    assert edge(graph, cls(S + "repos/user.repo.ts", "UserRepo"), cls(S + "models/user.ts", "UserEntity"),
                "dependency")  # `@/models/user`, as `Repo<UserEntity>` type argument


def test_monorepo_paths_and_workspace_package(graph):
    logger = cls("packages/shared/src/logger.ts", "Logger")
    assert edge(graph, cls(S + "services/api.service.ts", "ApiService"), logger, "association")  # @shared
    assert edge(graph, cls(S + "services/user.service.ts", "UserService"), logger, "association")  # @shared/logger
    assert edge(graph, cls(S + "server/users.controller.ts", "UsersController"), logger,
                "association")  # @example/shared (package.json name)


def test_constructor_parameter_properties_are_held_fields(graph):
    controller = cls(S + "server/users.controller.ts", "UsersController")
    repo = cls(S + "repos/user.repo.ts", "UserRepo")
    held = edge(graph, controller, repo, "association")
    assert held is not None and "field" in held.detail
    assert [c.member for c in held.calls] == ["all"]  # this.repo.all()
    members = {m.name for m in graph.nodes[controller].members if m.kind == "field"}
    assert {"repo", "log"} <= members
    # `readonly logger: Logger` (no access modifier) is a parameter property too
    assert "logger" in {m.name for m in graph.nodes[cls(S + "services/user.service.ts", "UserService")].members}


def test_inject_field_and_js_extension(graph):
    assert edge(graph, cls(S + "services/auth.service.ts", "AuthService"),
                cls(S + "services/api.service.ts", "ApiService"), "association")  # './api.service.js'
    assert edge(graph, cls(S + "repos/user.repo.ts", "UserRepo"), cls(S + "repos/repo.ts", "Repo"),
                "generalization")  # './repo.js' is repo.ts


def test_generic_bases(graph):
    """`implements Store<T>` / `extends Finder<T>` used to give the name "Store<T>"."""
    repo = S + "repos/repo.ts"
    assert edge(graph, cls(repo, "Repo"), cls(repo, "Store"), "realization")
    assert edge(graph, cls(repo, "Store"), cls(repo, "Finder"), "realization")  # interface to interface


def test_barrels_re_exports_and_folder_index(graph):
    module = cls(S + "app/app.module.ts", "AppModule")
    # `import { UserService, Auth } from '../services'`: folder index, export {..} from, alias
    assert edge(graph, module, cls(S + "services/user.service.ts", "UserService"), "dependency")
    assert edge(graph, module, cls(S + "services/auth.service.ts", "AuthService"), "dependency")
    # two-deep chain: '@/features' -> export * from './reports' -> export { ReportService }
    assert edge(graph, S + "main.tsx", cls(S + "features/reports/report.service.ts", "ReportService"),
                "dependency")
    # export type { Permission } from './role' through '@/models'
    assert edge(graph, cls(S + "repos/user.repo.ts", "UserRepo"), cls(S + "models/role.ts", "Permission"),
                "dependency")


def test_namespace_imports_and_exports(graph):
    dashboard = cls(S + "ui/Dashboard.tsx", "Dashboard")
    # export * as Widgets from './items'; <Widgets.Item/>
    assert edge(graph, dashboard, cls(S + "ui/widgets/items.tsx", "Item"), "dependency")
    # import * as Models from '@/models'; Models.User through the barrel's export *
    assert edge(graph, cls(S + "ui/Dashboard.tsx", "DashboardState"), cls(S + "models/user.ts", "User"),
                "association")


def test_default_exports(graph):
    assert edge(graph, S + "main.tsx", cls(S + "ui/Dashboard.tsx", "Dashboard"), "dependency")  # class
    anonymous = cls(S + "state/anon.ts", "default")
    assert graph.nodes[anonymous].name == "anon"  # `export default class { }`
    assert "create" in edge(graph, S + "ui/Profile.tsx", anonymous, "dependency").detail
    assert edge(graph, S + "ui/Profile.tsx", S + "ui/UserCard.tsx", "dependency")  # default function


def test_jsx_hooks_and_react_types(graph):
    jsx = edge(graph, S + "ui/UserList.tsx", S + "ui/UserCard.tsx", "dependency")
    assert "jsx" in jsx.detail and [c.member for c in jsx.calls] == ["UserCard"]
    hook = edge(graph, S + "ui/Profile.tsx", S + "state/useUserStore.ts", "dependency")
    assert [c.member for c in hook.calls] == ["useUserStore"]
    # React.FC<UserListProps> on an arrow-function component
    assert edge(graph, S + "ui/UserList.tsx", cls(S + "ui/UserList.tsx", "UserListProps"), "dependency")
    # class component: Component<ItemProps> type argument
    assert edge(graph, cls(S + "ui/widgets/items.tsx", "Item"), cls(S + "ui/widgets/items.tsx", "ItemProps"),
                "dependency")


def test_decorators_list_used_classes(graph):
    app_module = cls(S + "app/app.module.ts", "AppModule")
    found = edge(graph, app_module, cls(S + "app/app.component.ts", "AppComponent"), "dependency")
    assert "decorator" in found.detail  # @NgModule({ declarations: [AppComponent] })
    assert edge(graph, cls(S + "server/users.module.ts", "UsersModule"),
                cls(S + "server/users.controller.ts", "UsersController"), "dependency")
    assert edge(graph, cls(S + "app/app.component.ts", "AppComponent"), "npm:@angular/core", "dependency")


def test_type_positions_unions_generics_returns(graph):
    store = cls(S + "state/useUserStore.ts", "UserStore")
    assert edge(graph, store, cls(S + "repos/user.repo.ts", "UserRepo"), "association")  # UserRepo | null
    assert edge(graph, cls(S + "services/user.service.ts", "UserService"), cls(S + "models/user.ts", "User"),
                "dependency")  # Promise<User[]> return, import type
    assert edge(graph, cls(S + "models/user.ts", "UserEntity"), cls(S + "models/user.ts", "User"),
                "realization")
    # interface members are fields too
    assert edge(graph, cls(S + "ui/UserList.tsx", "UserListProps"), cls(S + "models/user.ts", "User"),
                "association")


def test_dynamic_import(graph):
    assert edge(graph, S + "main.tsx", S + "features/lazy-page.tsx", "dependency")


def test_type_aliases_and_type_parameters_are_not_unresolved(graph):
    labels = {e.to for n in graph.nodes.values() for e in n.external_refs.values()}
    assert not labels & {"T", "UserId", "Status"}


# --- before / after ----------------------------------------------------------------------

def test_only_really_unused_classes_stay_unconnected(graph):
    """Before this work the fixture had 13 classes with no lines and 38 unused nodes."""
    touched = {i for e in graph.edges.values() if e.from_id != e.to for i in (e.from_id, e.to)}
    classes = [i for i, n in graph.nodes.items() if n.kind in ("class", "interface")]
    assert sorted(i for i in classes if i not in touched) == [cls(S + "lib/unused.ts", "TrulyUnused")]
    unused = sorted(i for i in classes
                    if any(w["kind"] == "unused" for w in graph.nodes[i].warnings))
    assert unused == [cls(S + "lib/unused.ts", "TrulyUnused"),
                      cls(S + "server/users.module.ts", "UsersModule")]
    assert graph.diagnostics["imports"].keys() == {"package-unknown"}  # platform-browser-dynamic


def test_plain_js_paths_still_resolve(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/index.js").write_text("export class Tool {}\n")
    (tmp_path / "util.mjs").write_text("export function help() {}\n")
    (tmp_path / "app.js").write_text("import { Tool } from './lib';\nimport { help } from './util';\n"
                                     "const { Tool: T2 } = require('./lib');\nhelp(); new Tool();\n")
    graph = scan_repo(tmp_path, modules=[JavaScriptModule()], use_cache=False)
    assert ("app.js", "lib/index.js.Tool", "dependency") in graph.edges
    assert ("app.js", "util.mjs", "dependency") in graph.edges


# --- tsconfig parsing --------------------------------------------------------------------

def test_strip_jsonc_keeps_strings():
    text = '{\n // c\n "a": "x // not a comment", /* b */ "b": [1, 2,],\n "c": "/* s */",\n}'
    assert json.loads(tsconfig.strip_jsonc(text)) == {"a": "x // not a comment", "b": [1, 2],
                                                      "c": "/* s */"}


def write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_extends_child_paths_replace_parent_and_base_url_follows_its_config(tmp_path):
    write(tmp_path, "cfg/base.json", '{"compilerOptions": {"baseUrl": "../src", '
                                     '"paths": {"@old/*": ["old/*"]}},}')
    write(tmp_path, "tsconfig.json", '{"extends": "./cfg/base", "compilerOptions": '
                                     '{"paths": {"@/*": ["*"], "@/deep/*": ["d/*"]}}}')
    path_map = tsconfig.read_path_map(tmp_path, ())
    assert path_map.bases("@/x", "src/a.ts") == (["src/x", "src/@/x"], True)
    assert path_map.bases("@/deep/y", "src/a.ts")[0][0] == "src/d/y"  # longest prefix wins
    assert path_map.bases("@old/z", "src/a.ts") == (["src/@old/z"], False)  # replaced
    assert path_map.bases("./rel", "src/a.ts") == ([], False)


def test_paths_without_base_url_are_relative_to_their_config_and_array_extends(tmp_path):
    write(tmp_path, "web/tsconfig.json", '{"extends": ["./a.json", "@missing/config"], '
                                         '"compilerOptions": {}}')
    write(tmp_path, "web/a.json", '{"compilerOptions": {"paths": {"~/*": ["./app/*"]}}}')
    write(tmp_path, "web/tsconfig.app.json", '{"compilerOptions": {"paths": {"~/*": ["other/*"], '
                                             '"#lib": ["./lib/main.ts"]}}}')
    path_map = tsconfig.read_path_map(tmp_path, ())
    assert path_map.bases("~/x", "web/src/b.ts") == (["web/app/x"], True)  # tsconfig.json wins
    assert path_map.bases("#lib", "web/b.ts") == (["web/lib/main.ts"], True)  # app config merged
    assert path_map.bases("~/x", "elsewhere/b.ts") == ([], False)  # outside the config's folder
    assert path_map.info["problems"] == {"missing extends": 1}


def test_unreadable_config_is_counted_not_fatal(tmp_path):
    write(tmp_path, "tsconfig.json", "{ not json")
    assert tsconfig.read_path_map(tmp_path, ()).info["problems"] == {"unreadable": 1}


# --- diagnose ----------------------------------------------------------------------------

BROKEN_MAIN = """import React from 'react';
import fs from 'node:fs';
import { A } from '@/billing/invoice';
import { B } from '@/billing/tax';
import { C } from '~/core/thing';
import { D } from './widgets';
import { E } from './missing-file';
import { F } from '@app/gone';
import { G } from 'core/engine';
import { H } from 'secret-internal-lib';
export class Main { a?: A }
"""


@pytest.fixture
def broken(tmp_path):
    write(tmp_path, "package.json", '{"dependencies": {"react": "1"}}')
    write(tmp_path, "tsconfig.json", '{"compilerOptions": {"paths": {"@app/*": ["src/app/*"]}}}')
    write(tmp_path, "src/main.ts", BROKEN_MAIN)
    write(tmp_path, "src/core/engine.ts", "export class Engine {}\n")
    write(tmp_path, "src/widgets/w.ts", "export class W {}\n")
    return tmp_path


def test_diagnose_counts_unresolved_import_patterns(broken):
    graph = scan_repo(broken, use_cache=False)
    counts = {reason: b["count"] for reason, b in graph.diagnostics["imports"].items()}
    assert counts == {"alias-unmatched": 3, "folder-no-index": 1, "relative-missing": 1,
                      "alias-missing": 1, "bare-folder": 1, "package-unknown": 1}
    text = "\n".join(diagnose_text(graph))
    assert "3 imports starting with an alias marker (`@/` 2, `~/` 1), no tsconfig paths match" in text
    assert "1 imports of folders with no index file (in 1 file)" in text
    assert "tsconfig/jsconfig: 1 files, 1 with paths (1 patterns)" in text
    for secret in ("billing", "invoice", "secret-internal-lib", "engine", "widgets", "@app", "Main"):
        assert secret not in text


def test_diagnose_examples_are_anonymised(broken, capsys):
    data = broken / "data"
    main(["scan", str(broken), "-o", str(data / "graph.json"), "--no-cache"])
    capsys.readouterr()
    main(["diagnose", str(broken), "--data", str(data), "--examples"])
    out = capsys.readouterr().out
    assert "alias patterns: @a/*" in out
    assert "e.g. @/b/c, @/b/d, ~/e/f" in out
    assert broken.name not in out
    for secret in ("billing", "invoice", "secret", "engine", "widgets", "gone"):
        assert secret not in out


def test_anonymiser_keeps_structure_only():
    names = Anonymiser()
    assert names.path("../src/acme/index.js") == "../src/a/index.js"
    assert names.path("@acme/billing/acme.tsx") == "@a/b/a.tsx"


def test_re_export_loop_ends(tmp_path):
    write(tmp_path, "a/index.ts", "export * from '../b';\n")
    write(tmp_path, "b/index.ts", "export * from '../a';\nexport class Real {}\n")
    write(tmp_path, "main.ts", "import { Real, Ghost } from './a';\nnew Real();\n")
    graph = scan_repo(tmp_path, modules=[JavaScriptModule()], use_cache=False)
    assert ("main.ts", "b/index.ts.Real", "dependency") in graph.edges
    assert ("main.ts", "a/index.ts", "dependency") in graph.edges  # Ghost: the barrel itself
