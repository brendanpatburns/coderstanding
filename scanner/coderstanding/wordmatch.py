"""Word matching for `find`: split names into words, compare words with small tolerance.

`MAX_PER_HOUR`, `crash_reports.gd` and `CrashReporter` all split into lowercase words; a query
word matches a word exactly (after a crude stem), as a part of it, or within one typo.
"""

import re
from difflib import get_close_matches

WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
SUFFIXES = ("ing", "ers", "er", "ed", "es", "s")
MIN_PART, MIN_TYPO = 3, 5  # shortest query word matching inside a word; shortest typo-tolerant one

# how well a word matched, as a share of the place's points
EXACT, PART, TYPO = 1.0, 0.7, 0.35


def split_words(text):
    """Lowercase words of a snake_case, CamelCase, SCREAMING_CASE, path or file name."""
    return [w.lower() for w in WORD.findall(text or "")]


def stem(word):
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= MIN_PART:
            return word[:-len(suffix)]
    return word


def one_typo(a, b):
    """True when a and b differ by one letter added, dropped, changed or two swapped."""
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long = sorted((a, b), key=len)
    return any(long[:i] + long[i + 1:] == short for i in range(len(long)))


def word_quality(query, word):
    """0 when the query word does not match the word, else EXACT / PART / TYPO."""
    if stem(query) == stem(word):
        return EXACT
    if len(query) >= MIN_PART and (query in word or (len(word) >= MIN_PART and word in query)):
        return PART
    if len(query) >= MIN_TYPO and one_typo(stem(query), stem(word)):
        return TYPO
    return 0


def text_quality(query, text):
    """How well one query word matches somewhere in a text (0 = not at all)."""
    best = max((word_quality(query, w) for w in split_words(text)), default=0)
    if best < PART and query in (text or "").lower():
        best = PART
    return best


def close_names(words, names, limit=5):
    """Names (and their words) nearest to the query, for a search that found nothing."""
    pool = {}
    for name in names:
        pool.setdefault(name.lower(), name)
        for part in split_words(name):
            pool.setdefault(part, name)
    found = []
    for text in [*words, "".join(words)]:
        for key in get_close_matches(text.lower(), pool, n=limit, cutoff=0.6):
            if pool[key] not in found:
                found.append(pool[key])
    return found[:limit]
