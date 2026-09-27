"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {expenseMatchesQuery} = require("../public/filter-utils.js");

const row = {category: "Synthetic groceries", location: "Synthetic market", person: "Synthetic member"};

test("expense search matches section and entry fields without case sensitivity", () => {
  assert.equal(expenseMatchesQuery(row, "Synthetic household", "HOUSEHOLD"), true);
  assert.equal(expenseMatchesQuery(row, "Food", "synthetic groceries"), true);
  assert.equal(expenseMatchesQuery(row, "Food", "SYNTHETIC MARKET"), true);
  assert.equal(expenseMatchesQuery(row, "Food", "synthetic member"), true);
});

test("expense search treats each word as required and blank query as unfiltered", () => {
  assert.equal(expenseMatchesQuery(row, "Food", "market synthetic"), true);
  assert.equal(expenseMatchesQuery(row, "Food", "market utilities"), false);
  assert.equal(expenseMatchesQuery(row, "Food", "   "), true);
});

test("expense search safely handles missing row fields", () => {
  assert.equal(expenseMatchesQuery(null, "Miscellaneous", "misc"), true);
  assert.equal(expenseMatchesQuery(null, "Miscellaneous", "category"), false);
});
