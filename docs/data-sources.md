# Data Sources — inventory, formats, and the keys that join them

Grounded in the actual resource files, not assumptions. Three EBB platforms, three
data shapes. "Don't assume formats" is warranted — they genuinely differ.

## Source-system inventory
| Pipeline / storage (short) | FERC CID | EBB platform | Portal |
|---|---|---|---|
| Columbia Gulf (CGT) | C000307 | TC eConnects (TC Energy) | pipeline informational postings — **point catalog landed** (153 points, `data/samples/cgt_all_points.csv`) |
| Southeast Supply Header (SESH) | C001203 | Enbridge InfoPost | infopost.enbridge.com/infopost/SESHHome.asp?Pipe=SESH |
| Sabine Pipe Line | C000830 | gasnom.com (ONEOK) | www.gasnom.com/ip/SABINE/ |
| Egan Hub Storage | **C000086** | Enbridge InfoPost | infopost.enbridge.com/infopost/EGHome.asp?Pipe=EG |
| Bobcat Gas Storage | **C001706** | Enbridge InfoPost | infopost.enbridge.com/infopost/BGSHome.asp?Pipe=BGS |
| Sabine Hub Services, L.L.C. | *unresolved* | *unknown — see gap below* | — |

**Platform win:** Egan and Bobcat post on the *same* Enbridge InfoPost platform as SESH,
so one InfoPost adapter covers three portfolio assets. FERC CIDs confirmed 2026-07-04 from
their real point-location CSVs (landed in `data/raw/{egan,bobcat}/2026-07-04/`).

**Gap — Sabine Hub is not Sabine Pipe Line.** Your original scope named "Sabine Pipeline,
and Sabine Hub" as two things. A web search confirms **Sabine Hub Services, L.L.C.** is a
distinct ONEOK subsidiary that administers physical/administrative gas-volume tracking at
Henry Hub, while Sabine Pipe Line provides the physical wheeling. gasnom's Sabine Pipe Line
portal nav (fetched 2026-07-04) shows no separate Sabine Hub Services section or point
catalog — it may not have its own public EBB, or may be reachable only via a different
URL/portal we haven't found. **Do not assume a FERC CID or format for it; this needs either
your domain knowledge or a further targeted search before modeling it.**

