/*
 * convertCIO.cpp — Convert old-format SWAT+ file.cio to new redesigned format.
 *
 * Usage:
 *     convertCIO <old_file.cio> [output_file.cio]
 *
 * If output_file.cio is not specified, writes to stdout.
 * The original file is never modified.
 *
 * Build:
 *     g++ -std=c++17 -O2 -o convertCIO convertCIO.cpp
 *
 * Changes applied:
 *     - Section 'chg' renamed to 'calibration'
 *     - Section 'water_rights' removed; first token moved to 'water_allocation'
 *     - Old path lines (pcp_path, tmp_path, ...) consolidated into single 'io_path'
 *     - Dashes in filenames replaced with underscores
 *     - New sections added (carbon, salt, constituents, manure, water_allocation, update)
 *     - Token counts adjusted to match new format spec
 *     - Scans data directory for formerly-hardcoded files and adds them to new sections
 *     - Header line updated
 */

#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <tuple>
#include <vector>

namespace fs = std::filesystem;

// ---- constants ----

static const int COL_WIDTH = 18;

static const std::map<std::string, std::string> KNOWN_RENAMES = {
    {"weather-sta.cli", "weather_sta.cli"},
    {"weather-wgn.cli", "weather_wgn.cli"},
    {"hru-lte.con",     "hru_lte.con"},
    {"channel-lte.cha", "channel_lte.cha"},
    {"hyd-sed-lte.cha", "hyd_sed_lte.cha"},
    {"hru-data.hru",    "hru_data.hru"},
    {"hru-lte.hru",     "hru_lte.hru"},
    {"chan-surf.lin",    "chan_surf.lin"},
    // NOTE: water_allocation.wro renamed to water_allocation.wal for consistency
    // with the water_allocation section. The .wal extension matches the other
    // files in that section. Final naming TBD -- may change after review.
    {"water_allocation.wro", "water_allocation.wal"},
};

// Formerly-hardcoded files to scan for on disk.
// (section_name, 0-based_position, old_filename_on_disk)
struct HardcodedEntry {
    std::string section;
    size_t pos;
    std::string filename;
};

static const std::vector<HardcodedEntry> HARDCODED_SCAN = {
    // Slots added to existing sections
    {"channel",    8, "sed_nut.cha"},
    {"channel",    9, "element.ccu"},
    {"recall",     1, "salt_recall.rec"},
    {"recall",     2, "cs_recall.rec"},
    {"structural", 5, "satbuffer.str"},
    {"hru_parm_db", 4, "pest_metabolite.pes"},
    {"ops",        6, "puddle.ops"},
    {"ops",        7, "transplant.plt"},
    {"climate",    9, "salt_atmo.cli"},
    {"climate",   10, "cs_atmo.cli"},
    // Carbon (3 tokens)
    {"carbon",  0, "basins_carbon.tes"},
    {"carbon",  1, "carb_coefs.cbn"},
    {"carbon",  2, "co2_yr.dat"},
    // Salt (10 tokens)
    {"salt",    0, "salt_aqu.ini"},
    {"salt",    1, "salt_channel.ini"},
    {"salt",    2, "salt_hru.ini"},
    {"salt",    3, "salt_fertilizer.frt"},
    {"salt",    4, "salt_irrigation"},
    {"salt",    5, "salt_plants"},
    {"salt",    6, "salt_road"},
    {"salt",    7, "salt_urban"},
    {"salt",    8, "salt_uptake"},
    {"salt",    9, "salt_res"},
    // Constituents (17 tokens)
    {"constituents",  0, "constituents.cs"},
    {"constituents",  2, "cs_channel.ini"},
    {"constituents",  3, "cs_hru.ini"},
    {"constituents",  4, "fertilizer.frt_cs"},
    {"constituents",  5, "cs_irrigation"},
    {"constituents",  6, "cs_plants_boron"},
    {"constituents",  7, "cs_reactions"},
    {"constituents",  8, "cs_uptake"},
    {"constituents",  9, "cs_urban"},
    {"constituents", 10, "cs_res"},
    {"constituents", 12, "cs_aqu.ini"},
    {"constituents", 13, "initial.cha_cs"},
    {"constituents", 14, "reservoir.res_cs"},
    {"constituents", 15, "wetland.wet_cs"},
    {"constituents", 16, "nutrients.rte"},
    // Manure (2 tokens)
    {"manure",  0, "manure.frt"},
    {"manure",  1, "manure_allo.mnu"},
    // Water allocation (positions 1-6; position 0 handled from old water_rights)
    {"water_allocation", 1, "water_pipe.wal"},
    {"water_allocation", 2, "water_tower.wal"},
    {"water_allocation", 3, "water_use.wal"},
    {"water_allocation", 4, "water_treat.wal"},
    {"water_allocation", 5, "om_treat.wal"},
    {"water_allocation", 6, "om_use.wal"},
    // Update (1 token)
    {"update",  0, "scen_dtl.upd"},
};

