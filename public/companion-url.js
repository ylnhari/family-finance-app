(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.FFACompanion = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function isTailscaleIPv4(hostname) {
    const parts = String(hostname).split(".");
    if (parts.length !== 4 || parts.some(part => !/^(0|[1-9]\d{0,2})$/.test(part))) return false;
    const octets = parts.map(Number);
    if (octets.some(octet => octet > 255)) return false;
    return octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127;
  }

  function normalizeMyCardBenefitsURL(raw) {
    try {
      const url = new URL(String(raw || ""));
      const loopback = new Set(["127.0.0.1", "localhost", "[::1]"]);
      const trustedHttp = loopback.has(url.hostname) || isTailscaleIPv4(url.hostname);
      if (!["http:", "https:"].includes(url.protocol) ||
          (url.protocol === "http:" && !trustedHttp) || url.username || url.password ||
          url.pathname !== "/" || url.search || url.hash) return null;
      return new URL(url.origin + "/").href;
    } catch (error) {
      return null;
    }
  }

  return Object.freeze({ isTailscaleIPv4, normalizeMyCardBenefitsURL });
});
