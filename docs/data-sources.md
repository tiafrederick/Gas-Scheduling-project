# Data Sources — inventory, formats, and the keys that join them

Grounded in the actual resource files, not assumptions. Three EBB platforms, three
data shapes. "Don't assume formats" is warranted — they genuinely differ.

## Source-system inventory
| Pipeline (short) | FERC CID | EBB platform | Portal |
|---|---|---|---|
| Columbia Gulf (CGT) | C000307 | TC eConnects (TC Energy) | pipeline informational postings |
| Southeast Supply Header (SESH) | C001203 | Enbridge InfoPost | infopost.enbridge.com / link.enbridge.com |
| Sabine Pipe Line / Sabine Hub | C000830 | gasnom.com (ONEOK) | gasnom.com/ip/sabine |

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
  `Up/Dn Loc` (+ name). e.g. SESH `83004` ↔ CGT `4208`. This is the graph-edge goldmine.
- **Temporal grid**: Gas Day + nomination cycles Timely (TIM) / Evening / ID1 / ID2 / ID3.
- **Direction matters**: CGT posts "backhaul capacity through AlexSEG" — model a
  direction/backhaul flag or impact analysis will mislead.

## Named CGT constraint assets seen in notices
AlexSEG, BannSEG, StanSEG, HoumaSEG, Corinth, Grayson, Chicot, Alexandria, Banner, New Albany.

## Acquisition posture (DDL-006)
v1 = **manual export drops** into `data/raw/` (reliable, ToS-safe). Adapter interfaces
for TC eConnects / InfoPost / gasnom are designed but automation stays OFF until the model
is proven. Every landed file records provenance: source, url, retrieved_at, sha256.
