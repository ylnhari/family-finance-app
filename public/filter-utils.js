/* Small pure helpers for searchable finance tables. */
"use strict";

function expenseMatchesQuery(entry, group, query) {
  const terms = String(query || "").trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return true;
  const searchable = [group, entry?.category, entry?.location, entry?.person]
    .filter(value => value != null)
    .join(" ")
    .toLocaleLowerCase();
  return terms.every(term => searchable.includes(term));
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {expenseMatchesQuery};
}
