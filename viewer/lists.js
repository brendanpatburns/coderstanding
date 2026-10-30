// Long-list helpers for the details panel: merge calls by member, show the first few items, fold the rest.
(() => {
const SHOW_FIRST = 8;

// Calls come per (from_member, member) pair; merge by member, sum counts, biggest first, keep the callers.
function mergeCalls(calls) {
  const byMember = new Map();
  for (const c of calls) {
    const entry = byMember.get(c.member) || { member: c.member, count: 0, callers: [] };
    entry.count += c.count;
    if (c.from_member && !entry.callers.includes(c.from_member)) entry.callers.push(c.from_member);
    byMember.set(c.member, entry);
  }
  return [...byMember.values()].sort((a, b) => b.count - a.count || a.member.localeCompare(b.member));
}

// `items` are rendered <li> strings: the first few show, the rest sit in a native <details>.
function collapsedList(items, first = SHOW_FIRST) {
  if (!items.length) return "";
  if (items.length <= first) return `<ul>${items.join("")}</ul>`;
  return `<ul>${items.slice(0, first).join("")}</ul><details><summary>Show all ${items.length}</summary><ul>${items.slice(first).join("")}</ul></details>`;
}

const api = { SHOW_FIRST, mergeCalls, collapsedList };
if (typeof module !== "undefined") module.exports = api;
else window.Lists = api;
})();
