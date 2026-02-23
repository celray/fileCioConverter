#!/usr/bin/env python3
"""
Convert old-format SWAT+ file.cio to the new redesigned format.

Usage:
    python convertCIO.py [path]

    path  — a TxtInOut directory containing file.cio, OR a file.cio path directly.
            If omitted, uses the current directory.

The tool will:
    1. Find file.cio in the given directory (or use the path directly)
    2. Check if it's already in the new format — if so, skip
    3. Back up the original as file.cio.old.format (won't overwrite an existing backup)
    4. Convert in place and rename dash-filenames on disk
    5. Scan for formerly-hardcoded files and add them to the appropriate sections
"""

import sys
import os
import shutil

# ---------- filename dash-to-underscore rename ----------

KNOWN_RENAMES = {
    "weather-sta.cli": "weather_sta.cli",
    "weather-wgn.cli": "weather_wgn.cli",
    "hru-lte.con":     "hru_lte.con",
    "channel-lte.cha": "channel_lte.cha",
    "hyd-sed-lte.cha": "hyd_sed_lte.cha",
    "hru-data.hru":    "hru_data.hru",
    "hru-lte.hru":     "hru_lte.hru",
    "chan-surf.lin":    "chan_surf.lin",
    # NOTE: water_allocation.wro renamed to water_allocation.wal for consistency
    # with the water_allocation section. The .wal extension matches the other
    # files in that section. Final naming TBD — may change after review.
    "water_allocation.wro": "water_allocation.wal",
}

# ---------- formerly-hardcoded files to scan for on disk ----------
# These files were hardcoded in the old Fortran source (not listed in old file.cio).
# The new format lists them explicitly. During conversion, we scan the data directory
# for these files and add them to the appropriate section/position.
#
# Format: (section_name, 0-based_position, old_filename_on_disk)
# Only applied when the position is currently "null".

HARDCODED_SCAN = [
    # -- Slots added to existing sections --
    # channel: positions 8-9 are new (sed_nut.cha, element.ccu)
    ("channel",    8, "sed_nut.cha"),
    ("channel",    9, "element.ccu"),
    # recall: positions 1-2 are new (salt_recall.rec, cs_recall.rec)
    ("recall",     1, "salt_recall.rec"),
    ("recall",     2, "cs_recall.rec"),
    # structural: position 5 is new (satbuffer.str)
    ("structural", 5, "satbuffer.str"),
    # hru_parm_db: position 4 is new (pest_metabolite.pes) — inserted during transform
    ("hru_parm_db", 4, "pest_metabolite.pes"),
    # ops: positions 6-7 are new (puddle.ops, transplant.plt)
    ("ops",        6, "puddle.ops"),
    ("ops",        7, "transplant.plt"),
    # climate: positions 9-10 are new (salt_atmo.cli, cs_atmo.cli)
    ("climate",    9, "salt_atmo.cli"),
    ("climate",   10, "cs_atmo.cli"),

    # -- Entirely new sections --
    # carbon (3 tokens)
    ("carbon",  0, "basins_carbon.tes"),
    ("carbon",  1, "carb_coefs.cbn"),
    ("carbon",  2, "co2_yr.dat"),
    # salt (10 tokens)
    ("salt",    0, "salt_aqu.ini"),
    ("salt",    1, "salt_channel.ini"),
    ("salt",    2, "salt_hru.ini"),
    ("salt",    3, "salt_fertilizer.frt"),
    ("salt",    4, "salt_irrigation"),
    ("salt",    5, "salt_plants"),
    ("salt",    6, "salt_road"),
    ("salt",    7, "salt_urban"),
    ("salt",    8, "salt_uptake"),
    ("salt",    9, "salt_res"),
    # constituents (17 tokens — position 0 is cs_db, handled separately from old sim line)
    ("constituents",  0, "constituents.cs"),
    ("constituents",  2, "cs_channel.ini"),
    ("constituents",  3, "cs_hru.ini"),
    ("constituents",  4, "fertilizer.frt_cs"),
    ("constituents",  5, "cs_irrigation"),
    ("constituents",  6, "cs_plants_boron"),
    ("constituents",  7, "cs_reactions"),
    ("constituents",  8, "cs_uptake"),
    ("constituents",  9, "cs_urban"),
    ("constituents", 10, "cs_res"),
    ("constituents", 12, "cs_aqu.ini"),
    ("constituents", 13, "initial.cha_cs"),
    ("constituents", 14, "reservoir.res_cs"),
    ("constituents", 15, "wetland.wet_cs"),
    ("constituents", 16, "nutrients.rte"),
    # manure (2 tokens)
    ("manure",  0, "manure.frt"),
    ("manure",  1, "manure_allo.mnu"),
    # water_allocation: positions 1-6 (position 0 is wro, handled separately)
    ("water_allocation", 1, "water_pipe.wal"),
    ("water_allocation", 2, "water_tower.wal"),
    ("water_allocation", 3, "water_use.wal"),
    ("water_allocation", 4, "water_treat.wal"),
    ("water_allocation", 5, "om_treat.wal"),
    ("water_allocation", 6, "om_use.wal"),
    # update (1 token)
    ("update",  0, "scen_dtl.upd"),
]


