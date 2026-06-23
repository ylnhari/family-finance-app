# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and the project
uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added
- **Lending & borrowing ledger** — track money lent to / borrowed from each person, with
  fixed or percentage cashback, extra charges, cancellations, and a per-year breakdown.
- **"Hide values"** privacy toggle to mask all amounts on screen.
- Cross-process file lock around data writes — no two processes/instances can edit
  `finances.json` at once.
- Single-instance launch: re-running on the same data opens the running instance instead of
  starting a second writer (keyed on app id + data dir, so `--demo`/`--data-dir` still start
  separately).
- GitHub Actions CI running the full suite on Linux and Windows (Python 3.10 & 3.12),
  exercising both file-lock backends (`fcntl` and `msvcrt`).
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `.gitattributes`, issue/PR templates.

### Changed
- **Port handling is now deterministic**: the port comes from `ports.json` if present,
  otherwise from `--port`; the server no longer hunts for a free port and **fails clearly if
  the chosen port is already in use**.
- `--host 0.0.0.0` now prints an explicit warning that it exposes `finances.json` on the
  network (there is no authentication).

### Fixed
- **`loanState()` multi-prepayment bug** — two prepayments in the same month over-deducted the
  balance (it subtracted the cumulative prepay each iteration instead of the individual lump
  sum), skewing remaining months and total interest.
- Per-year ledger transaction count no longer includes cancelled rows (they are tracked
  separately).

[Unreleased]: https://github.com/ylnhari/family-finance-app/commits/main
