"""The 30-second overview of level 0 (docs/mockups/readability.html idea 5): one flow sentence
and a card per domain (what it is for, key classes, who it talks to). Built from the domain notes,
the level 0 lines and their hints; the viewer draws it and the slim text prints it.

The flow sentence is a note on the pseudo domain `domain:*` (field `description`):
`coderstanding set <app> "domain:*" description="..."`. No sentence yet: a plain one is generated.
"""

from .notes import domain_id, node_key, overview_id

KEY_CLASSES = 3  # key classes named on a domain's card


def domain_members(ctx):
    """{domain: [class ids]} of the classes on the map (outside code and tests are not in it)."""
    members = {}
    for node_id in ctx.allowed:
        if not ctx.is_external(node_id) and ctx.domain(node_id):
            members.setdefault(ctx.domain(node_id), []).append(node_id)
    return members


def key_classes(ctx, ids):
    """The domain's most used classes (users, then hub degree, then size): [{id, name, used_by}]."""
    def users(node_id):
        return len({l.from_id for l in ctx.incoming.get(node_id, []) if l.from_id != node_id})
    ranked = sorted(ids, key=lambda i: (-users(i), -(ctx.graph.nodes[i].hub_degree or 0),
                                        -ctx.graph.nodes[i].lines, i))
    return [{"id": i, "name": ctx.graph.nodes[i].name, "title": ctx.title(i), "used_by": users(i)}
            for i in ranked[:KEY_CLASSES]]


def domain_talks(edge_rows):
    """{domain: [{to, hint}]} from the level 0 lines between two domain cards."""
    prefix = domain_id("")
    talks = {}
    for edge in edge_rows:
        if edge["from"].startswith(prefix) and edge["to"].startswith(prefix):
            talks.setdefault(edge["from"][len(prefix):], []).append(
                {"to": edge["to"][len(prefix):], "hint": edge["hint"]})
    return {name: sorted(rows, key=lambda r: r["to"]) for name, rows in talks.items()}


def flow_order(names, members, talks, ctx):
    """The entry domain first, then the domains it talks to, and so on; the rest by name."""
    entries = {d: sum("entry" in ctx.graph.nodes[i].flags for i in ids)
               for d, ids in members.items()}
    order = []
    queue = sorted(names, key=lambda d: (-entries.get(d, 0), d))[:1]
    while queue:
        name = queue.pop(0)
        if name in order:
            continue
        order.append(name)
        queue += [row["to"] for row in talks.get(name, []) if row["to"] in names]
    return order + sorted(set(names) - set(order))


SENTENCE_NAMES = 4  # domains named per clause of the generated sentence


def join_names(names):
    if len(names) > SENTENCE_NAMES:
        names = [*names[:SENTENCE_NAMES], f"{len(names) - SENTENCE_NAMES} more"]
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]


def generated_sentence(order, talks):
    """A plain sentence from the lines: `Commands uses Helper and Notes. Helper uses Notes.`"""
    return " ".join(f"{name} uses {join_names([row['to'] for row in talks[name]])}."
                    for name in order if talks.get(name))


def overview_json(ctx, edge_rows):
    """{sentence, by, ai, generated, domains: [...]} or None when the app has no domains."""
    members = domain_members(ctx)
    if not members:
        return None
    talks = domain_talks(edge_rows)
    order = flow_order(set(members), members, talks, ctx)
    text, by = ctx.notes.get(node_key(overview_id()), "description")
    return {"sentence": text, "by": by, "ai": bool(by) and ctx.notes.is_ai(node_key(overview_id()), by),
            "generated": generated_sentence(order, talks),
            "domains": [{"name": name, "classes": len(members[name]),
                         "hint": ctx.notes.value(node_key(domain_id(name)), "hint"),
                         "description": ctx.notes.value(node_key(domain_id(name)), "description"),
                         "keys": key_classes(ctx, members[name]),
                         "talks": talks.get(name, [])} for name in order]}
