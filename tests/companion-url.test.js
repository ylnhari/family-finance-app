const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  isTailscaleIPv4,
  normalizeMyCardBenefitsURL,
} = require("../public/companion-url.js");

test("accepts bare loopback and HTTPS origins", () => {
  assert.equal(normalizeMyCardBenefitsURL("http://127.0.0.1:8777"), "http://127.0.0.1:8777/");
  assert.equal(normalizeMyCardBenefitsURL("http://LOCALHOST:8777"), "http://localhost:8777/");
  assert.equal(normalizeMyCardBenefitsURL("http://[::1]:8777"), "http://[::1]:8777/");
  assert.equal(normalizeMyCardBenefitsURL("https://cards.example.test"), "https://cards.example.test/");
});

test("accepts Rover HTTP only on literal Tailscale IPv4 addresses", () => {
  assert.equal(isTailscaleIPv4("100.64.0.1"), true);
  assert.equal(isTailscaleIPv4("100.127.255.254"), true);
  assert.equal(normalizeMyCardBenefitsURL("http://100.64.0.1:23456"), "http://100.64.0.1:23456/");
  assert.equal(normalizeMyCardBenefitsURL("http://0x64400001:23456"), "http://100.64.0.1:23456/");
  assert.equal(normalizeMyCardBenefitsURL("http://1681915905:23456"), "http://100.64.0.1:23456/");
  for (const value of [
    "http://100.63.255.255:23456",
    "http://100.128.0.1:23456",
    "http://0x64800001:23456",
    "http://192.168.1.5:23456",
    "http://rover.example.test:23456",
  ]) assert.equal(normalizeMyCardBenefitsURL(value), null, value);
});

test("accepts harmless WHATWG normalization without widening policy", () => {
  assert.equal(normalizeMyCardBenefitsURL(" HTTP://127.1:8777 "), "http://127.0.0.1:8777/");
  assert.equal(normalizeMyCardBenefitsURL("HTTPS://CARDS.EXAMPLE.TEST"), "https://cards.example.test/");
});

test("rejects non-bare or credentialed destinations", () => {
  for (const value of [
    "javascript:alert(1)",
    "http://user:pass@127.0.0.1:8777",
    "http://127.0.0.1:8777/path",
    "http://127.0.0.1:8777?query=1",
    "http://127.0.0.1:8777#fragment",
    "https://user:pass@cards.example.test",
    "https://cards.example.test/path",
    "https://cards.example.test?query=1",
    "https://cards.example.test#fragment",
    "",
  ]) assert.equal(normalizeMyCardBenefitsURL(value), null, value);
});