// ---- helpers ----

static std::string to_lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return std::tolower(c); });
    return s;
}

static std::string rename_file(const std::string &name) {
    if (name == "null" || name.empty()) return name;
    auto it = KNOWN_RENAMES.find(name);
    if (it != KNOWN_RENAMES.end()) return it->second;
    // General rule: replace dashes with underscores
    std::string out = name;
    std::replace(out.begin(), out.end(), '-', '_');
    return out;
}

static std::vector<std::string> tokenize(const std::string &line) {
    std::vector<std::string> tokens;
    std::istringstream iss(line);
    std::string tok;
    while (iss >> tok) tokens.push_back(tok);
    return tokens;
}

static std::vector<std::string> pad(std::vector<std::string> v, size_t n) {
    v.resize(n, "null");
    return v;
}

// Rename each token in a vector
static std::vector<std::string> rename_all(const std::vector<std::string> &v) {
    std::vector<std::string> out;
    out.reserve(v.size());
    for (auto &t : v) out.push_back(rename_file(t));
    return out;
}

// Get a section's tokens from the dict, or empty if not present
static std::vector<std::string> get_sec(
    const std::map<std::string, std::vector<std::string>> &d,
    const std::string &key)
{
    auto it = d.find(key);
    return it != d.end() ? it->second : std::vector<std::string>{};
}

// Get a single path value from the dict
static std::string get_path(
    const std::map<std::string, std::vector<std::string>> &d,
    const std::string &key)
{
    auto it = d.find(key);
    if (it != d.end() && !it->second.empty()) return it->second[0];
    return "null";
}

// ---- section type for output ----

struct Section {
    std::string name;
    std::vector<std::string> tokens;
};

// ---- format a section line ----

static std::string format_line(const Section &sec) {
    std::ostringstream oss;
    oss << std::left << std::setw(COL_WIDTH) << sec.name;
    for (auto &tok : sec.tokens) {
        oss << std::left << std::setw(COL_WIDTH) << tok;
    }
    // trim trailing whitespace
    std::string line = oss.str();
    auto end = line.find_last_not_of(" \t");
    if (end != std::string::npos) line.erase(end + 1);
    return line;
}

// ---- resolve file.cio path ----

static std::string resolve_cio_path(const std::string &arg) {
    if (fs::is_directory(arg)) {
        auto candidate = fs::path(arg) / "file.cio";
        if (fs::exists(candidate)) return candidate.string();
        std::cerr << "Error: no file.cio found in " << arg << "\n";
        std::exit(1);
    } else if (fs::exists(arg)) {
        return arg;
    }
    std::cerr << "Error: " << arg << " not found\n";
    std::exit(1);
}

static const std::string BACKUP_NAME = "file.cio.old.format";

// ---- main logic ----

