# Agent Ledger MCP

`mcp_ledger_server.py` is the local stdio MCP interface for the Lending & Borrowing page. It is intentionally separate from the web server: it opens no network listener and does not expose the complete `finances.json` document.

## Start it

Run read-only by default:

```text
python mcp_ledger_server.py
```

Enable changes only for an explicitly authorized local session:

```text
python mcp_ledger_server.py --write-enabled
```

It uses the app's normal local data directory unless `--data-dir` is supplied. A read-only session never initializes a missing store; it returns a clear initialization error instead. The write-enabled mode may initialize the normal blank local store. Writes share the app's cross-process lock, automatic daily backup policy, and atomic JSON writes, so they can safely coexist with the browser app.

The browser's normal whole-document save now also carries a document revision. If an MCP change happens after a browser tab loaded, that tab receives a conflict instead of silently replacing the newer ledger; reload the tab before saving again.

For Codex, configure a local stdio server using the path to this checkout:

```toml
[mcp_servers.family_finance_ledger]
command = "python"
args = ["<path-to-checkout>/mcp_ledger_server.py", "--write-enabled"]
```

Remove `--write-enabled` to leave the integration read-only.

## Tools and safety contract

- `lending_list_ledgers` returns only each ledger's id, person, record count, and opaque revision.
- `lending_list_records` returns at most 100 records from one ledger. Notes are excluded unless `include_notes: true` is explicit.
- `lending_create_ledger` and `lending_create_record` require a UUID `idempotency_key`. Retrying the same create cannot duplicate it.
- `lending_update_record` requires the revision returned by a fresh read. A changed ledger is rejected rather than silently overwritten.
- There is no delete tool. Void an incorrect record with `lending_update_record` and `{"cancelled": true}` so the local history remains recoverable.

Agents must use these narrow tools for lending work. They must not read or edit the finance JSON file directly, and should only request notes when they are needed for the user's current instruction.