def rename_file(name):
    """Rename a filename: apply known renames, else replace dashes with underscores."""
    if name == "null" or name == "":
        return name
    if name in KNOWN_RENAMES:
        return KNOWN_RENAMES[name]
    # General rule: replace dashes with underscores in filenames
    return name.replace("-", "_")


# ---------- section token counts for the NEW format ----------
# Each section has a fixed token count (excluding the section keyword).
# Tokens beyond this count are silently dropped; missing tokens filled with "null".

NEW_SECTION_TOKENS = {
    "simulation":       4,
    "basin":            2,
    "climate":         11,
    "connect":         13,
    "channel":         10,
    "reservoir":        8,
    "routing_unit":     4,
    "hru":              2,
    "exco":             6,
    "recall":           3,
    "dr":               6,
    "aquifer":          3,
    "herd":             3,
    "link":             2,
    "hydrology":        3,
    "structural":       6,
    "hru_parm_db":     11,
    "ops":              8,
    "lum":              5,
    "calibration":      9,
    "init":            11,
    "soils":            3,
    "decision_table":   4,
    "regions":         17,
    "carbon":           3,
    "salt":            10,
    "constituents":    17,
    "manure":           2,
    "water_allocation": 7,
    "update":           1,
    "io_path":          7,
}


def pad_tokens(tokens, count):
    """Pad token list to exactly 'count' entries using 'null'."""
    result = list(tokens[:count])
    while len(result) < count:
        result.append("null")
    return result


# ---------- parse old file.cio ----------

def parse_old_cio(filepath):
    """
    Parse old file.cio into a list of (section_name, [tokens]).
    Returns: (header_line, [(section_name, [tokens]), ...])
    """
    with open(filepath, "r") as f:
        lines = f.readlines()

    if not lines:
        print("Error: empty file", file=sys.stderr)
        sys.exit(1)

    header = lines[0].rstrip("\n")
    sections = []

    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if not parts:
            continue
        section = parts[0].lower()
        tokens = parts[1:] if len(parts) > 1 else []
        sections.append((section, tokens))

    return header, sections


def is_new_format(sections):
    """
    Detect if the file is already in the new redesigned format.
    Key markers: 'io_path' section exists (old format uses separate pcp_path, tmp_path, etc.)
    """
    section_names = {name for name, _ in sections}
    # io_path is unique to the new format — old format has pcp_path, tmp_path, etc.
    if "io_path" in section_names:
        return True
    # Also check: new format has 'carbon'/'salt'/'constituents' sections AND 'calibration' (not 'chg')
    new_markers = {"carbon", "salt", "constituents", "manure", "water_allocation", "update"}
    if new_markers.issubset(section_names) and "calibration" in section_names and "chg" not in section_names:
        return True
    return False


# ---------- transform ----------

