#!/usr/bin/env python3
"""MCP server for building, netlisting, rendering and simulating Xschem
schematics.

Circuits are built from a curated set of standard xschem device symbols
(resistor, capacitor, inductor, sources, diode, bipolar/MOS transistors).
Connections between component pins are made the same way xschem itself
does it without drawn wires: a `lab_pin.sym` (or `gnd.sym` for net "0")
is placed exactly on top of each pin, tagged with the net name -- xschem's
netlister merges same-named labels into one node. Pin offsets below were
read directly from the symbol files in
/usr/share/xschem/xschem_library/devices/ and validated by round-tripping
a test circuit through `xschem -n -s -x -q` and checking the resulting
netlist.
"""

import re
import subprocess
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer

BASE_DIR = Path(__file__).parent.resolve()
SCHEM_DIR = BASE_DIR / "schematics"
SCHEM_DIR.mkdir(exist_ok=True)

mcp = MCPServer("xschem", version="0.1.0")

# type -> (symbol file, {pin name: (local_x, local_y) at rotation=0/flip=0})
DEVICE_TYPES: dict[str, dict] = {
    "res": {"sym": "res.sym", "pins": {"p": (0, -30), "m": (0, 30)}},
    "capa": {"sym": "capa.sym", "pins": {"p": (0, -30), "m": (0, 30)}},
    "ind": {"sym": "ind.sym", "pins": {"p": (0, -30), "m": (0, 30)}},
    "vsource": {"sym": "vsource.sym", "pins": {"p": (0, -30), "m": (0, 30)}},
    "isource": {"sym": "isource.sym", "pins": {"p": (0, -30), "m": (0, 30)}},
    "diode": {"sym": "diode.sym", "pins": {"p": (0, -30), "m": (0, 30)}},
    "npn": {"sym": "npn.sym", "pins": {"c": (20, -30), "b": (-20, 0), "e": (20, 30)}},
    "pnp": {"sym": "pnp.sym", "pins": {"c": (20, 30), "b": (-20, 0), "e": (20, -30)}},
    "nmos4": {"sym": "nmos4.sym", "pins": {"d": (20, -30), "g": (-20, 0), "s": (20, 30), "b": (20, 0)}},
    "pmos4": {"sym": "pmos4.sym", "pins": {"d": (20, 30), "g": (-20, 0), "s": (20, -30), "b": (20, 0)}},
}

GND_NETS = {"0", "gnd", "GND"}

SCH_HEADER = "v {xschem version=3.4.4 file_version=1.2}\nG {}\nK {}\nV {}\nS {}\nE {}\n"


