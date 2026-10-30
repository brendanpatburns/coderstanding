"""Cards (docs/DESIGN.md 7, 7d): specs and memos, one Markdown file per card in
`<data>/<kind folder>/<id>.md` (items.py does the file format; same folder rule as tours).

A card *kind* only declares its folder, its stage workflow and its default stage:
- `spec` (`specs/`): the plan as nested epics, stages idea → todo → in-progress → review →
  implemented, plus dropped;
- `memo` (`memos/`, shown as "Notes" in the viewer): comments and information, stages todo →
  in-progress → done, plus na (N/A, the default: just information).
"Memo" is the code and storage name so it never mixes with notes.py (the per-class note fields
in notes.jsonl). A card's target key, for links and later comments, is `<kind>:<id>`
(`spec:effects`, `memo:why-udp`).

Every card has a title, an optional parent (same kind), an optional set level (default:
parent + 1, never shallower), a stage, a work `priority` (how soon) and an `importance` (how
much the functionality matters), both high / medium / low, `links` to classes, connections
and functions (CLI targets: `A`, `A>B`, `A#f`), free text (the body, [[wiki links]]) and a
sibling `order`. Ids come from the title once and never change.

Authors follow the notes model, per field: `by` is the card's author and `ai` lists the fields
still AI drafts (`ai: true` = all; an old file with `by: claude` and no `ai` counts as all AI).
A human edit of a field, or Accept, makes it the human's; an AI write never changes a field a
human has filled in and reports the fields it kept.

The store also computes what the sidebar shows (folding finer cards into their visible parent,
zoom with breadcrumbs, search and filters with paths) and the reverse index from classes and
lines to the cards that link them (`link_index`), so links are stored only on the card.
"""

from dataclasses import dataclass, field

from .apps import AppError, find_id
from .items import (Item, ItemError, ItemStore, check_id, focus_ids, free_id, resolve_target,
                    slug, unknown_targets)
from .notes import AI_AUTHOR, ARROW, FUNCTION_SEP, HUMAN_AUTHOR

PRIORITIES = ("high", "medium", "low")  # work priority and importance share one scale
FIELDS = ("title", "stage", "priority", "importance", "level", "parent", "links", "text")
OLD_FIELDS = {"status": "stage", "classes": "links"}  # field names of older spec files
SORTS = ("tree", "priority", "importance")
PATH_SEP = " › "
STAGE_LABELS = {"in-progress": "in progress", "na": "N/A"}  # UI words where they differ


@dataclass(frozen=True)
class Kind:
    name: str  # `spec` | `memo`: the target key prefix
    folder: str  # in the app's data folder; also the API path and CLI list command
    stages: tuple  # the workflow, in order
    default_stage: str  # a card with no stage set is in this one
    old_stages: dict = field(default_factory=dict)  # values of older files -> stages


SPEC = Kind("spec", "specs", ("idea", "todo", "in-progress", "review", "implemented", "dropped"),
            "idea", {"planned": "todo", "doing": "in-progress", "done": "implemented"})
MEMO = Kind("memo", "memos", ("todo", "in-progress", "done", "na"), "na")
KINDS = {k.name: k for k in (SPEC, MEMO)}
BY_FOLDER = {k.folder: k for k in KINDS.values()}


def stage_value(kind, value):
    """A stage as stored: old names mapped (`done` -> `implemented`), None kept, else checked."""
    if value is None or value == "":
        return None
    value = kind.old_stages.get(value, value)
    if value not in kind.stages:
        raise ValueError(f"stage must be one of {', '.join(kind.stages)}, got {value!r}")
    return value


@dataclass
class Card:
    id: str
    title: str
    parent: str = None
    level: int = None  # the set level; None = parent's level + 1 (1 at the top)
    stage: str = None  # None = the kind's default stage
    priority: str = None
    importance: str = None
    links: list = field(default_factory=list)
    text: str = ""
    order: int = None  # place among its siblings (None: after the ordered ones, by title)
    by: str = HUMAN_AUTHOR
    ai: set = field(default_factory=set)  # fields still AI drafts


