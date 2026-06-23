# Security Policy

## Threat Model

Family Finance is a **single-user, self-hosted** application meant to run on your own
machine. By default it binds to `127.0.0.1` (localhost only) and is **not** designed to be
exposed to the public internet.

Core assumptions:
- The app runs on a machine **you** control.
- `data/finances.json` lives on private/encrypted storage and is never committed (it is
  git-ignored by default).
- If you bind to a non-localhost address (`--host 0.0.0.0`), you control who can reach the
  port (LAN/VPN/firewall). There is **no authentication** — anyone who can reach the port
  can read and write your finances.

## Data privacy

- The server makes **no outbound network calls** that transmit `finances.json`.
- The optional Gemini AI endpoints are **off unless `GEMINI_API_KEY` is set**, are triggered
  only by an explicit user action, and send **only** the uploaded payslip (for extraction) or
  a goal/price *name* — never the contents of `finances.json`.
- The API key is read from the environment and is never logged or written to disk.

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest `main` | ✅ |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report privately via a
[GitHub Security Advisory](https://github.com/ylnhari/family-finance-app/security/advisories/new),
including:
- a description of the issue,
- steps to reproduce,
- potential impact,
- a suggested fix (optional).

You can expect an initial response within 72 hours.

## Known considerations

- **Plaintext data.** `data/finances.json` is human-readable JSON and may contain card
  numbers and other sensitive details. Keep it on encrypted storage and never remove `data/`
  from `.gitignore`.
- **Uploaded files.** Documents you attach are stored as-is under `data/files/`. Filenames are
  sanitized and path-traversal is rejected, but the file contents are not scanned.
- **No multi-user model.** There is a single local user; there is no per-user auth or RBAC.
