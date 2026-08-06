# MyCard Benefits companion

MyCard Benefits is an optional, separate local application for researching card
benefits. Family Finance works exactly the same without it.

## One-time local setup

1. Install and start your local MyCard Benefits clone by following that project's
   README. It binds to `127.0.0.1`; use the port resolved by that application.
2. In Family Finance, open **Cards** and choose **Companion setup**.
3. For a local browser, enter the loopback base URL, for example
   `http://127.0.0.1:8777`. For a remote client, enter your own configured Rover
   authenticated-proxy `proxy_url`. If Rover shows an HTTP URL, use its literal
   Tailscale IPv4 address. Then choose **MyCard Benefits companion**.

The launcher accepts only a bare base URL. `http` is allowed on loopback
(`127.0.0.1`, `localhost`, or `[::1]`) and on literal Tailscale IPv4 addresses
in `100.64.0.0/10`, which is how Rover's tailnet proxy is normally reached.
Every other remote destination must use `https`. Usernames, passwords, paths,
queries, and fragments are rejected; an accepted base is normalized to its
origin. Trust only your own loopback URL, Tailscale Rover proxy, or authenticated
HTTPS gateway; never expose the companion directly to a network. Its setting is
stored only in this browser's local storage, not in Family Finance data or
backups.

## Privacy and failure behavior

Family Finance does not start, embed, synchronize with, or import into MyCard
Benefits. The launcher sends no card name, number, expiry, CVV, PIN, owner,
benefit, document, or finance data. A click first opens a blank handoff tab so
the browser can honor the user gesture, then makes a cookie-free, no-referrer
reachability check before navigating that tab. This is a reachability check
only: destination identity is not yet verified, and signed identity pinning
remains a later security gate. If no URL is configured or the companion is
stopped, this setup page opens instead. A rejected URL stays in the setup dialog
with an error and is not saved.

Configure the companion only when you want to use it. Clearing the setup field
removes the browser-local URL and has no effect on either application's data.