def check_fields(kind, values):
    """Raise ValueError for an unknown field or a value of the wrong type; returns the values
    with stages normalized (the API's and the CLI's input)."""
    values = dict(values)
    for name, value in values.items():
        if name not in FIELDS:
            raise ValueError(f"unknown field: {name} (known: {', '.join(FIELDS)})")
        if name == "title" and (not isinstance(value, str) or not value.strip()):
            raise ValueError("a card needs a title")
        if name == "stage":
            values[name] = stage_value(kind, value)
        if name in ("priority", "importance") and value is not None and value not in PRIORITIES:
            raise ValueError(f"{name} must be one of {', '.join(PRIORITIES)}, got {value!r}")
        if name == "level" and value is not None and (type(value) is not int or value < 1):
            raise ValueError(f"level must be a number from 1, got {value!r}")
        if name == "parent" and value is not None and not isinstance(value, str):
            raise ValueError("parent must be an id")
        if name == "links" and (not isinstance(value, list)
                                or not all(isinstance(c, str) and c.strip() for c in value)):
            raise ValueError("links must be a list of targets (Name, A>B, Name#function)")
        if name == "text" and not isinstance(value, str):
            raise ValueError("text must be text")
    return values


def is_empty(value):
    return value in (None, "", [])


def card_from_item(kind, item):
    meta = {OLD_FIELDS.get(k, k): v for k, v in item.meta.items()}
    by = str(meta.get("by") or HUMAN_AUTHOR)
    raw_ai = meta.get("ai", by == AI_AUTHOR)
    ai = set(FIELDS) if raw_ai is True else {OLD_FIELDS.get(f, f) for f in raw_ai} & set(FIELDS) \
        if isinstance(raw_ai, list) else set()
    values = {"title": str(meta.get("title") or item.id), "stage": meta.get("stage"),
              "priority": meta.get("priority"), "importance": meta.get("importance"),
              "level": meta.get("level"), "parent": meta.get("parent"),
              "links": [str(c) for c in meta.get("links") or []], "text": item.body.strip()}
    try:
        values = check_fields(kind, values)
    except ValueError as error:
        raise ItemError(f"{kind.name} {item.id}: {error}") from error
    order = meta.get("order")
    return Card(item.id, **values, order=order if type(order) is int else None, by=by, ai=ai)


def card_to_item(card):
    meta = {"title": card.title}
    for name in ("parent", "level", "stage", "priority", "importance", "order"):
        if getattr(card, name) is not None:
            meta[name] = getattr(card, name)
    meta["links"] = card.links
    meta["by"] = card.by
    if card.ai:
        meta["ai"] = [f for f in FIELDS if f in card.ai]
    return Item(card.id, meta, card.text)


def priority_rank(value):
    return PRIORITIES.index(value) if value in PRIORITIES else len(PRIORITIES)


