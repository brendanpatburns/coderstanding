"""HTML reader (docs/DESIGN.md 4.4c): a module node per page, its scripts and stylesheets.

`<script src>` and `<link href>` pointing at repo files become `dependency` refs (detail
`import`); outside URLs and inline scripts are ignored. Scripts without `type="module"` are
listed in `page_scripts`: classic scripts of one page share their top-level names.
"""

import posixpath

import tree_sitter_html

from coderstanding.language import Declaration, FileResult, LanguageModule, Ref, Target, register

from .ts_util import children_of_type, descendants, first_child_of_type, line, make_parser, text

PARSER = make_parser(tree_sitter_html.language())
LINK_ATTRIBUTES = {"script": "src", "link": "href"}
OUTSIDE_PREFIXES = ("http:", "https:", "//", "data:", "mailto:", "#")


def attributes(tag):
    """name -> value of a start tag's attributes."""
    found = {}
    for attribute in children_of_type(tag, "attribute"):
        value = first_child_of_type(attribute, "quoted_attribute_value", "attribute_value")
        if value is not None and value.type == "quoted_attribute_value":
            value = first_child_of_type(value, "attribute_value")
        found[text(first_child_of_type(attribute, "attribute_name")).lower()] = text(value)
    return found


def linked_path(tag):
    """The src/href a <script>/<link> tag points at, as written, or ""."""
    name = text(first_child_of_type(tag, "tag_name")).lower()
    attribute = LINK_ATTRIBUTES.get(name)
    return attributes(tag).get(attribute, "") if attribute else ""


def is_classic_script(tag):
    name = text(first_child_of_type(tag, "tag_name")).lower()
    return name == "script" and attributes(tag).get("type", "").lower() != "module"


def repo_path(ctx, written):
    """Repo-relative path of a link written in this page, or "" when it is not a repo file."""
    written = written.split("?")[0].split("#")[0]
    if not written or written.startswith(OUTSIDE_PREFIXES):
        return ""
    base = "" if written.startswith("/") else posixpath.dirname(ctx.rel_path)
    rel = posixpath.normpath(posixpath.join(base, written.lstrip("/")))
    return rel if not rel.startswith("../") and ctx.file_exists(rel) else ""


@register
class HtmlModule(LanguageModule):
    name = "html"
    patterns = ("*.html", "*.htm")

    def parse(self, ctx):
        module_id = ctx.rel_path
        result = FileResult(declarations=[Declaration(
            module_id, posixpath.basename(module_id), "module", 1,
            namespace=posixpath.dirname(module_id), file_node=True)])
        root = PARSER.parse(ctx.read_bytes()).root_node
        for tag in descendants(root):
            if tag.type in ("start_tag", "self_closing_tag"):
                rel = repo_path(ctx, linked_path(tag))
                if rel:
                    result.refs.append(Ref(module_id, Target(path=rel), "dependency",
                                           line(tag), "import"))
                    if is_classic_script(tag):
                        result.page_scripts.append(rel)
        return result