def transform(header, sections):
    """
    Transform old sections into new format.
    Returns: new header, new sections list.
    """
    # Build a dict for quick lookup (some sections may appear once)
    sec_dict = {}
    for name, tokens in sections:
        sec_dict[name] = tokens

    # Collect path values from old format
    paths = {
        "pcp": sec_dict.get("pcp_path", ["null"])[0] if "pcp_path" in sec_dict else "null",
        "tmp": sec_dict.get("tmp_path", ["null"])[0] if "tmp_path" in sec_dict else "null",
        "slr": sec_dict.get("slr_path", ["null"])[0] if "slr_path" in sec_dict else "null",
        "hmd": sec_dict.get("hmd_path", ["null"])[0] if "hmd_path" in sec_dict else "null",
        "wnd": sec_dict.get("wnd_path", ["null"])[0] if "wnd_path" in sec_dict else "null",
    }

    # Extract cs_db from old simulation line (5th token, if present)
    old_sim = sec_dict.get("simulation", [])
    cs_db_from_sim = old_sim[4] if len(old_sim) >= 5 else "null"

    # Extract water_allocation.wro from old water_rights section
    old_wr = sec_dict.get("water_rights", [])
    wro_file = old_wr[0] if old_wr else "null"

    # Build new sections in canonical order
    new_sections = []

    # --- simulation (4 tokens: time.sim, print.prt, object.prt, object.cnt) ---
    sim = pad_tokens(sec_dict.get("simulation", []), 4)
    # Old format had: time.sim, print.prt, object.prt, object.cnt, [cs_db]
    # New: drop cs_db (moved to constituents)
    new_sections.append(("simulation", [rename_file(t) for t in sim]))

    # --- basin ---
    new_sections.append(("basin", [rename_file(t) for t in pad_tokens(sec_dict.get("basin", []), 2)]))

    # --- climate (11 tokens: +atmosalt.cli, +atmocs.cli at end) ---
    old_cli = sec_dict.get("climate", [])
    cli = pad_tokens(old_cli, 9)  # old had 9 slots
    cli = [rename_file(t) for t in cli]
    cli.extend(["null", "null"])  # atmosalt.cli, atmocs.cli
    new_sections.append(("climate", cli[:11]))

    # --- connect (13 tokens) ---
    old_con = sec_dict.get("connect", [])
    con = [rename_file(t) for t in pad_tokens(old_con, 13)]
    new_sections.append(("connect", con))

    # --- channel (10 tokens: old 8 + sed_nut.cha + element.ccu) ---
    old_ch = sec_dict.get("channel", [])
    ch = [rename_file(t) for t in pad_tokens(old_ch, 8)]
    ch.extend(["null", "null"])  # sed_nut.cha, element.ccu
    new_sections.append(("channel", ch[:10]))

    # --- reservoir (8 tokens, same) ---
    old_res = sec_dict.get("reservoir", [])
    new_sections.append(("reservoir", [rename_file(t) for t in pad_tokens(old_res, 8)]))

    # --- routing_unit (4 tokens: old 3 + rout_unit.dr) ---
    old_ru = sec_dict.get("routing_unit", [])
    ru = [rename_file(t) for t in pad_tokens(old_ru, 3)]
    ru.append("null")  # rout_unit.dr
    new_sections.append(("routing_unit", ru[:4]))

    # --- hru (2 tokens) ---
    old_hru = sec_dict.get("hru", [])
    new_sections.append(("hru", [rename_file(t) for t in pad_tokens(old_hru, 2)]))

    # --- exco (6 tokens) ---
    old_exco = sec_dict.get("exco", [])
    new_sections.append(("exco", [rename_file(t) for t in pad_tokens(old_exco, 6)]))

    # --- recall (3 tokens: old 1 + recall.slt + recall.cs) ---
    old_rec = sec_dict.get("recall", [])
    rec = [rename_file(t) for t in pad_tokens(old_rec, 1)]
    rec.extend(["null", "null"])  # recall.slt, recall.cs
    new_sections.append(("recall", rec[:3]))

    # --- dr (6 tokens) ---
    old_dr = sec_dict.get("dr", [])
    new_sections.append(("dr", [rename_file(t) for t in pad_tokens(old_dr, 6)]))

    # --- aquifer (3 tokens: old 2 + gwflow.aqu) ---
    old_aqu = sec_dict.get("aquifer", [])
    aqu = [rename_file(t) for t in pad_tokens(old_aqu, 2)]
    aqu.append("null")  # gwflow.aqu
    new_sections.append(("aquifer", aqu[:3]))

    # --- herd (3 tokens) ---
    old_herd = sec_dict.get("herd", [])
    new_sections.append(("herd", [rename_file(t) for t in pad_tokens(old_herd, 3)]))

    # --- link (2 tokens) ---
    old_link = sec_dict.get("link", [])
    new_sections.append(("link", [rename_file(t) for t in pad_tokens(old_link, 2)]))

    # --- hydrology (3 tokens) ---
    old_hyd = sec_dict.get("hydrology", [])
    new_sections.append(("hydrology", [rename_file(t) for t in pad_tokens(old_hyd, 3)]))

    # --- structural (6 tokens: old 5 + satbuffer.str) ---
    old_str = sec_dict.get("structural", [])
    st = [rename_file(t) for t in pad_tokens(old_str, 5)]
    st.append("null")  # satbuffer.str
    new_sections.append(("structural", st[:6]))

    # --- hru_parm_db (11 tokens: old 10, insert metabolite.pes at position 5) ---
    old_pdb = sec_dict.get("hru_parm_db", [])
    pdb = [rename_file(t) for t in pad_tokens(old_pdb, 10)]
    # Old: plants fert till pest [path hmet salt] urban septic snow (10 tokens)
    # New: plants fert till pest metabolite path hmet salt urban septic snow (11 tokens)
    # Insert metabolite.pes at position 4 (after pesticide.pes)
    pdb.insert(4, "null")  # metabolite.pes
    new_sections.append(("hru_parm_db", pdb[:11]))

    # --- ops (8 tokens: old 6 + puddle.ops + transplant.ops) ---
    old_ops = sec_dict.get("ops", [])
    ops = [rename_file(t) for t in pad_tokens(old_ops, 6)]
    ops.extend(["null", "null"])  # puddle.ops, transplant.ops
    new_sections.append(("ops", ops[:8]))

    # --- lum (5 tokens) ---
    old_lum = sec_dict.get("lum", [])
    new_sections.append(("lum", [rename_file(t) for t in pad_tokens(old_lum, 5)]))

    # --- calibration (was 'chg', 9 tokens) ---
    old_chg = sec_dict.get("chg", sec_dict.get("calibration", []))
    new_sections.append(("calibration", [rename_file(t) for t in pad_tokens(old_chg, 9)]))

    # --- init (11 tokens) ---
    old_init = sec_dict.get("init", [])
    new_sections.append(("init", [rename_file(t) for t in pad_tokens(old_init, 11)]))

    # --- soils (3 tokens) ---
    old_soils = sec_dict.get("soils", [])
    new_sections.append(("soils", [rename_file(t) for t in pad_tokens(old_soils, 3)]))

    # --- decision_table (4 tokens) ---
    old_dt = sec_dict.get("decision_table", [])
    new_sections.append(("decision_table", [rename_file(t) for t in pad_tokens(old_dt, 4)]))

    # --- regions (17 tokens) ---
    old_reg = sec_dict.get("regions", [])
    new_sections.append(("regions", [rename_file(t) for t in pad_tokens(old_reg, 17)]))

    # --- NEW SECTIONS (all null if not present in old format) ---

    # --- carbon (3 tokens) ---
    new_sections.append(("carbon", pad_tokens(sec_dict.get("carbon", []), 3)))

    # --- salt (10 tokens) ---
    new_sections.append(("salt", pad_tokens(sec_dict.get("salt", []), 10)))

    # --- constituents (17 tokens: first is cs_db from old simulation line) ---
    old_constit = sec_dict.get("constituents", [])
    if old_constit:
        constit = pad_tokens(old_constit, 17)
    else:
        constit = [rename_file(cs_db_from_sim)] + ["null"] * 16
    new_sections.append(("constituents", constit))

    # --- manure (2 tokens) ---
    new_sections.append(("manure", pad_tokens(sec_dict.get("manure", []), 2)))

    # --- water_allocation (7 tokens: first is wro from old water_rights) ---
    old_wal = sec_dict.get("water_allocation", [])
    if old_wal:
        wal = pad_tokens(old_wal, 7)
    else:
        wal = [rename_file(wro_file)] + ["null"] * 6
    new_sections.append(("water_allocation", wal))

    # --- update (1 token) ---
    new_sections.append(("update", pad_tokens(sec_dict.get("update", []), 1)))

    # --- io_path (7 tokens: pcp tmp slr hmd wnd pet out) ---
    io_path_tokens = [
        paths["pcp"], paths["tmp"], paths["slr"],
        paths["hmd"], paths["wnd"],
        "null",  # pet_path (new)
        "null",  # out_path (new)
    ]
    new_sections.append(("io_path", io_path_tokens))

    # Build new header
    new_header = "file.cio: written by SWAT+ rev.62.0.0 - Redesigned with complete file references"

    return new_header, new_sections


