# Maintenance report — 2026-09-27

Added a search control to the Monthly Expenses entry tables. It matches every word case-insensitively across section, category, location and person, filters before pagination, shows visible/total row counts, and gives a clear-search action when a section has no match. Typing retains focus and cursor position; changing the query resets each expense table's “show more” limit. Summary totals and charts continue to use all recorded entries, which the search label states explicitly.

The baseline tables had pagination but no cross-entry search. Search matching is a pure helper, covered by focused tests; the HTTP suite also confirms the helper is served by the local static route.

The server now rejects requests with a missing, malformed, or non-loopback Host, checks Origin and Fetch Metadata before PUT/POST/DELETE, and keeps headerless local clients and Rover's rewritten-Host/stripped-Origin proxy contract working. GET OAuth callbacks remain navigational. Rejected request bodies are drained only within a 0.5-second total deadline. The Windows test runner now stops on the first failing stage and exits without `pause`; `call` handles the local Python batch shim.

Verification on 2026-09-27: `cmd /c test.bat` — 237 Python tests and 54 Node tests passed. The Python tests used temporary data and mocked provider boundaries; optional investment extras were absent. No finance browser-rendering harness is available, so layout and keyboard behavior still need a manual browser check.
