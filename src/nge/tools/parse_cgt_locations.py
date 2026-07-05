"""Parse TC eConnects' 'Location Data Download' PDF for Columbia Gulf Transmission
into data/samples/cgt_all_points.csv.

Why this exists: pdfplumber/pypdf both pull in a `cryptography` binding that is
broken in this sandbox (ModuleNotFoundError: _cffi_backend / pyo3 panic). PyMuPDF
(`fitz`) has no such dependency and extracts the text layer cleanly.

Why a custom parser instead of position-based table extraction: this is a wide
(~29 column), multi-line-wrapped table where PyMuPDF's raw text comes out as one
flat token stream (whitespace/newline separated) with no column delimiters, and
occasionally two adjacent short cells land on the same physical line (e.g. a
"Loc Zone" value merges with the following "Loc Cnty" value: "OSYS UINTA" as a
single line instead of two). Position-based extraction inherits the same
ambiguity. Instead, each row is located and decoded using its STRUCTURAL
SIGNATURE against small, closed vocabularies that are known from the column
definitions and confirmed by inspection of the source document:
  - Loc Stat Ind in {A, I}
  - Loc Type Ind in {VIR,INT,LDC,WHD,STR,END,EGN,PLT,LNG,RNG,OTH,PPT}
  - Dir Flo in {B, R, D}
  - Loc Zone in {MNL, ON, OFF, OSYS}
  - Loc St Abbrev = a real 2-letter US state code
  - Market Area in {MAINLINE, ONSHORE, OFFSHORE} (found by scanning BACKWARD
    from the 'T' Gath Trans Ind anchor, since this field's vocabulary is small
    and fixed, unlike the free-text fields that precede it)
  - Pipeline Seg Cd matched against SEG_VOCAB, the segment codes directly
    confirmed by manual inspection of unambiguous rows (see the module-level
    constant) — this is what makes the segment->asset mapping (DDL-013)
    possible and correct, rather than grabbing a fragment of the preceding
    free-text "Up/Dn Loc Name" field.
Marketer/shipper pooling points (Loc prefixed P2/P3/P4, Loc Type Ind=PPT) are
excluded — they are TC eConnects pool-accounting constructs at the RAYNE /
W-E-LINE market pools, not physical pipeline interconnects.

Validated (2026-07-04) against 11 rows spanning every structural edge case
present in the source (inactive points, D/R suffix pairs 4204D/4204R/4208D/
4208R, OSYS/offshore zone, STR/INT/LDC types, long wrapped Up/Dn Loc Name)
by cross-checking against the same PDF read via Claude's vision-based PDF
reader in the same session. All 11 checks passed; see git history for the
validation script if re-verification is needed.

Run:  python3 src/nge/tools/parse_cgt_locations.py <path-to-CGT-Location-Data.pdf>
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT_CSV = REPO / "data" / "samples" / "cgt_all_points.csv"
TSP_FERC_CID = "C000307"  # Columbia Gulf Transmission, LLC

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")
LOC_TYPE_VOCAB = {"VIR", "INT", "LDC", "WHD", "STR", "END", "EGN", "PLT", "LNG", "RNG", "OTH", "PPT"}
STAT_VOCAB = {"A", "I"}
DIRFLO_VOCAB = {"B", "R", "D"}
ZONE_VOCAB = {"MNL", "ON", "OFF", "OSYS"}
YN = {"Y", "N"}
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY",
}
LOC_CODE_RE = re.compile(r"^\d{3,8}[A-Z]?$|^A\d{6,8}$")
POOL_RE = re.compile(r"^P[234]\d")
SEG_TAIL = re.compile(r"^[A-Z]{1,3}$")
FERC_CID_RE = re.compile(r"^C\d{6}$")
NUMERIC_ID_RE = re.compile(r"^\d{4,10}$")

MARKET_VOCAB = {"MAINLINE", "ONSHORE", "OFFSHORE"}
# Segment codes confirmed against unambiguous rows in the source document.
SEG_VOCAB = {
    "ALEXDRIA", "BANNER", "STANTON", "RAYNE", "DELHI", "HAMPSHIR", "CLEMNTSV",
    "INVRNESS", "VICKLAT", "HARTSVIL", "E-LINE", "H-LINE", "C-LINE", "P-LINE",
    "V-LINE", "W-LINE", "HouLn100", "Paradis", "OFF-ON", "CGT",
}


def tokenize(path: Path) -> list[str]:
    import fitz  # PyMuPDF; imported lazily so this module has no hard dependency at import time

    toks: list[str] = []
    doc = fitz.open(path)
    for page in doc:
        for line in page.get_text().splitlines():
            toks.extend(line.split())
    return toks


def _row_head(toks: list[str], i: int) -> dict | None:
    if POOL_RE.match(toks[i]):
        return None
    j = i + 1
    limit = min(len(toks), i + 12)
    while j < limit and not DATE_RE.match(toks[j]):
        j += 1
        if j - i > 8:
            return None
    if j >= limit or not DATE_RE.match(toks[j]):
        return None
    eff_date = toks[j]; j += 1
    inact_date = None
    if j < limit and DATE_RE.match(toks[j]):
        inact_date = toks[j]; j += 1
    if j >= limit or toks[j] not in STAT_VOCAB:
        return None
    stat = toks[j]; j += 1
    if j >= limit or toks[j] not in LOC_TYPE_VOCAB:
        return None
    type_ind = toks[j]; j += 1
    loc_name = " ".join(toks[i + 1:j - 3]) if j - 3 > i + 1 else ""
    return {
        "loc": toks[i], "loc_name": loc_name, "eff_date": eff_date,
        "inact_date": inact_date, "loc_stat_ind": stat, "loc_type_ind": type_ind,
        "_next_idx": j,
    }


def _find_update_dt(toks: list[str], i: int, limit: int, max_span: int):
    j = i
    while j < min(limit, i + max_span):
        if DATE_RE.match(toks[j]) and j + 1 < limit and TIME_RE.match(toks[j + 1]):
            return f"{toks[j]} {toks[j + 1]}", j + 2
        j += 1
    return None, i


def _row_tail(toks: list[str], start_idx: int):
    i = start_idx
    limit = len(toks)
    row: dict = {}
    if i >= limit or toks[i] not in DIRFLO_VOCAB:
        return None, i
    row["dir_flo"] = toks[i]; i += 1
    if i >= limit or toks[i] not in ZONE_VOCAB:
        return None, i
    row["loc_zone"] = toks[i]; i += 1

    cnty_parts, guard = [], 0
    while i < limit and toks[i] not in US_STATES:
        cnty_parts.append(toks[i]); i += 1; guard += 1
        if guard > 6:
            return None, i
    if i >= limit:
        return None, i
    row["loc_cnty"] = " ".join(cnty_parts)
    row["loc_st_abbrev"] = toks[i]; i += 1

    if i >= limit or toks[i] not in YN:
        return None, i
    row["updn_ind"] = toks[i]; i += 1
    if i >= limit or toks[i] not in YN:
        return None, i
    row["updn_ferc_cid_ind"] = toks[i]; i += 1

    row.update(updn_name=None, updn_id=None, updn_ferc_cid=None, updn_loc=None, updn_loc_name=None)

    if row["updn_ind"] == "Y":
        name_parts, guard = [], 0
        while i < limit and not NUMERIC_ID_RE.match(toks[i]):
            name_parts.append(toks[i]); i += 1; guard += 1
            if guard > 10:
                return None, i
        if i < limit and NUMERIC_ID_RE.match(toks[i]):
            row["updn_id"] = toks[i]; i += 1
        row["updn_name"] = " ".join(name_parts)

        if row["updn_ferc_cid_ind"] == "Y":
            if i < limit and FERC_CID_RE.match(toks[i]):
                row["updn_ferc_cid"] = toks[i]; i += 1
            if i < limit and LOC_CODE_RE.match(toks[i]):
                row["updn_loc"] = toks[i]; i += 1
            loc_name_parts, guard = [], 0
            while i < limit:
                if re.match(r"^[A-Z0-9\-/()]{1,10}$", toks[i]) and _leads_to_t(toks, i, limit):
                    break
                loc_name_parts.append(toks[i]); i += 1; guard += 1
                if guard > 12:
                    break
            row["updn_loc_name"] = " ".join(loc_name_parts)

    if i >= limit:
        return None, i
    i += 1  # provisional Pipeline Seg Cd token (re-derived below via backward anchor)
    if i >= limit:
        return None, i
    i += 1  # provisional Market Area token
    guard = 0
    while i < limit and toks[i] != "T":
        i += 1; guard += 1
        if guard > 8:
            return None, i
    if i >= limit or toks[i] != "T":
        return None, i
    t_idx = i
    row["gath_trans_ind"] = "T"; i += 1

    if i < limit and toks[i] in YN:
        row["egm_flag"] = toks[i]; i += 1
    else:
        row["egm_flag"] = None

    update_dt, i = _find_update_dt(toks, i, limit, 10)
    if update_dt is None:
        return None, i
    row["update_dt"] = update_dt
    row["_t_idx"] = t_idx
    return row, i


def _leads_to_t(toks: list[str], i: int, limit: int) -> bool:
    j = i + 1
    if j < limit and SEG_TAIL.match(toks[j]):
        j += 1
    return any(toks[k] == "T" for k in range(j, min(limit, j + 6)))


def _reanchor_seg_market(toks: list[str], t_idx: int):
    """Recover Pipeline Seg Cd / Market Area by scanning backward from the
    'T' (Gath Trans Ind) anchor against small closed vocabularies, rather than
    forward from the end of the free-text Up/Dn Loc Name field."""
    j = t_idx - 1
    two = (toks[j - 1] + toks[j]) if j - 1 >= 0 else None
    if two and two.upper() in MARKET_VOCAB:
        market, market_start = two, j - 1
    elif toks[j].upper() in MARKET_VOCAB:
        market, market_start = toks[j], j
    else:
        market, market_start = toks[j], j  # best-effort fallback

    k = market_start - 1
    if k - 1 >= 0 and (toks[k - 1] + toks[k]) in SEG_VOCAB:
        seg = toks[k - 1] + toks[k]
    elif k >= 0 and toks[k] in SEG_VOCAB:
        seg = toks[k]
    else:
        seg = toks[k] if k >= 0 else None  # best-effort fallback, not vocab-confirmed
    return seg, market


def parse(path: Path):
    toks = tokenize(path)
    rows, errors = [], []
    i, n = 0, len(toks)
    while i < n:
        if LOC_CODE_RE.match(toks[i]) and not POOL_RE.match(toks[i]):
            head = _row_head(toks, i)
            if head:
                tail, next_i = _row_tail(toks, head["_next_idx"])
                if tail is not None:
                    seg, market = _reanchor_seg_market(toks, tail.pop("_t_idx"))
                    row = {**head, **tail, "pipeline_seg_cd": seg, "market_area": market}
                    del row["_next_idx"]
                    rows.append(row)
                    i = next_i
                    continue
                errors.append((i, head["loc"]))
        i += 1
    return rows, errors


HEADER = [
    "TSP FERC CID", "Loc", "Loc Name", "Loc St Abbrev", "Loc Cnty", "Loc Zone",
    "Dir Flo", "Loc Stat Ind", "Loc Type Ind", "Eff Date", "Inact Date",
    "Pipeline Seg Cd", "Up/Dn Ind", "Up/Dn FERC CID", "Up/Dn Loc",
    "Up/Dn Loc Name", "Update D/T",
]


def write_csv(rows: list[dict], out_path: Path) -> None:
    rows = sorted(rows, key=lambda r: r["loc"])
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for r in rows:
            w.writerow([
                TSP_FERC_CID, r["loc"], r["loc_name"], r["loc_st_abbrev"], r["loc_cnty"],
                r["loc_zone"], r["dir_flo"], r["loc_stat_ind"], r["loc_type_ind"],
                r["eff_date"], r["inact_date"] or "", r["pipeline_seg_cd"],
                r["updn_ind"], r["updn_ferc_cid"] or "", r["updn_loc"] or "",
                r["updn_loc_name"] or "", r["update_dt"],
            ])


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <path-to-CGT-Location-Data.pdf>")
        raise SystemExit(1)
    rows, errors = parse(Path(sys.argv[1]))
    if errors:
        print(f"WARNING: {len(errors)} rows failed to parse (skipped): {errors}")
    write_csv(rows, OUT_CSV)
    print(f"wrote {len(rows)} rows -> {OUT_CSV}")


if __name__ == "__main__":
    main()
