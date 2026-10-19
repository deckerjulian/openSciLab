# Design rules

How openSciLab looks, reads and behaves, so that every part of it feels like the same
application. The rules are checked where they can be: `tests/test_terminology.py` (words),
`tests/test_theme_tokens.py` (colours, fonts, sizes), `tests/test_commands.py` (commands and
shortcuts) and `tests/test_node_audit.py` (nodes).

## Seven rules

1. **One word for one thing.** The glossary below is binding for the user interface, the node
   reference and the messages.
2. **One place for one task.** A capture is set up in the data view, a device in its document,
   a node or widget in the inspector. Other places link there instead of offering a copy.
3. **One way to run.** *Run*, *Pause* and *Stop* in the header (and the *Run* menu, F5, Shift+F5)
   drive whatever the active document runs: a flow, a panel, a capture, a waveform. No document
   has run buttons of its own.
4. **Problems next to their cause, not in a box.** Modal boxes only ask questions or report what
   cannot go on. Everything else: a message under the field, a badge on the node, a mark on the
   wire, a banner in the document, the status bar for what passes.
5. **Everything from tokens.** Colours, icon sizes and spacing come from `openscilab/ui/theme.py`
   (`token()`, `ICON`, `space()`); no literal colour, font or size anywhere else.
6. **Visible, not hidden.** Defaults are shown, the layout is kept between runs, nothing is only
   reachable through a right click.
7. **Documents keep to their contract** (`openscilab/ui/documents/base.py`): the shell shows a
   document's tool bar, inspector, menus, messages and run state in one place for all of them.

## Glossary

| Say | Not | Notes |
| --- | --- | --- |
| **device** | instrument, board, analyzer, hardware | "board" only for the printed circuit in firmware contexts; the document of a device is named after it |
| **data view** | logic analyzer, analyzer | the document that shows captures; its signals are **traces** |
| **capture** | acquisition | into the device's memory, once |
| **stream** | – | samples arriving while the device records |
| **stream to disk** | record | a stream written to a file until it is stopped |
| **monitor** | – | reading pins again and again, slowly |
| **run** | start, operate (as an action), continue | the header button is *Run*; its label says what runs: *Run* (flow, panel), *Capture* (data view, device), *Play* (waveform) |
| **real time** / **virtual time** | fast | how a flow's time passes |
| **simulator**, **simulated device** | simulation (of a device), test signals | *Simulated signals…* sets what a simulator plays |
| **settings** | preferences | only the settings of the application; the settings of the next capture are its **capture setup** |
| **example** | template, library | the projects that come with openSciLab; projects saved by the user to start from are **your templates** |
| **panel** | front panel | only the panel document; the screen of an oscilloscope is its **screen**; the tabs of a data view are its **analysis** |
| **waveform** | – | only the generator document |
| **channel** | line, net | a channel carries the name of the device's pin (D0, GP16, CH1); without a device CH1, CH2 … (counted from 1); in *Circuits* a probe's channel is named after the probe or its net |
| **pin** | – | a connection of a device (its document, GPIO nodes) |
| **rate** | sampling frequency | samples per second; *frequency* is a property of a signal |
| **measure** | measurements | |
| **install** / **update** (firmware) | flash | |
| **problem** (error, warning) | issue | |
| **section** | group, area, tab (of the sidebar) | an entry of the activity bar shows a section of the sidebar; plugins add sections (``ui/sections.py``) |
| **circuit** | schematic (as a document), design | the document of *Circuits* (``*.circuit.yaml``); its drawing is its *schematic* |
| **component** | part, element, symbol | a thing placed in a circuit (resistor, LM358, Arduino Uno); its look is its symbol |
| **wire** | trace, track, line | a drawn connection of a circuit |
| **net** | node, signal | only in *Circuits*: what wires, labels and pins connect; elsewhere it is a *channel* |
| **probe** | meter, scope | measures a net (or the current of a wire) while a circuit runs; a channel of the circuit's device |
| **microcontroller** | board, chip, MCU, device (in a circuit) | an emulated Arduino or ATmega in a circuit, running a firmware file |
| **analysis** | simulation, sweep (alone) | operating point, DC sweep, AC sweep, transient, noise, S-parameters - *computed*, not run |
| **component** | part, element | what a circuit is drawn with; "part" only in "a component with several units" is not said either |
| **terminal** | socket, jack, connector (in a circuit) | where a voltage is taken off a circuit; measured like a probe |
| **group (of a circuit)** | block, frame, cluster | components, wires and labels in a frame with a name and comment; its frame is its *frame* (the sidebar's "group" is a *section*) |
| **comment** | note, annotation | the author's text beside a component, wire, label or group |
| – | -, none | an empty value |

## Theme

* `token(name)` answers every colour: the palette of the shell (`TEXT`, `BORDER`, `ACCENT` …) and the
  tokens of the lab: `device.*` (state of a device), `type.*` (signal types: ports and wires),
  `group.*` (node groups: header tint, palette, minimap), `run.*` (state of a run), `trace.0-7`
  (series of every chart), `status.*` (results of checks), `display.*` (the waveform display,
  dark in both themes).
* Node groups are muted tints 20-25° of hue apart, signal types saturated colours: a node's group
  and the type of a port are never confused. Ports also differ by shape (round: values, square:
  signals, diamond: captures and tables, hexagon: devices).
* `space(n)`: steps of 4 px, scaled with the text size. `ICON`: `small` 12 (lists, tabs),
  `normal` 16 (buttons, tool bars), `large` 18 (tiles, headings), `activity` 20.
* One typeface: Inter Tight (`theme.load_fonts`, the application's font); painted text derives its
  font from the widget's (`smaller_font`, `scaled_font`); numbers in columns, code and the console use
  `tabular_font` (digits of one width), never a monospaced font of the system.

## Nodes

* A **group** is one English word (`device`, `gpio`, `generator`, `decoder`, `measure`, `signal`,
  `control`, `data`, `view`, `report`, `convert`, `time`, `remote`, `structure`); its title is a
  plural where it holds kinds of things (*Devices*, *Generators*, *Measurements*), else a topic
  (*Signal processing*, *Control*, *Data*, *Time*).
* A **node id** is `group.noun` in snake case – what the node *is* (`gpio.output`, `data.write`);
  its **title** has one or two words in the same pattern as its group's other nodes. Where the
  group is not visible (search, undo, problems) the title carries it: *Output (GPIO)*.
* Every **parameter** has a label and a description, every **port** a description.
* **Port names**: `in` / `out` for the main flow of values; `trigger` acts once per value;
  `start` / `stop` for what keeps running; `arm` arms a capture (its `trigger` is a setting);
  `reset`; `next` (sweep); `done` when it is over. A check (compare, range, report check) sends
  `result` (Bool: whether it holds) and the events `passed` and `failed`; the edges two recordings
  were aligned by are `matched`.
* **Parameter names**: `frequency` (never `freq`), `operator`, `title` for headings, `low` / `high`
  as quantities, `duty` from 0 to 1, `rate` for samples per second.
* **Types**: an `Event` input takes every value (each value is an event); `Any` is kept for nodes
  that work on whatever arrives (math, data, views, reports).
* A node shows its **effective** settings (defaults included) on the canvas: the parameters of
  its `summary`.