# ---------- write new file.cio ----------

COL_WIDTH = 18  # column width for alignment


def format_line(section_name, tokens):
    """Format a section line with aligned columns."""
    parts = [f"{section_name:<{COL_WIDTH}}"]
    for tok in tokens:
        parts.append(f"{tok:<{COL_WIDTH}}")
    return "".join(parts).rstrip()


def write_new_cio(filepath, header, sections):
    """Write the new file.cio."""
    lines = [header]
    for name, tokens in sections:
        lines.append(format_line(name, tokens))
    text = "\n".join(lines) + "\n"

    if filepath == "-":
        sys.stdout.write(text)
    else:
        with open(filepath, "w") as f:
            f.write(text)
        print(f"Converted file written to: {filepath}", file=sys.stderr)


# ---------- scan directory for formerly-hardcoded files ----------

def scan_hardcoded_files(sections, data_dir):
    """
    Post-process new sections: for each slot that is "null", check if the
    corresponding old hardcoded file exists on disk. If found, use it.
    Modifies sections list in place.
    """
    if not data_dir or not os.path.isdir(data_dir):
        return

    # Build dict for quick section lookup: name -> index in sections list
    sec_idx = {}
    for i, (name, _tokens) in enumerate(sections):
        sec_idx[name] = i

    found = []
    for section_name, pos, old_filename in HARDCODED_SCAN:
        if section_name not in sec_idx:
            continue
        idx = sec_idx[section_name]
        tokens = sections[idx][1]
        if pos >= len(tokens):
            continue
        # Only fill "null" slots — don't overwrite values from old file.cio
        if tokens[pos] != "null":
            continue
        disk_path = os.path.join(data_dir, old_filename)
        if os.path.exists(disk_path):
            tokens[pos] = old_filename
            found.append((section_name, pos, old_filename))

    return found


