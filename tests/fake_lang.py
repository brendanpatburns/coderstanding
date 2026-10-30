"""A tiny line-based fake language that proves the module contract.

    type <id> <kind> [flag,flag]   declare a type (first one is the file's node)
    base <name>                     generalization ref from the current type
    ref <kind> <target> [detail] [call]
    using <prefix> | alias <name> <target> | global <name> <target> | mark <target> <flags>
    scope <dotted scope>            scope for following refs
    end <line>                      last line of the current type (its size span)

A target is `path:<p>`, `id:<i>`, or a name; `<target>#<member>` adds a member.
"""

from coderstanding.language import (Declaration, FileResult, LanguageModule, Mark, Ref,
                                    Registry, Target)

registry = Registry()


def parse_target(text):
    text, _, member = text.partition("#")
    prefix, _, rest = text.partition(":")
    if prefix in ("path", "id") and rest:
        return Target(**{prefix: rest}, member=member)
    return Target(name=text, member=member)


@registry.register
class FakeModule(LanguageModule):
    name = "fake"
    patterns = ("*.fk",)
    path_suffixes = ("", ".fk")

    def parse(self, ctx):
        result = FileResult()
        current, scope = None, ""
        for line_no, line in enumerate(ctx.read_text().splitlines(), 1):
            word, *args = line.split() or [""]
            if word == "type":
                flags = args[2].split(",") if len(args) > 2 else []
                current = Declaration(args[0], args[0].rsplit(".", 1)[-1], args[1], line_no,
                                      flags, file_node=not result.declarations)
                result.declarations.append(current)
            elif word == "base":
                current.base = current.base or args[0]
                result.refs.append(Ref(current.id, parse_target(args[0]), "generalization",
                                       line_no, scope=scope))
            elif word == "ref":
                detail = args[2] if len(args) > 2 else ""
                call = args[3] if len(args) > 3 else ""
                result.refs.append(Ref(current.id, parse_target(args[1]), args[0], line_no,
                                       detail, call, scope))
            elif word == "using":
                result.imports.append(args[0])
            elif word == "alias":
                result.aliases[args[0]] = parse_target(args[1])
            elif word == "global":
                result.global_aliases[args[0]] = parse_target(args[1])
            elif word == "mark":
                result.marks.append(Mark(parse_target(args[0]), args[1].split(",")))
            elif word == "scope":
                scope = args[0]
            elif word == "end":
                current.end_line = int(args[0])
            elif word == "boom":
                raise ValueError("broken file")
        return result


FAKE = registry.modules()[0]