class Cards:
    """The cards of one kind of one app against its graph: read, check, fold, search, write."""

    def __init__(self, kind, data_dir, graph, notes):
        self.kind = kind
        self.store = ItemStore(data_dir, kind.folder)
        self.graph, self.notes = graph, notes
        self.reload()

    def reload(self):
        self.cards, self.broken = {}, {}
        for card_id in self.store.ids():
            try:
                self.cards[card_id] = card_from_item(self.kind, self.store.read(card_id))
            except ItemError as error:
                self.broken[card_id] = str(error)  # listed, never dropped or overwritten

    def key(self, card):
        """The card's target key (links, comments): `spec:<id>`, `memo:<id>`."""
        return f"{self.kind.name}:{card.id}"

    def stage(self, card):
        return card.stage or self.kind.default_stage

    # --- the tree ---

    def get(self, card_id):
        if card_id not in self.cards:
            raise KeyError(f"no {self.kind.name} {card_id}")
        return self.cards[card_id]

    def parent_of(self, card):
        """The parent card, or None at the top (a missing parent or a cycle counts as the top)."""
        seen, at = {card.id}, card.parent
        while at in self.cards and at not in seen:
            seen.add(at)
            at = self.cards[at].parent
        return self.cards[card.parent] if card.parent in self.cards and at not in seen else None

    def sort_key(self, card):
        return (card.order is None, card.order or 0, card.title.lower(), card.id)

    def children(self, card_id):
        return sorted((c for c in self.cards.values()
                       if (p := self.parent_of(c)) is not None and p.id == card_id),
                      key=self.sort_key)

    def roots(self):
        return sorted((c for c in self.cards.values() if self.parent_of(c) is None),
                      key=self.sort_key)

    def ancestors(self, card):
        """[top, ..., parent] of a card."""
        chain, at = [], self.parent_of(card)
        while at is not None:
            chain.insert(0, at)
            at = self.parent_of(at)
        return chain

    def level(self, card):
        """The card's level: its set level, but never shallower than its parent's + 1."""
        parent = self.parent_of(card)
        least = self.level(parent) + 1 if parent else 1
        return max(card.level or least, least)

    def descendants(self, card_id):
        found = []
        for child in self.children(card_id):
            found += [child, *self.descendants(child.id)]
        return found

    def path(self, card):
        return [*self.ancestors(card), card]

    def tree_key(self, card):
        return [self.sort_key(p) for p in self.path(card)]

    def max_level(self):
        return max((self.level(c) for c in self.cards.values()), default=1)

    # --- checks (unknown links listed like lost notes) ---

    def unknown_links(self, card):
        return unknown_targets(self.graph, self.notes, card.links)

    def problems(self, card):
        found = [f"unknown link: {c}" for c in self.unknown_links(card)]
        if card.parent and card.parent not in self.cards:
            found.append(f"parent {card.parent} not found (shown at the top)")
        elif card.parent and self.parent_of(card) is None:
            found.append(f"parent {card.parent} makes a cycle (shown at the top)")
        if card.level is not None and card.level < self.level(card):
            found.append(f"level {card.level} is not finer than its parent "
                         f"(shown at level {self.level(card)})")
        return found

    def all_problems(self):
        """[(card id, problem)] of every card, unreadable files first."""
        return [*((i, f"unreadable: {e}") for i, e in sorted(self.broken.items())),
                *((c.id, p) for c in sorted(self.cards.values(), key=lambda c: c.id)
                  for p in self.problems(c))]

    # --- search, filters and sorting (levels ignored) ---

    def pick(self, query="", stage=None, priority=None, importance=None, sort=None):
        """Cards matching every word (titles, text, links) and the filters, in tree order or by
        priority / importance (high first, unset last, then tree order)."""
        stage = stage_value(self.kind, stage)
        for name, value in (("priority", priority), ("importance", importance)):
            if value is not None and value not in PRIORITIES:
                raise ValueError(f"{name} must be one of {', '.join(PRIORITIES)}, got {value!r}")
        if sort not in (None, *SORTS):
            raise ValueError(f"sort must be one of {', '.join(SORTS)}, got {sort!r}")
        words = [w.lower() for w in query.split() if w]
        hits = [c for c in self.cards.values()
                if all(w in " ".join([c.id, c.title, c.text, *c.links]).lower() for w in words)
                and (stage is None or self.stage(c) == stage)
                and (priority is None or c.priority == priority)
                and (importance is None or c.importance == importance)]
        if sort in ("priority", "importance"):
            return sorted(hits, key=lambda c: (priority_rank(getattr(c, sort)), self.tree_key(c)))
        return sorted(hits, key=self.tree_key)

    def link_keys(self, card):
        """The classes and lines a card links, as view ids: `A`, `A→B` (a function: its class)."""
        keys = []
        for target in focus_ids(self.graph, self.notes, card.links):
            if ARROW not in target and target not in self.graph.nodes:
                target = target.rpartition(FUNCTION_SEP)[0]
            if target not in keys:
                keys.append(target)
        return keys

    # --- what the sidebar shows (DESIGN 7: folding, zoom, breadcrumbs) ---

    def fold(self, card, shown):
        """A sidebar box: the card, its shown children, and the finer cards folded into it
        (`finer`, their links as `folded_links`; `focus` = own and folded links)."""
        kids, folded = [], []
        for child in self.children(card.id):
            if shown(child):
                kids.append(self.fold(child, shown))
            else:
                folded += [child, *self.descendants(child.id)]
        own = set(card.links)
        folded_links = list(dict.fromkeys(c for s in folded for c in s.links if c not in own))
        return {"id": card.id, "finer": len(folded), "folded": [s.id for s in folded],
                "folded_links": folded_links,
                "focus": focus_ids(self.graph, self.notes, [*card.links, *folded_links]),
                "children": kids}

    def tree(self, level=None, zoom=None):
        """(boxes, breadcrumbs). Without zoom: top cards and every card up to `level` (None:
        all). Zoomed into a card: that card and its children, finer ones folded."""
        if zoom:
            top = self.get(zoom)
            crumbs = [{"id": s.id, "title": s.title} for s in self.path(top)]
            return [self.fold(top, lambda s: s.parent == top.id)], crumbs
        shown = (lambda s: True) if level is None else (lambda s: self.level(s) <= level)
        return [self.fold(s, shown) for s in self.roots()], []

    # --- JSON (the viewer's contract, DESIGN 7) ---

    def card_json(self, card):
        return {"kind": self.kind.name, "key": self.key(card), "id": card.id, "title": card.title,
                "parent": card.parent, "level": self.level(card), "level_set": card.level,
                "stage": self.stage(card), "stage_set": card.stage,
                "priority": card.priority, "importance": card.importance,
                "links": card.links, "text": card.text, "order": card.order,
                "by": card.by, "ai": bool(card.ai), "ai_fields": [f for f in FIELDS if f in card.ai],
                "path": [{"id": s.id, "title": s.title} for s in self.path(card)],
                "children": [c.id for c in self.children(card.id)],
                "unknown_links": self.unknown_links(card),
                "focus": focus_ids(self.graph, self.notes, card.links),
                "problems": self.problems(card)}

    def hit_json(self, card):
        return {"kind": self.kind.name, "id": card.id, "title": card.title,
                "path": PATH_SEP.join(p.title for p in self.path(card)), "stage": self.stage(card),
                "stage_label": STAGE_LABELS.get(self.stage(card), self.stage(card)),
                "priority": card.priority, "importance": card.importance}

    def list_json(self, level=None, zoom=None, query="", stage=None, priority=None,
                  importance=None, sort=None):
        """The sidebar's data; a query, a filter or a sort adds the matching cards with their
        paths as `hits` (None without any)."""
        boxes, crumbs = self.tree(level, zoom)
        asked = query.strip() or stage or priority or importance or sort
        hits = [self.hit_json(c) for c in self.pick(query, stage, priority, importance, sort)] \
            if asked else None
        return {"kind": self.kind.name, "folder": self.kind.folder,
                "items": {i: self.card_json(c) for i, c in self.cards.items()},
                "tree": boxes, "roots": [c.id for c in self.roots()], "crumbs": crumbs,
                "hits": hits, "max_level": self.max_level(),
                "stages": list(self.kind.stages), "default_stage": self.kind.default_stage,
                "stage_labels": {s: STAGE_LABELS.get(s, s) for s in self.kind.stages},
                "priorities": list(PRIORITIES),
                "problems": [{"id": i, "problem": p} for i, p in self.all_problems()]}

    # --- writing ---

    def short_link(self, text):
        """A class link as the file keeps it: its short name when that is unique, else as given."""
        found = resolve_target(self.graph, self.notes, text)
        if not found or found[0] != "node":
            return text
        name = self.graph.nodes[found[1]].name
        try:
            return name if find_id(self.graph, self.notes, name) == found[1] else found[1]
        except AppError:
            return found[1]

    def new_id(self, title):
        return free_id(slug(title, self.kind.name), set(self.cards) | set(self.broken))

    def check_place(self, card):
        """Refuse a missing parent, a parent cycle, or a set level shallower than allowed."""
        if card.parent is None:
            least = 1
        else:
            if card.parent not in self.cards:
                raise ValueError(f"no {self.kind.name} {card.parent} to be the parent")
            at = card.parent
            while at is not None:
                if at == card.id:
                    raise ValueError(f"{card.parent} is inside {card.id}: that would make a cycle")
                at = self.cards[at].parent if at in self.cards else None
            least = self.level(self.cards[card.parent]) + 1
        if card.level is not None and card.level < least:
            raise ValueError(f"level {card.level} is shallower than its parent allows "
                             f"(at least {least})")

    def write(self, card_id, values, by=HUMAN_AUTHOR, ai=False, position=None):
        """Create or update a card with `values` (some of FIELDS) as `by`. A human write makes
        each changed field the human's; an AI write (ai=True) keeps fields a human filled in.
        `position`: its place among its (new) siblings. Returns (card, fields kept)."""
        check_id(card_id)
        if card_id in self.broken:
            raise ValueError(f"{card_id}.md cannot be read; fix the file first: {self.broken[card_id]}")
        values = check_fields(self.kind, values)
        if "links" in values:
            values["links"] = list(dict.fromkeys(self.short_link(c.strip()) for c in values["links"]))
        for name in ("text", "title"):
            if name in values:
                values[name] = values[name].strip()
        if values.get("parent") == "":
            values["parent"] = None
        old = self.cards.get(card_id)
        if old is None:
            if not values.get("title"):
                raise ValueError(f"a new {self.kind.name} needs a title")
            card = Card(card_id, values["title"], by=by)
        else:
            card = Card(**{**vars(old), "links": list(old.links), "ai": set(old.ai)})
        kept = []
        for name, value in values.items():
            if old is not None and getattr(old, name) == value:
                continue  # sent back unchanged: keeps its author
            if ai and old is not None and name not in old.ai and not is_empty(getattr(old, name)):
                kept.append(name)
                continue
            setattr(card, name, value)
            (card.ai.add if ai else card.ai.discard)(name)
            if not ai:
                card.by = by
        if old is None and ai:
            card.ai |= set(values)
        if old is not None and card.parent != old.parent and "level" not in values \
                and card.level is not None:
            parent = self.cards.get(card.parent)
            if card.level <= (self.level(parent) if parent else 0):
                card.level = None  # moved under a deeper parent: follow it
        self.check_place(card)
        moved = old is None or card.parent != old.parent or position is not None
        self.cards[card_id] = card
        if moved:
            self.place(card, position)
        self.store.write(card_to_item(card))
        return card, kept

    def place(self, card, position=None):
        """Put the card at `position` among its siblings (None: last) and renumber them."""
        siblings = [s for s in (self.children(card.parent) if card.parent else self.roots())
                    if s.id != card.id]
        at = len(siblings) if position is None else max(0, min(int(position), len(siblings)))
        self.renumber([*siblings[:at], card, *siblings[at:]], skip=card.id)

    def renumber(self, siblings, skip=None):
        for number, sibling in enumerate(siblings):
            if sibling.order != number:
                sibling.order = number
                if sibling.id != skip:
                    self.store.write(card_to_item(sibling))

    def delete(self, card_id):
        """Delete a card; its children move up to its parent, in its place. Returns their ids."""
        card = self.get(card_id)
        parent = self.parent_of(card)
        siblings = self.children(parent.id) if parent else self.roots()
        kids = self.children(card_id)
        at = siblings.index(card)
        for kid in kids:
            kid.parent = parent.id if parent else None
            self.store.write(card_to_item(kid))
        del self.cards[card_id]
        self.store.delete(card_id)
        self.renumber([*siblings[:at], *kids, *siblings[at + 1:]])
        return [k.id for k in kids]

    def accept(self, card_id, fields=None, by=HUMAN_AUTHOR):
        """Make the AI draft fields (default: all) `by`'s."""
        card = self.get(card_id)
        if fields is not None and (not isinstance(fields, list)
                                   or not set(fields) <= set(FIELDS)):
            raise ValueError(f"fields must be some of {', '.join(FIELDS)}")
        card.ai -= set(FIELDS if fields is None else fields)
        card.by = by
        self.store.write(card_to_item(card))
        return card


def open_cards(app, graph, notes, kind):
    """One kind's cards of an app (in its data folder, like tours); kind = `spec` | `memo`."""
    return Cards(KINDS[kind], app.data, graph, notes)


def link_index(app, graph, notes):
    """The reverse links: view id (`A` class, `A→B` line) -> [hit json] of every card of every
    kind linking it (a function link counts for its class). The one place the view's badges,
    the details panel and `node` read it, so links are stored only on the card."""
    index = {}
    for kind in KINDS:
        cards = open_cards(app, graph, notes, kind)
        for card in sorted(cards.cards.values(), key=cards.tree_key):
            for key in cards.link_keys(card):
                index.setdefault(key, []).append(cards.hit_json(card))
    return index
