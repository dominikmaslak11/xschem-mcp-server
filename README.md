# xschem-mcp-server

Serwer MCP do budowania, renderowania i symulowania schematów
[Xschem](https://github.com/StefanSchippers/xschem) — pomyślany do sterowania
przez LLM (Claude/inne) na podstawie opisu słownego, bez ręcznego rysowania.

Komponenty łączy się bez routingu przewodów: etykieta (`lab_pin.sym`) lub
`gnd.sym` (dla sieci `0`/`gnd`/`GND`) jest automatycznie stawiana dokładnie na
współrzędnej pinu — xschem łączy w jeden węzeł wszystkie piny z tą samą nazwą
etykiety, tak jak globalne etykiety w KiCadzie. Mechanizm i współrzędne pinów
zweryfikowane empirycznie przez round-trip przez `xschem -n -s -x -q` i
sprawdzenie wygenerowanej netlisty.

## Narzędzia

- `build_schematic(name, components)` — buduje schemat z listy komponentów
  (typ, wartość, pozycja x/y, mapowanie pinów na sieci). Wspierane typy:
  `res, capa, ind, vsource, isource, diode, npn, pnp, nmos4, pmos4`.
- `render_schematic(name)` — headless render do PNG (do wizualnej weryfikacji).
- `netlist_schematic(name)` — generuje płaską netlistę SPICE.
- `simulate_schematic(name, control_lines, spice_directives="", timeout_s=60)`
  — netlistuje + uruchamia ngspice, zwraca sparsowane dane wynikowe.
- `save_schematic` / `get_schematic` / `list_schematics` — surowy dostęp do
  plików `.sch` (do ręcznych/złożonych schematów).
- `xschem_reference()` — ściągawka z typami komponentów, ich pinami i
  przykładowym workflow.

## Instalacja

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install mcp cairosvg
```

Wymaga zainstalowanego `xschem` i `ngspice` w systemie (`apt install xschem ngspice`).

## Rejestracja w Claude Code

```bash
claude mcp add --scope user xschem -- "$(pwd)/.venv/bin/python" "$(pwd)/server.py"
```

## Struktura wyników

```
schematics/<nazwa>/
├── schematic.sch      # plik schematu xschem
├── netlist/*.spice, sim.cir
├── data/*.txt          # wyniki symulacji (wrdata)
└── render/*.png          # wizualny render schematu
```