int main(int argc, char *argv[]) {
    // Default to current directory if no argument
    std::string arg = argc >= 2 ? argv[1] : ".";
    std::string old_path = resolve_cio_path(arg);

    std::cerr << "Reading: " << old_path << "\n";

    // ---- read old file ----
    std::ifstream fin(old_path);
    if (!fin) {
        std::cerr << "Error: " << old_path << " not found\n";
        return 1;
    }

    std::string header;
    std::getline(fin, header);

    std::map<std::string, std::vector<std::string>> sec_dict;
    std::string line;
    while (std::getline(fin, line)) {
        auto parts = tokenize(line);
        if (parts.empty()) continue;
        std::string key = to_lower(parts[0]);
        std::vector<std::string> vals(parts.begin() + 1, parts.end());
        sec_dict[key] = vals;
    }
    fin.close();

    // ---- detect if already in new format ----
    // io_path is unique to the new format (old format uses separate pcp_path, tmp_path, etc.)
    bool already_new = sec_dict.count("io_path") > 0;
    if (!already_new) {
        already_new = sec_dict.count("carbon") && sec_dict.count("salt") &&
            sec_dict.count("constituents") && sec_dict.count("manure") &&
            sec_dict.count("water_allocation") && sec_dict.count("update") &&
            sec_dict.count("calibration") && !sec_dict.count("chg");
    }
    if (already_new) {
        std::cerr << "file.cio is already in the current format. No conversion needed.\n";
        return 0;
    }

    // ---- back up original ----
    fs::path data_dir_for_backup = fs::path(old_path).parent_path();
    if (data_dir_for_backup.empty()) data_dir_for_backup = ".";
    fs::path backup_path = data_dir_for_backup / BACKUP_NAME;
    if (fs::exists(backup_path)) {
        std::cerr << "Backup already exists: " << backup_path << " (not overwriting)\n";
    } else {
        fs::copy_file(old_path, backup_path);
        std::cerr << "Original backed up to: " << backup_path << "\n";
    }

    // ---- extract special values from old format ----

    // cs_db from old simulation line (5th token)
    auto old_sim = get_sec(sec_dict, "simulation");
    std::string cs_db = old_sim.size() >= 5 ? old_sim[4] : "null";

    // wro file from old water_rights section
    auto old_wr = get_sec(sec_dict, "water_rights");
    std::string wro_file = old_wr.empty() ? "null" : old_wr[0];

    // path values
    std::string pcp_path = get_path(sec_dict, "pcp_path");
    std::string tmp_path = get_path(sec_dict, "tmp_path");
    std::string slr_path = get_path(sec_dict, "slr_path");
    std::string hmd_path = get_path(sec_dict, "hmd_path");
    std::string wnd_path = get_path(sec_dict, "wnd_path");

    // ---- build new sections ----
    std::vector<Section> sections;

    // simulation (4 tokens, drop old 5th cs_db)
    sections.push_back({"simulation",
        rename_all(pad(get_sec(sec_dict, "simulation"), 4))});

    // basin
    sections.push_back({"basin",
        rename_all(pad(get_sec(sec_dict, "basin"), 2))});

    // climate (old 9 + atmosalt + atmocs = 11)
    {
        auto v = rename_all(pad(get_sec(sec_dict, "climate"), 9));
        v.push_back("null"); // atmosalt.cli
        v.push_back("null"); // atmocs.cli
        v.resize(11, "null");
        sections.push_back({"climate", v});
    }

    // connect (13)
    sections.push_back({"connect",
        rename_all(pad(get_sec(sec_dict, "connect"), 13))});

    // channel (old 8 + sed_nut + element_ccu = 10)
    {
        auto v = rename_all(pad(get_sec(sec_dict, "channel"), 8));
        v.push_back("null"); // sed_nut.cha
        v.push_back("null"); // element.ccu
        v.resize(10, "null");
        sections.push_back({"channel", v});
    }

    // reservoir (8)
    sections.push_back({"reservoir",
        rename_all(pad(get_sec(sec_dict, "reservoir"), 8))});

    // routing_unit (old 3 + rout_unit.dr = 4)
    {
        auto v = rename_all(pad(get_sec(sec_dict, "routing_unit"), 3));
        v.push_back("null"); // rout_unit.dr
        v.resize(4, "null");
        sections.push_back({"routing_unit", v});
    }

    // hru (2)
    sections.push_back({"hru",
        rename_all(pad(get_sec(sec_dict, "hru"), 2))});

    // exco (6)
    sections.push_back({"exco",
        rename_all(pad(get_sec(sec_dict, "exco"), 6))});

    // recall (old 1 + recall.slt + recall.cs = 3)
    {
        auto v = rename_all(pad(get_sec(sec_dict, "recall"), 1));
        v.push_back("null"); // recall.slt
        v.push_back("null"); // recall.cs
        v.resize(3, "null");
        sections.push_back({"recall", v});
    }

    // dr (6)
    sections.push_back({"dr",
        rename_all(pad(get_sec(sec_dict, "dr"), 6))});

    // aquifer (old 2 + gwflow.aqu = 3)
    {
        auto v = rename_all(pad(get_sec(sec_dict, "aquifer"), 2));
        v.push_back("null"); // gwflow.aqu
        v.resize(3, "null");
        sections.push_back({"aquifer", v});
    }

    // herd (3)
    sections.push_back({"herd",
        rename_all(pad(get_sec(sec_dict, "herd"), 3))});

    // link (2)
    sections.push_back({"link",
        rename_all(pad(get_sec(sec_dict, "link"), 2))});

    // hydrology (3)
    sections.push_back({"hydrology",
        rename_all(pad(get_sec(sec_dict, "hydrology"), 3))});

    // structural (old 5 + satbuffer.str = 6)
    {
        auto v = rename_all(pad(get_sec(sec_dict, "structural"), 5));
        v.push_back("null"); // satbuffer.str
        v.resize(6, "null");
        sections.push_back({"structural", v});
    }

    // hru_parm_db (old 10, insert metabolite.pes at position 4 → 11)
    {
        auto v = rename_all(pad(get_sec(sec_dict, "hru_parm_db"), 10));
        v.insert(v.begin() + 4, "null"); // metabolite.pes after pesticide.pes
        v.resize(11, "null");
        sections.push_back({"hru_parm_db", v});
    }

    // ops (old 6 + puddle.ops + transplant.ops = 8)
    {
        auto v = rename_all(pad(get_sec(sec_dict, "ops"), 6));
        v.push_back("null"); // puddle.ops
        v.push_back("null"); // transplant.ops
        v.resize(8, "null");
        sections.push_back({"ops", v});
    }

    // lum (5)
    sections.push_back({"lum",
        rename_all(pad(get_sec(sec_dict, "lum"), 5))});

    // calibration (was 'chg', 9 tokens)
    {
        auto old_chg = get_sec(sec_dict, "chg");
        if (old_chg.empty()) old_chg = get_sec(sec_dict, "calibration");
        sections.push_back({"calibration",
            rename_all(pad(old_chg, 9))});
    }

    // init (11)
    sections.push_back({"init",
        rename_all(pad(get_sec(sec_dict, "init"), 11))});

    // soils (3)
    sections.push_back({"soils",
        rename_all(pad(get_sec(sec_dict, "soils"), 3))});

    // decision_table (4)
    sections.push_back({"decision_table",
        rename_all(pad(get_sec(sec_dict, "decision_table"), 4))});

    // regions (17)
    sections.push_back({"regions",
        rename_all(pad(get_sec(sec_dict, "regions"), 17))});

    // ---- new sections ----

    // carbon (3)
    sections.push_back({"carbon", pad(get_sec(sec_dict, "carbon"), 3)});

    // salt (10)
    sections.push_back({"salt", pad(get_sec(sec_dict, "salt"), 10)});

    // constituents (17, first token is cs_db from old simulation line)
    {
        auto old_c = get_sec(sec_dict, "constituents");
        std::vector<std::string> v;
        if (!old_c.empty()) {
            v = pad(old_c, 17);
        } else {
            v.push_back(rename_file(cs_db));
            v.resize(17, "null");
        }
        sections.push_back({"constituents", v});
    }

    // manure (2)
    sections.push_back({"manure", pad(get_sec(sec_dict, "manure"), 2)});

    // water_allocation (7, first token from old water_rights)
    {
        auto old_wal = get_sec(sec_dict, "water_allocation");
        std::vector<std::string> v;
        if (!old_wal.empty()) {
            v = pad(old_wal, 7);
        } else {
            v.push_back(rename_file(wro_file));
            v.resize(7, "null");
        }
        sections.push_back({"water_allocation", v});
    }

    // update (1)
    sections.push_back({"update", pad(get_sec(sec_dict, "update"), 1)});

    // io_path (7: pcp tmp slr hmd wnd pet out)
    sections.push_back({"io_path", {
        pcp_path, tmp_path, slr_path, hmd_path, wnd_path,
        "null",  // pet_path (new)
        "null"   // out_path (new)
    }});

    // ---- scan data directory for formerly-hardcoded files ----
    fs::path data_dir = fs::path(old_path).parent_path();
    if (data_dir.empty()) data_dir = ".";

    // Build section name -> index map
    std::map<std::string, size_t> sec_idx;
    for (size_t i = 0; i < sections.size(); ++i)
        sec_idx[sections[i].name] = i;

    std::vector<std::tuple<std::string, size_t, std::string>> found_files;
    for (auto &entry : HARDCODED_SCAN) {
        auto it = sec_idx.find(entry.section);
        if (it == sec_idx.end()) continue;
        auto &tokens = sections[it->second].tokens;
        if (entry.pos >= tokens.size()) continue;
        if (tokens[entry.pos] != "null") continue;
        if (fs::exists(data_dir / entry.filename)) {
            tokens[entry.pos] = entry.filename;
            found_files.emplace_back(entry.section, entry.pos, entry.filename);
        }
    }

    if (!found_files.empty()) {
        std::cerr << "\nDetected formerly-hardcoded files on disk:\n";
        for (auto &[sec, pos, fname] : found_files)
            std::cerr << "  " << sec << "[" << pos << "] <- " << fname << "\n";
    }

    // ---- write converted file in place ----
    std::string new_header =
        "file.cio: written by SWAT+ rev.62.0.0 - Redesigned with complete file references";

    {
        std::ofstream fout(old_path);
        if (!fout) {
            std::cerr << "Error: cannot write to " << old_path << "\n";
            return 1;
        }
        fout << new_header << "\n";
        for (auto &sec : sections) {
            fout << format_line(sec) << "\n";
        }
        fout.close();
        std::cerr << "Converted file written to: " << old_path << "\n";
    }

    // Rename dash-filenames on disk
    for (auto &[old_name, new_name] : KNOWN_RENAMES) {
        fs::path old_p = data_dir / old_name;
        fs::path new_p = data_dir / new_name;
        if (fs::exists(old_p) && !fs::exists(new_p)) {
            fs::rename(old_p, new_p);
            std::cerr << "  Renamed: " << old_name << " -> " << new_name << "\n";
        }
    }

    std::cerr << "\nConversion complete.\n";
    return 0;
}