def _schem_dir(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("name must match [A-Za-z0-9_-]+")
    d = SCHEM_DIR / name
    (d / "data").mkdir(parents=True, exist_ok=True)
    (d / "netlist").mkdir(parents=True, exist_ok=True)
    (d / "render").mkdir(parents=True, exist_ok=True)
    return d


def _pin_symbol_line(net: str, wx: float, wy: float, idx: int) -> str:
    if net in GND_NETS:
        return f"C {{gnd.sym}} {wx} {wy} 0 0 {{name=lg{idx}}}"
    return f"C {{lab_pin.sym}} {wx} {wy} 0 0 {{name=l{idx} lab={net} sig_type=std_logic}}"


@mcp.tool()
def list_schematics() -> list[str]:
    """List saved schematic names."""
    return sorted(p.name for p in SCHEM_DIR.iterdir() if p.is_dir())


@mcp.tool()
def save_schematic(name: str, sch_content: str) -> str:
    """Save (or overwrite) a raw xschem .sch file verbatim. Use this for
    hand-written/complex schematics; for standard components prefer
    build_schematic."""
    d = _schem_dir(name)
    path = d / "schematic.sch"
    path.write_text(sch_content)
    return str(path)


@mcp.tool()
def get_schematic(name: str) -> str:
    """Return the raw .sch file content for a saved schematic."""
    path = _schem_dir(name) / "schematic.sch"
    if not path.exists():
        raise FileNotFoundError(f"No saved schematic named {name!r}")
    return path.read_text()


@mcp.tool()
def build_schematic(name: str, components: list[dict]) -> str:
    """Build an xschem schematic from a component list and save it.

    Each component is a dict:
      {"ref": "R1", "type": "res", "value": "1k", "x": 150, "y": 0,
       "pins": {"p": "in", "m": "out"}, "props": "footprint=1206"}

    - "type" must be one of: res, capa, ind, vsource, isource, diode,
      npn, pnp, nmos4, pmos4 (call xschem_reference() for each type's
      pin names).
    - "pins" maps each pin name to a net name. Nets named "0", "gnd" or
      "GND" become a proper spice ground (gnd.sym); any other name just
      has to match across components to be electrically connected --
      no wire routing/coordinates between components is needed, only
      each component's own x/y (pin placement is derived automatically).
    - Place components on a grid at least ~100 units apart in x so
      labels don't overlap (y is usually 0 for a single row of parts).
    - "value" and "props" are optional (props is a raw extra string
      appended to the instance's attribute list, e.g. "m=2").

    Returns the generated .sch file path.
    """
    lines = [SCH_HEADER.rstrip("\n")]
    idx = 0
    for comp in components:
        ref = comp["ref"]
        dtype = comp["type"]
        if dtype not in DEVICE_TYPES:
            raise ValueError(f"unknown type {dtype!r}; valid: {sorted(DEVICE_TYPES)}")
        spec = DEVICE_TYPES[dtype]
        x, y = comp["x"], comp["y"]
        props = f"name={ref}"
        if comp.get("value") is not None:
            val = str(comp["value"])
            if " " in val and not (val.startswith('"') and val.endswith('"')):
                val = f'"{val}"'
            props += f" value={val}"
        if comp.get("props"):
            props += f" {comp['props']}"
        lines.append(f"C {{{spec['sym']}}} {x} {y} 0 0 {{{props}}}")
        pins = comp.get("pins", {})
        for pin_name, net in pins.items():
            if pin_name not in spec["pins"]:
                raise ValueError(
                    f"{dtype} has no pin {pin_name!r}; valid pins: {sorted(spec['pins'])}"
                )
            dx, dy = spec["pins"][pin_name]
            idx += 1
            lines.append(_pin_symbol_line(str(net), x + dx, y + dy, idx))

    d = _schem_dir(name)
    path = d / "schematic.sch"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def _run_xschem_netlist(d: Path) -> str:
    sch = d / "schematic.sch"
    if not sch.exists():
        raise FileNotFoundError(str(sch))
    out_dir = d / "netlist"
    for old in out_dir.glob("*.spice"):
        old.unlink()
    proc = subprocess.run(
        ["xschem", "-n", "-s", "-x", "-q", "-o", str(out_dir), str(sch)],
        cwd=d,
        capture_output=True,
        text=True,
        timeout=60,
    )
    spice_files = list(out_dir.glob("*.spice"))
    if not spice_files:
        raise RuntimeError(f"netlisting failed:\n{proc.stdout}\n{proc.stderr}")
    return spice_files[0].read_text()


@mcp.tool()
def netlist_schematic(name: str) -> str:
    """Run xschem headlessly on a saved schematic and return the
    generated flat SPICE netlist (for inspection/debugging)."""
    return _run_xschem_netlist(_schem_dir(name))


@mcp.tool()
def render_schematic(name: str) -> str:
    """Render a saved schematic to a PNG image and return its path, so
    the diagram can be visually inspected."""
    d = _schem_dir(name)
    sch = d / "schematic.sch"
    if not sch.exists():
        raise FileNotFoundError(str(sch))
    svg_path = d / "render" / "schematic.svg"
    png_path = d / "render" / "schematic.png"
    subprocess.run(
        ["xschem", "-x", "-q", "--svg", "--plotfile", str(svg_path), str(sch)],
        cwd=d,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if not svg_path.exists():
        raise RuntimeError("xschem did not produce an SVG render")
    import cairosvg

    cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), scale=2)
    return str(png_path)


