# Next Session — Phase 3 Live EBB Acquisition (cold-start handoff)

> **Why this file exists:** the previous session ran in a container whose network egress
> policy was fixed at startup and blocked ALL outbound web. The user has since set the
> environment's domain allowlist to "All domains", which applies to **new** sessions.
> This doc tells a fresh session (or a future you) exactly how to continue. Everything
> below was decided and approved — don't re-litigate, execute. Context lives in
> `docs/design-decision-log.md` (esp. DDL-006/007/012) and `docs/data-sources.md`.

## Step 0 — Verify egress actually works now
```bash
curl -sS -o /dev/null -w "%{http_code}\n" --max-time 15 https://example.com   # expect 200
curl -sS "$HTTPS_PROXY/__agentproxy/status"                                   # no connect_rejected entries
```
If still 403 at CONNECT: stop, report to the user — the environment policy didn't apply.

## Step 1 — Land raw bytes FIRST (no parsing in the same pass — user-approved rule)
Fetch these public pages, saving the **raw bytes** to `data/raw/<portal>/<yyyy-mm-dd>/`
with a provenance sidecar per file (`.meta.json`: url, retrieved_at UTC, sha256,
http_status, content_type). Use a browser User-Agent; be polite (sequential, one pass;
these are informational postings meant to be read). Preinstalled Playwright/Chromium is
the fallback if a page needs JS/postbacks.

| Portal | Start URLs |
|---|---|
| Enbridge InfoPost — Egan | `https://infopost.enbridge.com/infopost/EGHome.asp?Pipe=EG` |
| Enbridge InfoPost — Bobcat | `https://infopost.enbridge.com/infopost/BGSHome.asp?Pipe=BGS` |
| Enbridge InfoPost — SESH | `https://infopost.enbridge.com/infopost/SESHHome.asp?Pipe=SESH` |
| gasnom — Sabine | `https://www.gasnom.com/ip/SABINE/` and `https://www.gasnom.com/ip/SABINE/oauc.cfm?type=1` |

From each home page, also land the obvious public subpages (Notices / Critical, Storage
conditions or equivalent, Operationally Available Capacity, Locations/Index of Customers)
— discover the links from the fetched HTML, don't guess URLs.

## Step 2 — Backfill Egan/Bobcat FERC CIDs
Source: FERC CID listing (https://www.ferc.gov/media/ferc-cid-listing) or the TSP header
in each portal's own postings. Then replace the `PENDING-EG` / `PENDING-BGS` placeholder
keys in `src/nge/load.py` (`PIPELINES`) and note it in the Design Decision Log open items.

## Step 3 — Inspect landed bytes, THEN design parsers
Only after real files are in `data/raw/`: read them, document the actual structure in
`docs/data-sources.md` (new format-family entries), design the storage fact schema
(`storage_*` tables in `schema/canonical.sql`), and write parsers with fail-loud
validation. Wire new gold fixtures into the eval harness (`src/nge/extract/eval.py`)
as extraction targets get added.

## Hard boundaries (unchanged — DDL-007/012)
- Public informational postings ONLY. **Anything behind the Enbridge LINK shipper login
  (BP storage balances, scheduled quantities) is confidential: manual export drops only.
  BP credentials must NEVER enter this container.**
- Programmatic LLM extraction additionally requires `ANTHROPIC_API_KEY` in the
  environment; the fetch adapters themselves need no key.

## State as of this handoff (all committed on `claude/gas-scheduler-training-wfuggt`)
- Canonical DuckDB store loads end-to-end: `PYTHONPATH=src python3 -m nge.load`
- Cited impact analysis works: `PYTHONPATH=src python3 -m nge.reach --asset AlexSEG`
- Extraction eval harness + regex baseline (P/R/F1 = 1.0 on AlexSEG gold):
  `PYTHONPATH=src python3 -m nge.extract.eval`
- Test suite: `python3 -m unittest discover -s tests` (14 passing)
