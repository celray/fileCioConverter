# convertCIO

Author: Celray James Chawanda

A conversion tool for SWAT+ `file.cio` that transforms old-format `file.cio` files into the new redesigned format, allowing existing SWAT+ projects to run with the updated codebase.

This tool is part of the SWAT+ clean-up effort.

## What it does

- Reads an old-format `file.cio` and converts it to the new 32-line format
- Backs up the original file as `file.cio.old.format`
- Renames dash-separated filenames to use underscores (e.g., `weather-sta.cli` to `weather_sta.cli`)
- Consolidates separate path lines (`pcp_path`, `tmp_path`, etc.) into a single `io_path` section
- Adds new sections (carbon, salt, constituents, manure, water_allocation, update)
- Scans the data directory for formerly-hardcoded files and adds them to the appropriate sections
- Detects if a file is already in the new format and skips conversion

## Implementations

There are two implementations in `src/`:

- **C++ (`src/convertCIO.cpp`)** -- standalone binary, built with CMake
- **Python (`src/convertCIO.py`)** -- single-file script, no dependencies beyond Python 3

Both produce identical output.

## Building the C++ version

```bash
cmake --preset default
cmake --build build
```

The binary is output to `build/convertCIO`.

## Usage

```bash
# C++ version
./build/convertCIO [path]

# Python version
python src/convertCIO.py [path]
```

`path` can be a TxtInOut directory containing `file.cio`, or a direct path to a `file.cio`. If omitted, the current directory is used.