def _parse_numeric_table(path: Path, max_rows: int = 500) -> dict:
    rows = []
    with open(path) as f:
        for line in f:
            parts = line.split()
            try:
                rows.append([float(p) for p in parts])
            except ValueError:
                continue
    truncated = len(rows) > max_rows
    if truncated:
        step = max(1, len(rows) // max_rows)
        rows = rows[::step]
    cols = list(zip(*rows)) if rows else []
    return {
        "n_rows": len(rows),
        "n_cols": len(cols),
        "columns": [list(c) for c in cols],
        "truncated_for_display": truncated,
    }


@mcp.tool()
def simulate_schematic(
    name: str,
    control_lines: list[str],
    spice_directives: str = "",
    timeout_s: int = 60,
) -> dict:
    """Netlist a saved schematic, run ngspice in batch mode, and return
    results.

    `control_lines` are the ngspice control-block commands, e.g.
      ["ac dec 20 1 1meg", "wrdata data/ac.txt v(out)"]
    or
      ["tran 1u 2m", "wrdata data/tran.txt v(out) v(in)"]
    Always end each analysis with a `wrdata data/<file> <vectors>` -- only
    files written under ./data/ are parsed and returned.

    `spice_directives` is optional raw SPICE text inserted before the
    control block (e.g. `.model DMOD D(IS=1e-14)` for a diode's model
    card, or `.options ...`).
    """
    d = _schem_dir(name)
    netlist = _run_xschem_netlist(d)

    control_block = ".control\n" + "\n".join(control_lines) + "\n.endc\n"
    lines = netlist.splitlines()
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if line.strip().lower() == ".end":
            insert_at = i
            break
    combined = "\n".join(lines[:insert_at])
    if spice_directives:
        combined += "\n" + spice_directives
    combined += "\n" + control_block + "\n.end\n"

    deck_path = d / "netlist" / "sim.cir"
    deck_path.write_text(combined)

    data_dir = d / "data"
    before = {p: p.stat().st_mtime for p in data_dir.glob("*")}
    start = time.time()
    try:
        proc = subprocess.run(
            ["ngspice", "-b", str(deck_path)],
            cwd=d,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        log = proc.stdout + proc.stderr
        success = proc.returncode == 0 and "error" not in log.lower()
    except subprocess.TimeoutExpired as e:
        log = (e.stdout or "") + (e.stderr or "") + f"\n[timed out after {timeout_s}s]"
        success = False

    new_files = [
        p for p in data_dir.glob("*")
        if p.stat().st_mtime >= start - 0.01 and (p not in before or p.stat().st_mtime > before.get(p, 0))
    ]
    data = {}
    for p in new_files:
        try:
            data[p.name] = _parse_numeric_table(p)
        except Exception as e:
            data[p.name] = {"error": str(e)}

    return {
        "success": success,
        "log": log[-8000:],
        "data_files": data,
        "netlist_used": combined,
    }


@mcp.tool()
def xschem_reference() -> str:
    """Reference for build_schematic: supported component types, their
    pin names, and the overall workflow."""
    lines = ["XSCHEM BUILD_SCHEMATIC COMPONENT REFERENCE", "=" * 42, ""]
    for t, spec in DEVICE_TYPES.items():
        lines.append(f"{t:10s} pins: {', '.join(spec['pins'])}")
    lines.append("")
    lines.append("""\
Ground: use net name "0", "gnd" or "GND" on any pin -- it becomes a real
spice ground (gnd.sym), not just a label.

Workflow:
  1. build_schematic(name, components=[...])
  2. render_schematic(name)   -> look at the PNG to sanity-check layout
  3. netlist_schematic(name)  -> inspect the flat SPICE netlist if needed
  4. simulate_schematic(name, control_lines=[...]) -> run ngspice, get
     parsed data columns back
  5. (reuse spice-mcp-server's plot_result-style plotting if you want a
     PNG plot of the data -- or just describe the numeric columns)

Example -- RC lowpass, one row of parts, AC sweep:
  build_schematic("rc1", components=[
    {"ref": "V1", "type": "vsource", "value": "AC 1", "x": 0, "y": 0,
     "pins": {"p": "0", "m": "in"}},
    {"ref": "R1", "type": "res", "value": "1k", "x": 150, "y": 0,
     "pins": {"p": "in", "m": "out"}},
    {"ref": "C1", "type": "capa", "value": "100n", "x": 300, "y": 0,
     "pins": {"p": "out", "m": "0"}},
  ])
  simulate_schematic("rc1", control_lines=[
    "ac dec 20 1 1meg", "wrdata data/ac.txt v(out)"])
""")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