### Egan Hub Storage — confirmed interconnects (from real data, 2026-07-04)
Egan point `45103` ("COLUMBIA - STORAGE") ↔ **CGT** (C000307) loc `4123` ("EGAN
STOR-ACADIA-REC") — **resolved at confidence 1.0** (`resolved_roundtrip`) now that CGT's
own point catalog is landed and both sides declare each other. Also interconnects with ANR
(C000623), TGP (C000020), Texas Gas (C000592), Trunkline (C000251), FGT (C000255), Texas
Eastern (C000094), Kinder Morgan Louisiana (C000231).

### Bobcat Gas Storage — confirmed interconnects (from real data, 2026-07-04)
Texas Eastern (C000094), Transco (C000654), FGT (C000255), Gulf South (C000591), ANR
(C000623). No direct CGT or SESH interconnect found in Bobcat's own point catalog.

### Columbia Gulf (CGT) — point catalog landed (from real data, 2026-07-04)
`CGT Location Data.pdf` (TC eConnects, 48 pages, in the original upload) parsed into
153 physical points — `pdfplumber`/`pypdf` both fail in this sandbox (`cryptography`
binding broken); parsed instead with PyMuPDF (`fitz`) + a custom anchored-vocabulary
parser, `src/nge/tools/parse_cgt_locations.py` (see its docstring for the full method
and validation). Marketer/shipper pooling points (`Loc` prefixed `P2/P3/P4`, `Loc Type
Ind=PPT`) are excluded — TC eConnects accounting constructs at the RAYNE/W-E-LINE
market pools, not physical interconnects.

This is CGT's export doing double duty: besides interconnects, it's the **only**
portfolio pipe whose export carries `Pipeline Seg Cd` — the key that makes
segment→asset mapping possible (see DDL-013 in `docs/design-decision-log.md`). Two new
confidence-1.0 round-trips resulted: CGT `4123` ↔ Egan `45103`, CGT `519` ("SABINE -
HENRY HUB") ↔ Sabine `11202` ("Columbia Gulf - HH"). The SESH interconnect (`4208`)
resolved to a genuine finding instead: SESH's own posting still references CGT's
retired, undifferentiated point `4208`, not the split delivery/receipt pair
`4208D`/`4208R` CGT uses today — surfaced as `resolved_cid_loc` (0.9), not a false 1.0.

The 9 reference links you provided map to: the three portals above (Enbridge InfoPost/Link
for SESH, gasnom for Sabine), **Rextag** (commercial pipeline GIS/maps — *unofficial*,
good for a visual mental model, not a data source of record), and **PCI Energy Solutions**
(vendor how-to on scheduling workflow — process context, not data). None introduce a new
data *shape* beyond the exports below; they matter as acquisition endpoints + orientation.

## Format families (what we must parse)
1. **Notices — structured index + free-text body.** (CGT `Notices.pdf`, `Maintenance Posting.pdf`)
   - Index row: Notice Type, Notice ID, Post/Effective/End times, Critical flag.
   - Body: prose with **embedded quantitative facts** (e.g. AlexSEG "Estimated Capacity
     Setting 2,325,000–2,450,000 Dth; Design 2,683,256; Posted 87–91%").
   - Lifecycle: `Notice Stat Desc` = Initiate / Update / Complete; "REVISED …" supersedes.
   - → structured parse for the index; **LLM-assisted extraction** for the body (see extraction-schema.md).
2. **Point / interconnect reference — CSV.** (`SeshAllPoints.csv`, `Sabine Pipeline Locations.csv`)
   - Each row carries the point AND its counterparty's `Up/Dn FERC CID` + `Up/Dn Loc`.
   - **Different column layouts per platform** → per-source adapters required (risk #7).
3. **Index of Customers — hierarchical CSV.** (`SESH Index of Customers.csv`)
   - Record types `H` (header) / `D` (contract/deal) / `P` (point). Contains BP's FTS
     contracts (e.g. `840245-R1`, 27,000 Dth/d, 2022-11-01→2027-10-31).
4. **Capacity screens — point × cycle tables.** (CGT `Operationally Available Capacity.pdf`,
   `Daily Capacity Posting.pdf`, SESH `OA_MLC` PDFs) — design/operating/scheduled/available by
   point and nomination cycle. *Format not yet parsed; shape stubbed in schema.*
5. **Tariff / rates — regulatory filings.** (`Sabine Tariff.pdf`, `Notice of Rate Change`)
   - FERC docket (e.g. `RP26-898-000`), effective date, FT-1/IT-1 reservation & usage
     rates, FRP/UFRP matrix by rec/del pressure section, **clean vs. marked (redline)** versions.
6. **Maps — geospatial PDFs.** (`SESH Pipeline Map.pdf`, `Sabine Pipeline Map.pdf`) — reference only.
7. **Outage schedules.** (`SESH Outage Schedule.pdf`, `Major Service Outage Overview.pdf`).

## The keys that join everything
- **FERC CID** = universal company key. C001203 SESH · C000307 CGT · C000830 Sabine ·
  C000094 TETLP · C000654 Transco · C000020 TGP · C000021 SNG · C000591 Gulf South · …
- **Point key is composite** `(TSP FERC CID, LOC)` — loc codes are pipeline-scoped and
  collide across pipelines.
- **Interconnects are pre-keyed in the data**: every point declares `Up/Dn FERC CID` +
  `Up/Dn Loc` (+ name), e.g. CGT `4123` ↔ Egan `45103`. This is the graph-edge goldmine —
  but the two sides can go stale independently (SESH `83004` still points at CGT's
  retired `4208` instead of the split `4208D`/`4208R`), so resolution confidence tiers
  (not a bare boolean) are load-bearing.
- **Temporal grid**: Gas Day + nomination cycles Timely (TIM) / Evening / ID1 / ID2 / ID3.
- **Direction matters**: CGT posts "backhaul capacity through AlexSEG" — model a
  direction/backhaul flag or impact analysis will mislead.

## Named CGT constraint assets seen in notices
AlexSEG, BannSEG, StanSEG, HoumaSEG, Corinth, Grayson, Chicot, Alexandria, Banner, New Albany.
Mapped to TC eConnects segment codes (`segment_asset_map`, DDL-013): AlexSEG→`ALEXDRIA`,
BannSEG→`BANNER`, StanSEG→`STANTON` (all confidence 0.9, confirmed by notice text +
geographic clustering); HoumaSEG→`HouLn100` (confidence 0.7, geography-only — Terrebonne
Parish = Houma, LA — not yet notice-confirmed at the exact-point level).

## Acquisition posture (DDL-006 → DDL-012)
v1 = **manual export drops** into `data/raw/` (reliable, ToS-safe). Every landed file
records provenance: source, url, retrieved_at, sha256.

### Acquisition v2 — automated public-EBB fetch (DDL-012) — LIVE via Firecrawl
Direct container egress (curl/WebFetch) to these hosts is blocked by this sandbox's proxy
policy, and that did not change even after the user set the environment's domain allowlist
to "All domains" — the policy binds when the container boots, so it only applies to a
*new* session (see `docs/next-session.md` for that fallback path).

**What actually worked, 2026-07-04:** the **Firecrawl MCP connector** — a hosted scraping
service that fetches from its own infrastructure, not through this container's proxy —
reached every EBB host tried on the first attempt: `infopost.enbridge.com`,
`linkwc.enbridge.com` (raw CSV downloads), and `www.gasnom.com`. Used to land Egan's and
Bobcat's real point-location CSVs (see `data/raw/egan/`, `data/raw/bobcat/`) and confirm
their FERC CIDs. Each page/file fetch costs ~1 Firecrawl credit — a real cost against the
user's account, not free, so bulk historical pulls should be sized deliberately rather
than fetched reflexively.

No MCP connector or skill beyond Firecrawl is required. `docs/next-session.md` remains a
valid, credit-free alternative (open a fresh session so the container's own egress policy
applies) for anyone who wants direct-fetch instead of a hosted scraper.

### Hard boundary — what is NEVER fetched automatically
Public informational postings (critical notices, storage conditions, operationally
available capacity, index of customers) are in scope. **BP's own storage inventory
balances and scheduled quantities live behind the Enbridge LINK shipper login — they are
confidential, and BP credentials must never enter this cloud container** (DDL-007).
Those exports remain **manual drops**: download them yourself while logged in, drop the
files into `data/raw/`, and the loaders treat them with the same provenance as anything
fetched. This is a professional/compliance boundary, not a technical limitation.