# ---------- also rename files on disk ----------

def rename_files_on_disk(directory):
    """Rename files in a directory to match the new naming convention."""
    renamed = []
    for old_name, new_name in KNOWN_RENAMES.items():
        old_path = os.path.join(directory, old_name)
        new_path = os.path.join(directory, new_name)
        if os.path.exists(old_path) and not os.path.exists(new_path):
            os.rename(old_path, new_path)
            renamed.append((old_name, new_name))
    return renamed


# ---------- main ----------

BACKUP_NAME = "file.cio.old.format"


def resolve_cio_path(arg):
    """
    Resolve a user argument to a file.cio path.
    Accepts: a directory (looks for file.cio inside), or a direct file path.
    """
    if os.path.isdir(arg):
        candidate = os.path.join(arg, "file.cio")
        if os.path.exists(candidate):
            return candidate
        print(f"Error: no file.cio found in {arg}", file=sys.stderr)
        sys.exit(1)
    elif os.path.isfile(arg):
        return arg
    else:
        print(f"Error: {arg} not found", file=sys.stderr)
        sys.exit(1)


def main():
    # Default to current directory if no argument given
    if len(sys.argv) < 2:
        arg = "."
    else:
        arg = sys.argv[1]

    cio_path = resolve_cio_path(arg)
    data_dir = os.path.dirname(os.path.abspath(cio_path))

    print(f"Reading: {cio_path}", file=sys.stderr)

    header, sections = parse_old_cio(cio_path)

    # Detect if already in new format — refuse to double-convert
    if is_new_format(sections):
        print("file.cio is already in the current format. No conversion needed.", file=sys.stderr)
        sys.exit(0)

    # Back up the original — never overwrite an existing backup
    backup_path = os.path.join(data_dir, BACKUP_NAME)
    if os.path.exists(backup_path):
        print(f"Backup already exists: {backup_path} (not overwriting)", file=sys.stderr)
    else:
        shutil.copy2(cio_path, backup_path)
        print(f"Original backed up to: {backup_path}", file=sys.stderr)

    new_header, new_sections = transform(header, sections)

    # Scan data directory for formerly-hardcoded files
    found = scan_hardcoded_files(new_sections, data_dir)
    if found:
        print("\nDetected formerly-hardcoded files on disk:", file=sys.stderr)
        for sec, pos, fname in found:
            print(f"  {sec}[{pos}] <- {fname}", file=sys.stderr)

    # Write converted file in place
    write_new_cio(cio_path, new_header, new_sections)

    # Rename dash-filenames on disk
    renamed = rename_files_on_disk(data_dir)
    if renamed:
        print("\nRenamed files on disk:", file=sys.stderr)
        for old, new in renamed:
            print(f"  {old} -> {new}", file=sys.stderr)

    print("\nConversion complete.", file=sys.stderr)


if __name__ == "__main__":
    main()
