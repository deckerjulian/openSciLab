# Changelog

All notable changes to this project are documented here. Versions follow
[Semantic Versioning](https://semver.org/) (pre-releases written as Python does: `0.1.0b1`). A
release is created by pushing a tag `v<version>` whose version has a section in this file (see
[Builds and releases](README.md#builds-and-releases)).

The project was called **PiPiLogicAnalyzer** up to version 7.2.0; as **openSciLab** it counts
again from 0.1.

## [Unreleased]

### Circuits: draw a circuit and simulate it live

A new section of the window, *Circuits* (Ctrl+Shift+C), and the document *Circuit* (`*.circuit.yaml`,
*File → New → Circuit*): schematics of analog, digital and RF circuits, simulated with ngspice
([docs/circuits.md](docs/circuits.md)).

- **Live simulation**: *Run* simulates the circuit without end, in real or virtual time; probes show
  their voltage, current or logic level on the schematic, LEDs light with their current, switches,
  push buttons and potentiometers are operated while it runs; a changed value goes in at once, a
  changed circuit starts again where its time was. *Speed*, a slider in the tool bar of the circuit,
  runs it slower or faster than the clock (0.001× to 100×), taken at once while it runs. ngspice runs in a process of its own: circuits run
  side by side and nothing else in openSciLab slows them down.
- **Analyses**: operating point, DC sweep, AC sweep (magnitude and phase on a logarithmic axis),
  transient, noise and S-parameters (with a Smith chart and Touchstone export), under *Results*.
- **A catalog of about 23 000 components**: openSciLab's own (passive parts, sources, diodes, LEDs,
  transistors, MOSFETs, op-amps, comparator, regulator, 555, switches, logic gates, probes,
  microcontrollers, RF), KiCad's symbol libraries (simulated as KiCad does, with standard models where
  a part has none, and op-amps, regulators, the 555, switches and 74xx/4000 gates further) and every
  model of SPICE libraries of makers; searched at once, with a preview of each part.
- **Microcontrollers**: an Arduino Uno, Nano or Mega 2560 (or an ATmega of KiCad's libraries) runs a
  `.hex` or `.elf` firmware, emulated cycle by cycle (simavr) in lockstep with ngspice; a serial
  monitor below the schematic.
- **Oscilloscopes as probes**: four channels with time and volts per division, offset, trigger (channel,
  level, edge, auto or normal), their picture on the symbol and on a screen below the schematic, with
  peak to peak, mean and frequency.
- **The circuit as a device**: `circuit:<file>` gives the probes as analog and logic channels to the
  data view, flows and panels; a flow opens a circuit by its file.
- **The tools in the vendor folder**: the applications bring ngspice and the AVR emulator, each with
  its license text (on Windows ngspice's own DLL package, without the GPL `table` code model); a
  release carries the sources of both (simavr is GPL, ngspice's KLU LGPL). A source installation finds
  them on the computer or installs them into the vendor folder when first needed (*Settings →
  Circuits*), from their own projects: ngspice's DLL package on Windows, built from the sources on
  macOS and Linux (or Homebrew, the package manager, KiCad), the AVR emulator built from simavr's
  sources; KiCad's symbols come from KiCad's archive on request; every download is checked against a
  pinned SHA-256; `tools/build_vendor.py` builds the folder in a checkout.
- **Power electronics**: a voltage-controlled switch, a bridge rectifier, a triangle source, a sine
  reference (its RMS from a control voltage), a variable transformer (a variac set in volts), terminals
  (named output sockets, measured like probes), a 900 V power op-amp (PA94), high-voltage transistors
  and a fast diode; knobs turn through the range their part names; op-amp models no longer fail while
  their supplies are not up yet; the settings
  *Start* (*power on*: every capacitor empty, as when switched on) and *Tolerance* (ngspice's
  `reltol`).
- **Fast live**: ngspice reads only the probes and the currents of LEDs at every step (the other nets 30
  times a second) and computes no other currents - circuits run up to twice as fast; numbers on the
  schematic change four times a second, the oscilloscope's readouts are painted and no longer lay out
  the window anew; traces are drawn as lines (a fast square wave took a hundred times longer as a
  path, the Arduino example slowed down with its potentiometer turned low).
- **See where the current flows**: while a circuit runs, a glowing stream flows along the wires and
  through the components like a liquid, with a soft glow, its waves in the direction of the current,
  wider and faster where more flows (round corners without a jump); the pins of a potentiometer, a
  transistor or a microcontroller show the current into or out of each of them, and wires and pins
  take the colour of their voltage - grey at 0 V, green positive, red negative, a gradient across a
  two-pin component (*Current* in the tool bar, on by default); a wire's tool tip says how much flows. The currents are measured by ngspice (an ammeter in front
  of each pin of a subcircuit) or follow from Kirchhoff's law. Drawing a running schematic got cheaper
  on the way: components keep their outline and their picture, the streams are one layer above them, the
  oscilloscope draws its traces without a step per point.
- An engine process that had run a microcontroller crashed when it ran the next circuit with values from
  outside (a device input): ngspice keeps calling the step callback once it had one.
- An engine process that had run a microcontroller crashed when it ran the next circuit with values from
  outside (a device input): ngspice keeps calling the step callback once it had one.
- **Pins of connected devices in a circuit** (hardware in the loop): a *device input* is a pin a
  connected device reads - its level or voltage drives the net while the circuit runs; a *device
  output* is a pin it drives - high or low, or the voltage of an analog output, following the net. The
  inspector offers the connected devices and their free pins; problems are shown at the component; the
  outputs are made safe when the circuit stops.
- **The Probe button offers every probe**: voltage, current and logic probes, the oscilloscope, a
  terminal and the pins of connected devices (Shift+P still places a voltage probe).
- **Comments and groups**: components, wires and labels carry comments shown beside them; groups
  (Ctrl+G) put parts of the circuit in a dashed frame with a name, a comment and, if wanted, a light
  background colour, are selected at their frame, move their members when dragged and are taken apart
  with Ctrl+Shift+G.
- **Potentiometers turn whichever way they lie**: the wiper follows the pointer along the
  potentiometer (dragging a lying one sideways did nothing; dragging any of them failed for a while).
- **Moving keeps connections**: the wires grow along while parts are dragged, wires between moved parts
  move with them, and a pin that touched a wire's middle, another pin or a label gets a new piece of
  wire.
- **Typing searches**: any letter typed on the canvas searches the catalog; the tools moved to Shift+W,
  Shift+L, Shift+G, Shift+P (and stay in the tool bar and the menu); R and X act on a selection.
- **After a review of the plugin** (2026-10-17), fixed: two probes of one name on different nets were
  one node in the netlist; a supply symbol without parameters took the net "Supply"; a noise analysis
  named the net as shown, not as SPICE knows it; KiCad parts with their own model parameters, inferred
  regulators (LM2940-5.0 was 40 V) and the 555, model libraries with continuation lines after comments
  and nested subcircuits, level-1 MOSFET models; both ends of a wire follow when a component between two
  moved ones is straightened; groups forgot nothing when a wire collapsed (a new wire took its place in
  the group); references may not be called like wires (`w2`); paste keeps comments and groups; Ctrl+G on
  a group keeps its name; edits that change nothing push no undo step; a dead or aborted ngspice run
  says so instead of staying "running"; a probe added while running gets its course; the engine's pipe
  and its pooled processes are locked and cleaned up; analyses that hang are stopped; the device's
  voltage range follows the sources, not their frequencies; oscilloscope channels of the device are
  against their GND; after a restart the microcontrollers went on (they froze for as long as they had
  run) and take their new wiring, Reset starts the firmware as switched on, serial input and Reset no
  longer call simavr from another thread; the emulator reports PORT register writes (an output written
  to the level the net already had, a pull-up switched off, were lost); KiCad's Arduino modules supply
  +5V; malformed firmware files are reported; the serial monitor keeps lines whole and names Serial1-3;
  the inspector no longer went blank after Delete or undo and keeps what is typed before a save or
  close; rename problems stand under the field; dragging a group frame moves its wires; knobs ignore
  sideways scrolling and follow the wheel's size; the oscilloscope is cleared when the run stops and
  does not overwrite what is typed; the catalog does not download KiCad's symbols with KiCad turned
  off and says when the index failed; malformed circuit files and failed exports are reported, not
  raised; installing one tool no longer rewrites another's loaded library (a staged install), downloads
  are tried again and the source archives are checked against pinned SHA-256; the AVR emulator is
  installed on demand and its shim ships with the package.
- **A busy computer no longer stops a circuit**: a run whose first point came later than the engine
  process first reported was taken for stuck at once ("ngspice does not get on (it fails to
  converge)") while ngspice went on; a sidebar opened while the window was still laid out keeps its
  width.
- Examples: *Circuits* (an RC low pass, a transistor switch, an inverting amplifier, a half adder, an
  Arduino blinking an LED as fast as its potentiometer says, held on by a switch, watched by an
  oscilloscope, and a hybrid AC/DC lab power supply drawn with wires only, in framed blocks: DC, AC
  or both up to 230 V set with the potentiometers `RV_DC` and `RV_AC`, switching regulators for the
  rails 10 V beyond the peak, a linear power amplifier for an exact, clean output at `OUT`/`COM`). Probes show their name with
  the value (`C 105 mV`). In virtual time a capture of a circuit keeps to the flow's time (a timer's
  second passed at once, the capture took the circuit's first moments). A part whose pins are all on one net is said
  to be shorted (the transistor switch had its LED shorted that way and its potentiometer upside down).

### Plugins add sections of their own

A plugin can add an entry of the activity bar with a section of the sidebar, kinds of documents
(their files, *File → New*, the Project section, the session) and icons (`openscilab/ui/sections.py`,
[docs/drivers.md](docs/drivers.md#sections-and-documents-of-a-plugin), the example
`examples/plugins/notes_section.py`); *Help → Plugins* lists them. *Circuits* is built this way.

### One typeface, a new mark

- **Inter Tight** everywhere - menus, documents, flow nodes, schematics, the console and the editors
  (numbers in columns with digits of one width) - bundled with openSciLab (`openscilab/ui/fonts`, SIL
  Open Font License, its text beside the fonts), so every platform shows the same letters.
- **A new logo**: three channels of a logic analyser in white, teal and blue on a graphite squircle;
  the header shows the mark alone, larger.

### One application: the interface overhauled

The parts grew one after the other; now they follow one set of rules ([docs/design.md](docs/design.md):
a glossary, one place per task, one way to run, problems next to their cause, everything from the
theme). Checked by `tests/test_terminology.py`, `tests/test_theme_tokens.py` and
`tests/test_node_schema.py`.

- **One way to run**: *Run*, *Pause* and *Stop* in the header – and the new *Run* menu, F5, F6,
  Shift+F5, F10 – run what the active document runs: a flow or a panel, a capture in a data view
  or with a device (*Capture*), a waveform (*Play*). Flows, panels and waveforms have no run buttons
  of their own any more; *Real time | Virtual time* sits beside *Run*, *Use simulators for every
  device* is in the *Run* menu.
- **The shell**: menus *File* (was *Project*), *Edit*, *View*, *Run*, *Devices*, *Examples* (was
  *Templates*), *Help*; one *New* button whose menu is *File → New* in the same order; the sidebar's
  *Search* is the command palette (Ctrl+K); a click opens an entry in every list of the sidebar;
  the sidebar is as it was left; no untitled flow at the start; the status bar shows problems with
  icons and the devices (a click shows them); a double-click on a tab no longer opens a window.
- **The data view is a document like the others**: no menu bar, status bar or docks of its own; its
  analysis (decoders, measure, search, markers, the capture) is in the inspector, the listing below
  the traces (Ctrl+2), what goes on now in the bar below them, its notes in the shell's status bar.
  A decoder is added at once (the channels found by name), its options are edited below the list
  instead of in a second dialog.
- **Devices**: a document with *Data view*, *Use in a flow* and *Stream to disk* in its header (*Run*
  captures); *Overview* (was *Details*) lists the device's functions with what each does; the pin
  table names the modes (*Input (pull-up)*), shows only the actions of a pin's mode and numbers the
  capture channels as the data view does (CH1); profiles are only in the data view.
- **Nodes, renamed after one scheme** (no compatibility: `python tools/rename_nodes.py <folder>`
  converts flows, panels, Python and Markdown): `device.device`, `gpio.output` / `input` /
  `analog_output`, `generator.*` (was `gen.*`; `tx_uart` → `uart`, `output` → `file`), `decoder.*`
  (was `decode.*`), `signal.*` (was `dsp.*`), `data.write` / `read` / `bundle` / `unbundle`,
  `view.chart`, `report.chart`, `convert.events` / `bit` / `analog` / `number`, `time.sync` /
  `align` / `latency`, `remote.input` / `output` / `command` / `align`. `control.counter` became
  `measure.count` (with `reset`), `control.limit` became `control.range`. Checks send `result`
  (Bool) and `passed` / `failed` (events); `freq` → `frequency`, `op` → `operator`, the title of a
  check is `title`. Every port and parameter has a description and a label; an `Event` input
  (trigger, arm, start, stop, reset) takes any value. The node reference of docs/lab.md is written
  from the registry (`tools/gen_node_docs.py`).
- **The flow editor**: a node shows its group by colour and icon, its title, its effective settings
  (defaults too) and ports whose shape tells their kind; the inspector has *Settings*, a folded
  *Advanced* part and *Node*, says what is wrong under the field, and edits the steps of a sequence
  and the states of a state machine as tables; the palette keeps which groups are open, finds nodes
  by keywords and descriptions; the quick search shows icon, title and group; broken wires are drawn
  dashed with the reason; the arrow keys move the selection; undo names what it undoes (*Add
  Timer*); the view nodes of a running flow are tiles of one document (*Views · flow*), not a tab
  each.
- **Panels**: the options of a widget are fields (minimum, maximum, unit …) instead of YAML text;
  operating a panel whose flow does not run says so, with *Run* beside it.
- **Theme**: one source of colours (`theme.token`), node groups and signal types told apart by hue,
  one palette for every chart, the display colours of the data view as tokens; sizes and icons by
  name; simulators are *Simulated …* everywhere.

### Application

- Bus rows of a long capture: a value that began exactly at the edge of a pixel column could be
  drawn one column off (the quick way of looking up a whole capture rounded the column edges
  differently from the runs); now both round the same.
- **Docks sized for the screen**: the sidebar, the inspector and the node palette take their widths of
  the theme (larger with the text size), at most a share of the window - on a small screen the document
  keeps about half - and never less than their contents need (fonts of other platforms are wider); a
  section the sidebar does not show no longer widens it (the catalog of *Circuits* did).
- **The sidebar starts folded away the first time**: the activity bar (or Ctrl+B) opens it at its
  width (250 px), and again at that width when it takes more than a third of the window; after that
  it is as it was left.
- **Patterns from blocks**: *Edit pattern…* in the inspector of `generator.pattern` builds the tracks
  from a block per pin - a clock, a square wave, a constant level, a bit of a counter, bytes, UART
  text or SDL text - with the pins of the device to choose from and a preview. *SDL text* shows and
  edits what the node stores; a block is its SDL behind a comment that names it (`// clock
  period=2 start=low`), so the pattern opens as blocks again, and SDL changed by hand stays text.
- **The inspector offers what the flow knows and locks what does not count**: a channel of the
  states wired into *State bit*, the clocks of a device's state mode, the initial state of a state
  machine, the pulled inputs of a Python node, the outputs of the generator's kind, the annotation
  rows of a decoder - as a choice instead of free text (a value that is not among them stays,
  marked). A parameter that a wire sets or that belongs to another setting is locked with the
  reason in its tooltip: a check's tolerance without an expected value, the value of a comparison
  with 'b' wired, the duration of a capture with samples, the clock edge without a clock, the
  cutoff of a moving average, the flush of a log that is not CSV, among others.
- **Inputs chosen from the flow**: the traces of a strip chart, the inputs of a Python node, the
  columns of a table and the fields of a bundle pick outputs of the flow (▾): a tick adds an input
  named after the output and wires it, unticking removes both. An unbundle offers the fields of the
  bundle wired in.
- **Editors of their own** in the inspector: the trigger of a capture (none, an edge or a pattern of
  a captured channel), the channels a replay maps to pins (keys from the capture wired in, pins of
  the device), colours (a colour dialog), units and `auto` values (a list to pick from, any other
  may be typed); the pins of SPI and I²C sending say their order. The size of a comment or group
  is no longer in the inspector (its frame sets it).
- *Check* says what its limits are: *minimum (≥)*, *maximum (≤)*, *± tolerance*, *display unit* and
  *title* (stored as before as `low`, `high`, `tolerance`, `unit`, `name`).
- Text of several lines in a flow file (Python code, the SDL of a pattern) is written as a block
  (`|`), also inside a mapping, instead of a quoted line with `\n`.
- **Example *Loopback with state mode (Arduino Mega)*** (*Digital I/O*): one pattern drives an analog
  signal (A0, read back at A1 by the ADC), a digital signal (D34 to D35) and a clock (D3 to D2); the
  monitor logs A1 and D35, a state capture takes D35 on each rising edge of D2. It runs on the new
  simulated Mega (`sim:mega`, wired the same way) and passed on a real Mega 2560 with these three
  wires (16 rising edges in 64 states, A1 from 0 V to 5 V).
- **Example *Loopback test (Pico W)*** (*Digital I/O*): a test bench for a Pico with three wires,
  GP2–GP6, GP3–GP7 and GP4–GP8. One pattern drives a clock, a data line and UART frames, a capture
  takes them back, a state capture takes GP7 on the clock edges at GP6; then a PWM on GP2 and an
  SPI transfer whose bytes come back over the wire GP3–GP7 (MISO); seven checks in the report.
  Runs on `sim:pico` (wired in its project) and passed on a real Pico W.
- `measure.count` counts the edges of states without time stamps (a state capture of a Pico): it
  stopped with "the time base is unknown".

### Devices

- **The monitor of a Pico with its analog inputs**: the pin table of the device document starts the
  monitor with the pins of the ADC (GP26–GP28), which the Pico refused ("no analog input 'GP26'
  (A0, A1, …)"); the monitor and the analog inputs of a Pico take A0–A2 and their pins alike.
- The simulators read MISO of an SPI transfer while MOSI drives it: wired to MOSI it gave zeros
  instead of the bytes sent.
- **The monitor of a flow on a real Arduino**: `device.monitor` asks the device at its rate, which
  only the simulators answered ("the monitor of ... reports only while it runs"); the Arduino now
  answers with the levels and analog inputs of that moment (READ, ADC_READ).
- `sim:mega`: a simulated Arduino Mega 2560 with all 70 pins, its 68 channels in the order of the
  real board, wired D34 → D35, A0 → A1 and D3 → D2.
- **Arduino Mega 2560: all 70 pins.** D0–D53 and A0–A15 are pins of the device card, of the flows
  and of the logic analyzer (68 channels; D0/D1 carry the USB serial line), A0–A15 are its 16
  analog inputs. Before, the pin masks of the Arduino protocol held 32 pins, so the Mega offered
  only whole ports (D22–D29, D10–D13 with D50–D53, D42–D49, A8–A15) and neither A0–A7 nor most of
  the numbered pins. Tried on a Mega 2560 with D34–D35 and A0–A1 wired together: inputs and outputs
  both ways, analog inputs, monitor, buffer and stream captures across ports, the pattern
  generator on pins of two ports.
- A buffer capture of an Arduino holds as many samples as a byte per port of its pins allows: on
  the Uno D8 alone takes one byte (it took two, by the highest pin), on the Mega up to 11 bytes
  for all 68 channels (372 samples).
- **An Arduino Mega that did not answer**: opening its port restarts it, and a request during the
  first second keeps its bootloader (STK500v2) from starting the firmware until the next restart,
  so the Mega was never found ("no openSciLab Arduino firmware answers"). A board that does not
  answer the first request is restarted once (DTR) and asked again after 1.5 s of silence; a Mega
  known by its USB identifiers is not asked before its bootloader is over (2.3 s to open instead of
  3 s).
- *Update firmware* writes an image of the Arduino Mega 2560 (`openSciLab_megaatmega2560.hex`) for
  its chip and bootloader (avrdude `-p atmega2560 -c wiring`); before, every AVR image went to an
  ATmega328P, which the Mega refused.
- **The C64 profiles say what they are for**: the two profiles for gusmanb's LogicAnalyzer board V2
  are *C64 expansion port - LogicAnalyzer V2: ...* (file `c64-logicanalyzer-v2.json`, was
  `c64-expansion-port.json`, whose copy in the profiles folder is removed unless it was
  changed), and the Arduino Mega 2560 has a profile of its own, *C64 expansion port - Arduino Mega
  2560 (Ben Eater's wiring)* (`c64-arduino-mega.json`): the pins of Ben Eater's 6502 monitor
  (address bus on the even pins 22-52, data bus on the odd pins 39-53, Φ2 on D2, R/W on D3), the
  other lines of the port on free pins of the same ports, a state capture per falling Φ2 edge with
  the buses *Address* and *Data*. Made for a slowed or single-stepped 6502: the Mega sends about
  1,000 states a second, a C64 at full speed (1 MHz) is far too fast for it.

### Firmware

- **Arduino protocol 2** (`docs/protocols.md`): pin masks and levels are `ports` bytes (HELLO), bit b
  of byte k is pin 8k+b; on the AVR boards byte k is a port of the chip, so the pin table follows
  the ports and has gaps (`PINS` answers `-` for a number without a pin). Capture samples, the
  runs of `DATA` events and of the pattern generator hold a byte per port of their pins;
  `GEN_LOAD` carries the pins of its runs, `GEN_START` no longer; HELLO says `gen_bytes` instead
  of `gen_runs`. openSciLab works with protocol 2 only: a board with the Arduino firmware of
  0.1.0b2 asks for the firmware that comes with the application. The pin numbers of the Uno and
  Nano changed with it (A0 is 16), the simulated Arduino profiles say `gen_bytes`.
- **Pico, RP2040 boards** (found on a Pico W): streams ended at once with an overflow, without a
  sample - the interrupt flags left by the previous use of the DMA channels counted a pass over
  the buffer right at the start; they are cleared before the stream starts. The self-test reported
  every free channel `STUCK_HIGH` (and the device information counted them driven): the pull-down
  was read before it had discharged the pin; it now waits as on the RP2350.

## [0.1.0b2] - 2026-10-11

### Application

- *Front panel* of an oscilloscope: *Open in a window* shows its screen (the Web Control stream) in a window of its
  own, which keeps running while other tabs are shown; closing it puts the screen back. *Capture with these
  settings* captures as set on the oscilloscope - the channels that are on, its time base, memory depth and trigger
  (a new `CaptureSession.device_settings`, capability `DEVICE_SETTINGS`); *Again* repeats it that way.
- *Device card of a Rigol DHO900*: when the bridge app is not running on the oscilloscope, the card offers to install
  it (a banner; closed, it does not come back while the card is open), and *Details* has *Install bridge app...*
  (*Update bridge app...* when it runs). openSciLab connects adb over the network (port 55555), installs the app,
  starts it without a touch on the screen and connects again through it. Needs adb (Android platform tools).
- *Capture settings*: the analog inputs are channels in the list beside the logic ones (with name
  and colour), no longer a row of small check boxes below it; the logic channels carry the names
  of the device (the Rigol: D0–D15, so they are not taken for its analog CH1–CH4). A device
  without a pattern trigger does not offer one.
- **A tab into a window of its own**: drag it off the tab bar or double-click it (as well as
  *Open in a new window* on its right click); closing the window puts it back.
- The close button of a tab sits inside the tab, centred on its title.
- The sidebar starts at its width (250 px) instead of taking the room of the hidden node palette
  as well; the node palette opens beside it at its own width. A layout saved by an earlier version
  is not restored once (it held the wide sidebar).
- *Signals* of a simulator's device card: the button for a channel's own signal says that it acts
  on the channel selected in the table (*Set signal of the selected channel…*) and is off while
  none is selected (before, it did nothing without a selection).
- Measuring the latency of a simulator works out of the box: the *Timing* tab starts with a
  loopback the simulator already has (`sim:daq`: P0.0 → P0.1) instead of the reverse pair, whose
  wire made a loop, and offers only the rates the device reaches in the chosen mode (`sim:daq`
  streams at most 50 kHz; before, 100 kHz failed with "Specified parameters are incorrect"). A
  rate above the device's maximum is refused with that maximum, *Wire them* says on the *Timing*
  tab when a wire cannot be made or is already there, and the *Wires* of the *Signals* tab list
  the wires of the simulator's profile as well.
- A latency measured with a capture (*Timing* tab, `timing.calibrate` with `mode: capture`)
  switches the output within the capture: a fast capture is short (100,000 samples at 10 MHz:
  10 ms), so the switches come faster (down to 2 ms apart) and fewer; before, they came 30 ms
  apart and missed it ("0 of 10 edges seen"). The capture holds at most what the device's memory
  does. A capture too short to switch in (100 MHz: 1 ms) is refused with the highest rate that
  works (16.6 MHz for 100,000 samples), and the *Timing* tab offers only the rates up to it.
- Loading a profile from a device card opens no data view: its decoders go to the device's data
  view when one is open, else to the next one.
- A panel has an icon of its own - a dial in a frame, the front of an instrument - and the *Panel*
  button says what a panel is.
- **Panels are laid out freely, like in a GUI editor**: widgets are placed and sized in pixels
  on the panel's surface instead of grid cells. Moved or resized (eight handles) they snap to the
  edges and middles of the other widgets and of the panel - red guides show it - or to a raster of
  8 pixels (Alt: freely). Shift/Ctrl and a click or a frame on the free surface select several;
  the arrow keys move the selection; *Arrange* (tool bar, right click) aligns, distributes and
  orders them; the panel's corner resizes it. An operated panel grows and shrinks with its window
  as a whole. The panel file stores `width`/`height` of the panel and `x`, `y`, `width`, `height`
  of each widget (`row`, `column` and `columns` are gone; the example panels are converted).
- **Templates of your own**: *Project → Save project as template…* (also at the top of the
  *Templates* menu) keeps the project as a template - its flows, panels, nodes, waveforms and
  devices, without the captures and results of its `data` folder, with a name and a description.
  Your templates come first, as *My templates*, on the start page and in the *Templates* menu;
  delete one with a right click on its tile or *Templates → My templates → Delete*. They are kept
  in `templates/` of the settings directory.

### Stability

- **Errors nobody caught are kept**: an error in a part of the window or a thread that nobody
  handled lands in `crash.log` in the settings directory with its traceback, and the status bar
  says so; *Help → Open the log folder* opens the folder. A crash of the application itself (or
  of a device process) leaves the stacks of its threads in `faults.log`.
- **A device process that hangs is ended**: a device process sends a sign of life every second;
  after 10 s without one, or when a call to it takes longer than 60 s, it is ended and its device
  shown as disconnected (its capture fails with the reason) instead of freezing what waited for it.
- **Text with control characters is saved so it can be read again**: a flow whose description or
  a comment held a control character (pasted from elsewhere) was written but could not be opened
  any more; a line break of Unicode (NEL) in the title of a panel widget came back as a space.
  Flows, panels, projects and waveforms now write such text with escapes (found by the fuzz tests).
- A sigrok session file with damaged metadata is refused with a message that says so, instead of
  an error of the configuration reader.
- A short capture of a device in a process of its own could leave the device "capturing" for
  good, refusing every capture after it: on a slow computer the capture ended before the answer to
  its start arrived (found by the crash hunt on the CI runners).
- `report.check` takes `last: true`: only the last value counts, checked when the flow ends - for a
  measurement that settles. The example *Align instruments* checks the accuracy so; its first,
  rough value failed the check on a busy computer.
- The device card of a device whose process just ended no longer raises an error: it asked the
  monitor of the device every few hundred milliseconds whether it runs, and a tick between the end
  of the device process and the card's close failed (*An error occurred* in the status bar).
- The full screen window of a panel (F11) is deleted by Qt when it closes. Before, Python's garbage
  collector freed it at a moment of its own, which could crash the application when that moment
  fell into something Qt did with its windows.
- No error while a capture still arrives is drawn: the data view read the state of the transfer
  twice in one paint, and when the transfer ended in between, the paint failed (a flickering view,
  "endPaint called with active painter" in the log).
- A protocol decoder that cannot be loaded is no longer left out silently: *Help → Decoder search
  paths…* names it with the error, and the log says so.
- Nothing fails silently any more: every error openSciLab ignores on purpose (a port that is gone
  while it closes, a device that cannot tell its capabilities, ...) is in the debug log with its
  traceback (`--debug-driver`, *View → Console*).
- For developers: `tests/test_fuzz_parsers.py` throws arbitrary input at every parser (files,
  protocol frames, addresses, quantities) with hypothesis, and `pytest -m soak tests/test_soak.py`
  streams for minutes while it watches the memory; the workflow *Stability* runs the soak test
  on request, and a crash hunt: the tests on several runners at once, a crash with its Python and
  native stacks.

### Devices

- **Simulators of USB devices run in a process of their own**, as the device they simulate: a
  simulator of the device list that emulates a USB link (`sim:pico`, `sim:daq`) is read in a device
  process - the same way as a real Pico, with its processor load in the device list and *Details →
  Process* on its card. Its *Signals*, *Events* and *Timing* tabs and *Devices → Simulate faults*
  work as before. Simulators that know the time of their samples (`sim:uno`) and those a flow
  opens stay in the application. *Settings → Devices: Run simulators of USB devices in a process of
  their own* (on by default) switches it off.
- **Wires between simulators across processes**: `simulation.wiring` of a project and trigger
  routes of the hub connect simulators wherever they run - in the application or in device
  processes. The input reads the other simulator's net through a small net server of that process;
  simulators in device processes share the system's clock. *Synchronized instruments* runs with
  `sim:pico` and `sim:daq` of the device list. A wire stays when the target simulates something
  else; an input whose simulator went away reads low.
- A flow that uses simulators of the device list gives them the wires of the project within one
  device too (`logic: [{from: GP16, to: GP17}]`); before, only those between devices - so
  *Synchronized instruments* aligned nothing when its simulators were connected in the device list.
- A simulator in a device process starts with a capture that needs no trigger, as one in the
  application does (the device process gave the application `is_simulator` as false); and a device
  process that opens very fast no longer fails to send its first sign of life.
- **The device list shows what each connected device does**: its state (*Ready*, *Armed*,
  *Receiving 45 %*, *Failed*), how it is connected, the settings of its next capture (rate,
  samples, channels, trigger, stream), how long a capture lasts (*3.28 ms of signal*) and a bar
  with its load: how much of a running capture arrived, how much of the link a stream uses, what
  a device process takes of a processor core (orange from 90 %), else how much of the device
  memory a capture uses - said only when it does not fill the memory, as the settings of a
  capture do by default. It follows the device every second while the list is shown.
- **The built-in devices are plugins too**: the Pico boards, Arduino, DSLogic, Rigol, the
  simulators and the remote devices register their kinds of address the same way a plugin does
  (`openscilab/plugins/`). *Help → Plugins…* and `openscilab-cli plugins` list them as *built in*,
  together with every kind of address. A plugin can now also open its device as an instrument
  with all its facets (`instrument=`), say that no simulator stands in for it
  (`simulation=None`), or that it is a simulator itself (`simulator=True`); see
  [docs/drivers.md](docs/drivers.md#plugins).
- **Plugins add nodes for the flows**: a plugin's functions with `@node` - as a project's own nodes
  - are known to every flow and project, in the palette (in a group of their own, titled with
  `node_group`), in flow files and to `openscilab run`. *Help → Plugins…* and
  `openscilab-cli plugins` list them; a flow with a node of a missing plugin says so; a plugin
  cannot replace a node of openSciLab. Example: `examples/plugins/calibration_nodes.py`
  (two-point calibration, NTC thermistor).
- *Simulate* of a flow runs a simulated Arduino (`arduino-sim:`) on the simulator *uno* and a
  simulated Rigol (`rigol-sim:`) on *dho924s*, as it does for the real devices (before: *free*).
- A Pico whose monitor reports 100 times a second or more keeps its outputs: the driver read the
  reports as long as they came and sent no heartbeat meanwhile, so the board's watchdog released
  the outputs and stopped the monitor after 1 s, and commands waited up to half a second.
- **The latency of a Pico is measured with a capture** (*Timing* tab, `timing.calibrate` with
  `mode: capture`): its capture without a trigger failed with "Specified parameters are incorrect",
  since the firmware has no immediate trigger of its own. The driver now starts it as a trigger
  sequence without stages, which begins at the first sample - up to a fifth of the system clock
  (40 MHz on a Pico at 200 MHz), and the *Timing* tab offers the rates up to it. A Pico takes no
  command while it streams, so a measurement with a stream says to measure with a capture, and an
  output that cannot be switched ends the capture instead of leaving the device capturing.
- **The board around a Pico is checked when it is opened and when it is written**: the firmware
  measures it (`INFO:TRIGGER_CHAIN`, `INFO:CHANNELS_DRIVEN`). The LogicAnalyzer board of gusmanb
  (its trigger chain pulls GP0/GP1 to GND) with the image of a bare Pico, whose outputs would drive
  against the board's level shifters, is not opened: a warning offers the firmware for the board,
  and using it anyway takes a tick that confirms the risk (kept for this board by the unique ID of
  its chip). Such a board shows *wrong firmware for this board* in *Connected hardware*, whose
  *Install firmware* takes the image for the board; installing another image there, and a board just
  written with one, ask again. A board used at one's own risk says so on its device card. Flows,
  scripts and the command line refuse it with the reason.
- **Record**: a stream to the disk until *Stop* that keeps everything (as long as the disk has room),
  from the data view and the device card of every device that streams; it runs at the rate of the
  settings or the highest the stream reaches and shows its time, size and the free space while it
  runs.
- A stopped capture of a device in a device process (the DSLogic, a Pico) no longer leaves the
  application thinking it still captures: a stopped buffer capture reports nothing, and the
  application waited for it for ever (*Stop* seemed to hang). A result that comes after the stop
  ended the capture is no longer taken for the result of the next one, a request that names a
  capture the device process already forgot fails that call instead of ending the device process,
  and a failed stop or start does not leave the capture state behind.
- DSLogic: a stopped stream says it is idle only once it handed over what it received; a stream
  that overflows says so at once instead of unpacking its backlog first; a disk that runs full ends a
  recording with a message (before, without any when nothing had arrived yet).
- Disconnecting or reconnecting a device closes it in the background: a device process that does not
  answer kept the window frozen for up to about 12 s.
- DSLogic: an endless stream whose trigger scrolled out of the samples kept (or a stream stopped
  before its trigger) marks no sample as the trigger (before, sample 0 or one past the end).
- Pico: the answer to a command sent as a capture ends comes after the capture data; it reaches its
  command even when the completion starts the next capture at once (a flow) - before, the new
  capture dropped it or took it for its own answer and failed.
- Remote devices (found in a review):
  - A device that connects again after a network drop delivers to the same listeners:
    `remote.receive` and `remote.sync` go on with the new connection, which subscribes again to the
    blocks and sync signals they listen to (before, the new connection had no listeners: a
    `remote.receive` without a duration waited for ever, blocks and the sync output stopped).
  - The card of a remote device offers no *Monitor* box (the device sends its values by itself; its
    *Monitor* box showed an error, and unticking it raised another); `device.monitor` reads the
    latest values as before.
  - `openscilab_device` gives a connection up when openSciLab has been silent for 5 s (it pings
    every second) and connects again - before, a host gone without a word left the device
    *connected* for about two hours, and what it sent was lost; a server that accepts but never
    greets is given up after 10 s, and `stop()` ends that wait at once.
  - A `server` that is no address (`"host:abc"`) is refused by `Device(...)`; a beacon port taken by
    another program is said in `last_error` and tried again (both ended the connection thread for
    good, with an empty `last_error`).
  - `gpio.pulse` on a remote device sends both edges at once with their times: the device times the
    width (before, the second edge was sent after the answer to the first, so a short pulse lasted
    at least a round trip).
  - A message of a device openSciLab cannot use (a `pong` without its times, a block of half a
    sample, a time that is no number) and a sync listener that fails are logged; the connection
    stays (before, it was closed).
  - `stop()` waits for the device's threads to end, and a thread of an earlier `start()` that is
    still connecting ends instead of making a second connection.
  - A sync output whose `write` fails (or a planned output that raises) is logged and the device
    goes on (before, its schedule ended: later outputs never ran).
- Arduino (found in a review; not yet tried on hardware):
  - A buffer capture no longer misses its trigger: an edge between two turns of the wait or among
    the pre-trigger samples of a later turn triggers (a pulse at 1 kHz with 200 pre-trigger samples
    was missed about 4 times in 10). The wait answers at least every 20 ms or so, also at 10 Hz
    (before: 6.4 s, so CAPTURE_ABORT timed out), and the next capture aborts one whose abort was
    not answered instead of finding the board busy.
  - The depth of a buffer capture follows the firmware: a sample takes 1 to 4 bytes by the highest
    pin (Uno: from D8 on 2, Mega: A8–A15 4), not by the number of channels; analog inputs in a
    buffer capture are refused at once.
  - Streams are offered up to the board's sampling rate (HELLO `stream_rate`, 20 kHz on the Uno,
    100 kHz elsewhere), not up to what the line carries (500 kHz on the R4).
  - The square wave goes to the pin with a timer of its own (new pin capability `SQUARE`: Uno D9,
    Mega D11, R4 D9, ESP32 GPIO18) instead of the last PWM pin (D11 at a fixed 490 Hz on the Uno,
    the strapping pin GPIO0 on the ESP32).
  - The arbitrary waveform plays a period in as many points as the DAC's rate allows (100 at
    1 kHz; before always 1000, refused above 100 Hz); a pattern with more runs than the board
    holds (HELLO `gen_runs`: Uno 32, Mega 256, R4 512, ESP32 2048) is refused with that number.
  - A lost DATA frame fails the capture with a message instead of shortening it unnoticed.
  - A CH340 is tried at 115200 and 2 Mbaud (Uno clones and ESP32 boards), the CH9102 of ESP32
    boards is found.
  - Firmware: a pattern or arbitrary waveform whose timer cannot start leaves no outputs behind;
    the latest analog value is written atomically on the AVR boards; a state capture clocked by
    INT0/INT1 takes no frame for an edge from before it started; UART sending below 20 kBd keeps
    interrupts on and the host waits as long as slow transmissions take; the ESP32 refuses PWM
    below 0.5 Hz and the R4 timers below 1 Hz instead of misbehaving; an immediate buffer capture
    has no pre-trigger samples (the trigger mark sat in the wrong place).
- **Rigol DHO900 over USB** (`rigol:usb`, or `rigol:usb:<serial number>` with several): the same SCPI
  through the instrument's USBTMC interface (`driver/usbtmc.py`, with the libusb of the DSLogic),
  found in the device list like the oscilloscopes on the network; after a timeout the request for
  the answer is taken back (`INITIATE_ABORT_BULK_IN`) and a late answer dropped. `rigol-sim:usb`
  simulates it. On Linux it needs `packaging/linux/60-rigol.rules`.
- **Rigol DHO900 tried on a DHO924S** (with the modified firmware NK v0.5.2; over the network, through the bridge app
  and over USB, its generator on CH1 and on the logic analyzers). Before, the driver had only met
  its simulation, which knew the same wrong commands:
  - The logic channels are switched with `:LA:ENABle` and `:LA:DIGital:ENABle D<n>,ON` (the
    commands before do not exist). This firmware's unpatched library does not give out D0–D15
    (`:WAVeform:SOURce D0` is refused, also with the logic probe): a capture of them says so
    instead of failing with "Command execute failed", and through the bridge app instead of
    taking the data of CH1 for D0. D0–D15 still trigger: an edge on a logic channel starts a
    capture of the analog ones. If D0–D15 are given out, they come as 16 bit words
    (`:WAVeform:FORMat WORD`; `BYTE` gives only their upper byte, always 0). Their real sample
    period is twice what `:WAVeform:PREamble?` reports for them alone (measured on a DHO924S from
    20 µs to 10 ms per division of time base, through a known edge's position - its rate and
    trigger sample are corrected, both by that factor): captured together with the analog
    channels, whose rate is not halved, every logic sample is repeated once instead, which lines
    it up with their finer time axis (the DHO reports its preamble, trigger included, as if it
    had been sampled at the analog rate there).
  - **Edge trigger on the analog channels**: CH1–CH4 at a level in volts, rising or falling
    (*Capture settings*, *Edge*: the analog channels below the logic ones, with *Level*). A level
    outside the channel's range on the oscilloscope's screen is refused with the reason (the
    instrument would take another one). The capture settings keep it, and so do capture files.
  - No pattern trigger, no external trigger and no burst mode are offered: the pattern trigger
    takes CH1–CH4 only (the programming guide; refused for D0–D15 also with the probe), the DHO924S
    has no trigger input, and the driver takes one capture per trigger (all three were offered and
    failed).
  - **Front panel** tab on its device card: the oscilloscope's screen live (the H.264 stream of
    its Web Control, some 20 frames a second, decoded with PyAV - a new dependency, `av`; without
    it the screen images, about 5 a second), touched with the mouse as on the instrument, and beside
    it its settings: run, stop, single, force, auto setup; time base and delay; on/off, volts per
    division (◀ ▶ through 1-2-5), offset, coupling, probe and invert of CH1–CH4; source, slope,
    level, sweep, coupling, holdoff of the edge trigger; acquisition mode, averages, memory depth
    and sample rate. Read every two seconds while the tab is shown (not during a capture), on the
    driver's connection (the DHO answers one connection at a time). Without the network (only
    USB) the screen comes over SCPI (`:DISPlay:DATA?`, about two a second, no touches). Settings
    of any instrument can be shown so (`SettingsFacet`).
  - **Web control** on the device card: the instrument's own web page in the browser; over USB at
    the address its bridge app announces.
  - The memory depth: this firmware takes a tenth of the depth named up to `10M` and a twentieth
    above. The driver reads back what each setting gave and keeps the smallest that holds the
    capture (up to `500M`, the 25 Mpts of one channel; `5M` of the programming guide added);
    before, every capture failed.
  - Every sample of the capture arrives, with the trigger in its place: the preamble counts the
    read window, which the read before left behind (a capture after a larger one had its size and
    the wrong pre-trigger samples, after a smaller one a few hundred samples). Driver and bridge
    app open the window over the whole acquisition first.
  - The generator plays what the instrument has: sine, square (duty 5–95 %), triangle and ramp
    (`RAMP` with a symmetry), pulse (as a square wave), noise, DC, up to 25 MHz / 15 MHz / 150 kHz
    and ±2.5 V. Sweep, burst, arbitrary points and too high a frequency (which the instrument cut
    down silently) are refused with the reason; before, the output was switched on even when the
    instrument refused a setting, and an error left from before was blamed on the waveform.
  - A DHO whose `*IDN?` names another maker (this one answers `NK TECHNOLOGIES`) is taken by its
    model; before, it was refused as "no Rigol instrument".
  - Logic channels came out of this firmware with holes (a 1 kHz wave: 553 of its 800 edges, runs of 9299
    samples): the instrument writes only the first half of the points it names for D0-D15, each at twice the
    sample period its preamble reports; the other half is old memory. Only the written half is read now (the
    bridge app too), and captured without analog channels the instrument is set up for twice the rate and
    samples, so the capture has the rate, the samples and the pre-trigger samples that were asked for.
    The bridge app on the instrument has to be updated.
  - Just after a capture the DHO says STOP but answers `:WAVeform:DATA?` with an empty block for a moment: that
    was read as a signal of zeros ("no data"); the driver and the bridge app try again, and a waveform that never
    comes is an error with the bytes it gave.
  - A rate that needs 50 ms/div or more (100 kSa/s over 100k points) is refused with the reason and the
    lowest rate that works: from 50 ms/div the DHO rolls, `:SINGle` leaves its trigger status at STOP and
    the capture only timed out ("did not take the capture up").
  - A capture on the DHO924S (NK v0.5.2) came at a third of the rate asked for (10 MSa/s over 100k points:
    3.1 MSa/s, a 1 MHz wave read as a mess): the instrument spreads the memory over about 16 divisions
    instead of 10 (the logic analyzer on: twice that) and its rates are steps. The driver now reads the
    rate back (``:ACQuire:SRATe?``, right only while the instrument runs) and shortens the time base until
    it is not below the rate asked for; the trigger offset uses the rate the instrument gave.
  - Captured together, the logic channels of the DHO924S came a few samples off the analog ones (up to
    4 samples late at 1.25-6.25 MSa/s, 2 early at 31.25 MSa/s; its own screen shows them in line, aligned in
    the FPGA's display path that the memory export does not go through). The driver and the bridge app move
    them back by a measured table per rate; what remains is the instrument's own variation of about one
    sample from capture to capture. The bridge app on the instrument has to be updated.
  - The bridge app no longer stops the DHO's scope app right after the instrument starts: its `*IDN?`
    came while the scope app was still starting, and the scope app ends itself on that (a firmware bug,
    also in 00.01.05.00.02). The bridge now asks only once the instrument's SCPI port has answered for 30 s.
    The bridge app on the instrument has to be updated.
  - A failed setup ends the capture with its reason (a trigger level, a depth the instrument
    does not have) instead of "Device reported an error starting the capture. Restart the device".
  - USB: no more `INITIATE_CLEAR` after a timeout; on the DHO it stopped the instrument from
    reading commands over USB until it was restarted. What an earlier program left unread is
    dropped when the interface is opened.
  - Bridge app: it runs on the DHO's Android 7.1.2 (it asked for Android 9 and did not install);
    it starts again after a reboot (the setting is written at once, not in the background). When
    the DHO closed the app's connection to its SCPI server (it does so when another program
    connects to it or leaves), the next command was lost and the next query answered
    "ERR instrument: the instrument closed the connection"; the app now notices it and connects
    again first.
  - A connection closed while the samples of a capture still arrived (closing the device during
    the transfer) ended the transfer thread with a traceback instead of stopping it.
- Rigol DHO900 (found in a review):
  - A capture without trigger is forced (`:TFORce`) once the oscilloscope waits: `:SINGle` makes
    the sweep SINGLE, so before it waited for a trigger that might never come (up to an hour).
  - A capture counts as done only after the oscilloscope showed that it took it up (`RUN`, `WAIT`,
    `TD`) and then `STOP`; before, a `STOP` read 50 ms after `:SINGle` could hand over the capture
    before. One it never takes up fails after 10 s with what the instrument reported.
  - After a query timed out the connection gets back in step (`*IDN?`, answers up to it dropped):
    before, the late answer was taken for the next query's, and every answer after it was one
    behind.
  - Stopping a capture while the bridge app reads it returns at once instead of waiting for the
    connection (up to 10 minutes, the window froze; closing the device as well), and a stopped
    capture is no longer reported as a capture. A new capture is refused as busy until the
    reading has ended.
  - Only the models the driver knows are taken: DHO914, DHO914S, DHO924, DHO924S, with the
    generator only on the S models; a DHO800 (no logic analyzer) or another Rigol is refused with
    a message saying so. Before, every Rigol had 16 logic channels, 4 analog ones and a generator.
  - The bridge app reads only the logic channels of the capture (`:BRIDge:SNAPshot D0,D3,CH2`):
    before, it was asked for D0-D15, whose channels the driver had switched off.
  - Logic and analog channels sampled at different rates (or with the trigger elsewhere) are
    refused with a clear message, directly and by the bridge app, instead of being shown out of
    step with the rate of one of them.
  - Bridge app: an `:OVERview?` or `:TILE?` that fails is answered with `ERR` before the block
    starts (a tile is encoded first); one that breaks off after its header closes the connection,
    where it used to put `ERR` into the block and leave the client out of step.
  - Analog samples stay 8 bit (`BYTE`, the top 8 of the 12 ADC bits); documented in
    `docs/protocols.md`.

### Firmware

Found on a Pico 2 with firmware 8 (the first test on hardware):

- **The edge trigger waits for an edge**: a capture whose trigger channel was already at the
  trigger level started at once, in the middle of the level, with pre-trigger samples that were
  never recorded; between the bursts of a capture with a loop count the next burst started at once
  as well (200 ns after the one before instead of at the next edge). The trigger channel now has to
  be at the other level first, as the simulators always had it.
- **The pattern generator below 50 MHz** put out its own timing value as the first sample and then
  hung (endless) or ran through the pattern in a few cycles (a count of passes): with autopull a
  `PULL` does nothing while the OSR is full, so the delay per sample never arrived.
- **Low pulses** (`PULSE` with level 0) came out as high pulses, and **SPI modes 2 and 3** ran
  their clock idle low: the inversion of the pin was set before `pio_gpio_init`, which clears it.
- **Pull-downs on the RP2350** (Pico 2, Pico 2 W): erratum E9 keeps a pin that was high at about
  2 V against the pull-down, so the self-test reported every channel `STUCK_HIGH` and skipped the
  capture tests, and an input with pull-down read 1. The pin is now pulled down with its input
  buffer disabled for a moment (`PIN_MODE` input with pull-down, the self-test).
- Found in a review (not tried on hardware yet: the test board drives no outputs):
  - The watchdog counts from the end of a capture's transfer: a capture that took long to send
    (always over WiFi) released the outputs at once afterwards.
  - A fast-trigger capture on an RP2040 board no longer turns the pins of the pattern generator and
    the pulses into inputs (they run on the same PIO block).
  - The blast trigger waits for an edge too (a trigger level at the start does not trigger).
  - A blast capture stopped before its end aborts its DMA channel: the next owner of the channel
    resumed the old transfer.
  - A low pulse, SPI in modes 2 and 3 and UART end without a glitch: the pin no longer dips or floats
    while it becomes an ordinary output.
- **An image for the LogicAnalyzer board V2 of gusmanb** (`BOARD_LOGICANALYZER_V2`, with a
  Pico 2; the board of his firmware 6.0): level shifters drive its 24 channels, so they are inputs
  only - the firmware refuses outputs, pulls, PWM, pulses, the pattern generator, the transmitters
  and the ADC on them, and the board reports none of these functions. Its self-test checks that the
  shifters are supplied (the VREF switch on *ext* without a voltage switches them off, and the
  channels read nothing). A board that runs it is always updated with its own image. The image of
  a bare Pico or Pico 2 warns in its self-test when it looks like such a board (GP0/GP1 pulled to
  GND by the board's trigger chain, or every channel driven from outside).

## [0.1.0b1] - 2026-10-06

The first release of **openSciLab**, the open measurement and control lab that PiPiLogicAnalyzer
has become - a beta: every function works with the simulators and is covered by about 2000 tests,
but much of the new firmware has not been checked on real hardware yet.

**Highlights**

- **A lab instead of one instrument**: several instruments at once, each with a device card -
  Pico boards (firmware protocol 8 with GPIO, PWM, ADC, pattern generator and UART/SPI/I²C),
  Arduino boards with a firmware of their own, DSLogic analyzers, the Rigol DHO900 oscilloscope
  (with a bridge app), measuring devices on other computers (`openscilab_device`) and simulators
  of all of them.
- **Flows**: a visual editor (and a Python DSL) for `*.flow.yaml` - devices, GPIO, generators,
  every sigrok decoder, measurements, signal processing, control, data, views and reports; real
  time or deterministic virtual time; every node type has an example.
- **Panels** as the front panel of a measuring station, **reports** in HTML or PDF with checks,
  and `openscilab run` to use a flow as a test.
- **Time**: the samples of different instruments on one time axis (sync signals, latency
  measurement, drift, NTP/PTP).
- **The logic analyzer** of PiPiLogicAnalyzer as the data view, with streams, analog and state
  captures, trigger sequences and large captures arriving progressively.
- **A new window**: start page with every example project as a starting point, projects,
  templates, sidebar, command search, settings.
- **The repository is now `deckerjulian/openSciLab`**, with a new wiki as the user guide.

The details follow.

### Application

- `openscilab run <name>` in a project folder runs the project's flow of that name, as the
  command line documentation says; `openscilab --debug-driver` also logs the drivers that run in a
  device process of their own.
- Only the simulated boards with the openSciLab Arduino firmware are offered with the Arduino
  protocol (the simulated DAQ no longer shows up among them); messages and help texts name the
  current menus.

- **Every node checked, every node in an example.** A review of all node types and simulators
  found and fixed these; a test now fails when a node type has no example and when an example's
  flow has any warning (a misspelt parameter was silently ignored before):
  - *Streams*: `convert.edges` and `dsp.debounce` no longer lose edges and short pulses where two
    blocks meet; `dsp.resample` keeps its sample grid across blocks; `dsp.math` combines the
    signals at their common times (blocks of a stream once, not block k with block k-1);
    `measure.count` keeps each wire apart. FIR filters and moving averages are placed without
    their delay; empty blocks no longer give two samples; impossible cutoffs are refused.
  - *Numbers*: measurements no longer round the sample rate to whole hertz; `measure.setup_hold`
    pairs data and clock by time; `dsp.integral` starts at 0 (trapezoids), `dsp.derivative` is the
    same streamed and whole; the FFT no longer doubles the Nyquist line; `min(a)` and `max(a)` work
    in `dsp.math`, which keeps the unit and makes a comparison of signals digital.
  - *Units*: `control.compare`, `control.limit`, `dsp.threshold` and `control.sweep` take
    quantities in the unit of the signal (`20 mA` against a reading in mA, `3.3 V`, a tolerance of
    `50 mV`); a missing value to compare with is an error.
  - *State machine*: values that came before a state was entered no longer lead out of it, and
    values for other states no longer hold up their sender; a way out without `after` or `input`
    is refused. A wired `start` of a sequence runs it for every value; waiting for an input
    without a wire is refused.
  - *Devices*: unknown pins are named (`uno has no pin 'd7' - D7?`); a refused capture says the
    device's limits (`the rate is at most 100 kHz`); `pre` must be fewer than `samples`; levels
    may be `high`/`low`, a duty cycle `50 %`, an I²C address `0x48`; `remote.set` reads `false`
    as false; channels named CH1 fit digital and analog inputs.
  - *Generators*: `gen.arbitrary` plays a CSV file or a signal at their own rate; `gen.tx_*`
    send the `data` parameter for an event without data and say `done` when the transmission is
    through; `gen.output` plays an `.sdl` file on a pin and rate of your choice; a square-only
    output gets a square over its whole range by default.
  - *Decoders*: `text` takes the first data row that has annotations (only MOSI wired: its data)
    and of I²C only the data bytes.
  - *Data, views, reports*: bundles are written as numbers, not Python text; `data.file` writes
    signals as captures, bundles as CSV, refuses a table as a capture file, and its numbered files
    go on after those already there (`data.file_write`, a duplicate of it, is gone);
    `data.logger` writes only the new rows of a sliding table, one line per column, and keeps
    analog samples too; `data.buffer` keeps when its values came. `view.scope` joins only blocks
    that follow each other; `view.strip_chart` draws every channel of a capture and events at
    their times; `view.number` and `view.led` show signals and texts like `on`; diagrams of a
    report skip values that are no numbers; `structure.unbundle` finds numbered fields.
  - *Flows*: an unknown parameter is named with a suggestion (`has no parameter 'level' - did you
    mean 'threshold'?`); errors no longer repeat the node's name; a conversion inserted on a wire
    gets its parameters (a number to a truth value: at or above 0.5).
- **Captures and streams in virtual time see what the flow does meanwhile**: the flow's time runs
  on while a simulator's capture waits for its trigger, so a stimulus after `armed` - also after a
  wait - is captured, and a stream ends where its `stop` came. Two examples that had to run in real
  time no longer do; captures of USB devices (Pico, DAQ) have the flow's time in virtual time too.
- **State mode in flows**: `device.capture` with a `clock` channel takes one sample per clock edge
  and sends them at `states` (`convert.state_bit` takes a channel of them); a pattern trigger may
  have don't-care bits (`1x11`).
- **The simulators behave like the devices**: the Uno's captures hold 448 samples up to 100 kHz
  (pins only, at most 2 s after the trigger) and it streams 11.5 kB/s, as its firmware; the Uno R4,
  the Pico (800 kB/s) and the DAQ (20 kS/s for all inputs) likewise; the oscilloscope and a
  multi-board set no longer offer a stream. A generator that stops keeps its time in the signal
  (a capture across the stop sees both parts); *Safe*, a restart and the watchdog stop generators
  and analog outputs too; pull resistors no longer stick; an RC low pass follows a generator or a
  DAC; sources swing to the board's logic level (5 V on an Uno); the Arduino board stays free
  after a stopped capture; the state mode waits for its trigger; an edge right at the start of a
  capture counts. The simulated DAQ on the network measures a 50 Hz sine on AI0 as its demo does.
- **New examples**: *State mode*, *Signal processing* (derivative, integral, resampling, averaging,
  logic to volts), *Send SPI*, *Waveform file*, *Replay a capture*; *Bundle* takes its bundle apart
  again.
- Fixed: questions and messages had a large empty space left of their text (the icon took the
  width meant for the text); the question when closing names a temporary project properly.
- **Edit a node in a window of its own**: a double-click on a node opens its parameters in a
  window (a double-click on the data shown in it still opens them in a view). Mappings and long
  lists - the states of a state machine, the signals of a simulator - get a big text field in YAML,
  one entry a line, in the inspector too.
- Fixed: YAML read `on:` and `off` as true and false (YAML 1.1), so the transitions of a state
  machine were lost and the *Traffic light* example stopped in its first state. Flows, panels and
  projects are read as YAML 1.2 now (only `true` and `false` are truth values); a state machine
  names keys it does not know and states it cannot go to.
- **A tidier window.** The command search sits right after the logo; *Examples* is called
  *Templates* (menu and header). Tabs have their close button on the left. The node palette is no
  sidebar section any more: it shows beside a flow whose graph is edited (*View → Node palette*,
  Ctrl+Shift+N). The activity bar has the start page and the settings at the bottom and is
  arranged with a right click (show, hide, order, names under the icons); the parts of the
  sidebar sections (open documents, project, data, recent files, connected and available devices)
  open and close with a click on their header and show how many entries they hold. The overview
  of a flow draws its nodes as blocks in the colours of their kinds, with the visible part outlined.
- The rows of a data view start at the top with their height instead of stretching to fill the
  view; the overview below the waveform shows analog channels too (as an envelope) and fast
  digital signals clearly.
- **Nodes of a flow look the part**: a title bar in the colour of their kind (devices, measurements,
  views, data, timing, ...) with an icon, a soft shadow, port names brighter when wired, parameters
  as a small table. Wires back (feedback, e.g. to the next step of a sweep) run round below the
  nodes instead of through them.
- **Better arranging, and Auto-arrange**: *Arrange* (now in the tool bar of a flow) lines wires up
  port to port, routes long wires past the columns between and orders the nodes of a column with
  as few crossings as it finds; *Auto* arranges the flow again whenever nodes or wires are added or
  removed. Every example and template is arranged anew (none overlaps).
- Fixed: the capture settings opened from the tool bar of a data view showed on a white background.
- **Every example is a starting point; the start page shows them all.** The start page lists every
  project of the example library as a tile, by category, scrollable and with a search; a click opens
  it as a temporary project. The first category, *Start a project*, holds the plain starting points:
  *Empty lab*, *Logic Analyzer* (now an ordinary project that opens a data view with the connected
  device: `open: [data]`), *Data acquisition* (analog inputs of a measuring box recorded until Stop,
  strip charts, a file, an output set from the panel), *Synchronized instruments* (a logic analyzer
  and a DAQ with a drifting clock aligned by a sync signal) and *Remote measuring device* (a device on
  another computer with the script to run there). *Characteristic curve*, *Test bench* and
  *Long-term logger* are examples of *Measurement* and *Data and reports*. *Project → New project…*
  shows the start page; there are no separate templates any more.
- **Streams of analog inputs in flows**: `device.stream` hands on analog channels as blocks of
  volts (`channels: [AI0, AI1]`), also without digital channels; analog-only streams of the
  simulators no longer fail. Inputs named `AI0` or `AIN0` are analog ports.
- **A local DAQ simulator** (`sim:daq`, *Simulation: USB DAQ*): a measuring box on USB in the class
  of an NI USB-6001 - eight analog inputs, two analog outputs, twelve digital lines - whose blocks
  arrive as over USB and whose sample clock runs 30 ppm fast, with a digital loopback (P0.0 → P0.1)
  for the latency, AO0 wired to AI2 and P0.7 free for a sync signal. Simulator profiles take a
  `clock_drift` (ppm); *Signals → USB link and clock* changes it.
- **Smaller text and tighter controls by default**: 10 px text; padding, icons and the rows of the
  waveform follow the text size (*Settings → Appearance*; 12 px gives the earlier spacing).
- **Simulated remote devices with the timings to try.** The device list has the remote DAQ with its
  own drifting clock, with a clock that follows UTC (as with PTP) and with a shared sample clock;
  the *Simulation: timing* box on its *Remote* tab changes the network's latency and jitter and the
  drift of its clock while it runs, and starts it again with another clock. The address keeps the
  settings (`remote-sim:daq?latency=20&jitter=2&drift=300`).
- **A higher priority for reading devices on macOS and Linux** (*Settings → Devices*, off by
  default): openSciLab asks when it starts and raises itself and its device processes with the
  password dialog of the system; on Linux it can be allowed for good. Windows needs no rights.
- Fixed: a black box in the top left corner of some device cards (an empty event list).
- **A Timing tab on the device card.** How the samples of the device are placed and how well (now
  also for captures of the data view, not only of flows), its **sample clock** - own, or shared /
  external, which `timing.align` (new default `drift: auto`) follows -, **Measure** the latency
  with a loopback right there (an output wired to a channel, rate, stream or capture; kept like
  that of `timing.calibrate`), the measured latencies, a **sync output** on a pin (the signal of
  `timing.sync` until stopped) and this computer's NTP/PTP clock.
- **Wires and the USB link of a simulator** on its *Signals* tab: *Add wire…* connects a pin to a
  channel (*Wire them* on the Timing tab adds the loopback at once), *USB link* emulates latency,
  jitter and frames (or not); both are kept per simulator. In real time a simulated capture
  records what the outputs do until it ends, as the device does.
- **The device card has everything about a device in one place.** The extra *Device information*
  window is gone: the *Details* tab shows the device's functions (capture settings, self-test,
  network, bootloader, firmware, *Reconnect*, *Copy the details*), the capture limits, the
  capabilities, board, firmware and connection. New there: *Restart device* for devices that report
  `RESTART` (the simulators; the Pico firmware has no restart command yet - *Reconnect* restarts an
  Arduino, *Restart into the bootloader* a Pico). **Profiles** are in the card's header (load,
  save, standard profiles), which also names the settings of the next capture.
- **Disconnect all devices** at once (*Devices* menu, the button above the connected devices):
  one question names every device and what disconnecting it interrupts.
- **A fresh start when nothing was saved.** The session brings back saved files and the project;
  device cards, data views of devices and the connected hardware no longer come back, and devices
  are only connected again when *Settings → Startup* says so (off by default).
- **Views of a flow's data do not offer to capture.** The data view of *Run data* and of a scope
  node shows search, measure, panels and save - no capture bar, no device, no *Open*.
- **The simulators are one group of the device list again**: also the simulated Arduinos and
  oscilloscopes that speak the real protocols. They are no longer listed as connected hardware.
- **Connected hardware says how firmware gets onto a board**: three steps above the list (plug in,
  with BOOTSEL for a board without firmware; find it; *Install firmware* in its row).
- **Tabs look alike everywhere** (console, device card, dialogs, analysis panels): flat, with an
  accent line under the current one; the white line under the tabs is gone.
- **Plugins add devices without changing openSciLab.** A `.py` file or package in the `plugins`
  folder of the settings directory (or a folder of `OPENSCILAB_PLUGINS`, or an installed package
  with an `openscilab.plugins` entry point) registers a kind of device with its driver:
  `kinds.register("mydevice", open_mydevice, detect=..., process=True)`. The device list then shows
  its devices, flows open it with `address: mydevice:...`, and so do `openscilab-cli` and
  `api.open()`; with `process=True` it is read in a device process of its own, *Simulate* uses the
  simulator profile it names. A plugin with user interface of its own (a backend that asks for an
  address) brings it in `setup_ui()`. A plugin that fails is reported (status bar, *Help →
  Plugins…*, `openscilab-cli plugins`), the others load. `examples/plugins/counter_device.py` is a
  complete example; see `docs/drivers.md`. An address of an unknown kind (`foo:...`) now says so
  instead of being tried as a Pico.
- **Tool bars that fit the window and can be arranged.** The header, flow, panel and data view bars
  (including the view bar above the tracks) give way when the window is narrow: the less important
  entries first show only their icon, then move into a *More* menu (`⋯`) at the end of the bar,
  where they work as in the bar (check boxes, device and other choices, settings menus). Capture and
  Run always stay. A right click on a bar opens *Customize toolbar…* (show, hide and order entries,
  some extra ones such as the align tools of a flow), the display (*Automatic*, *Icons only*, *Icons
  and text*) and *Reset toolbar*; the arrangement is kept per bar. The data view's two bars became
  one, its view bar is a tool bar as well, so a data view fits narrower windows. Buttons have a flat
  tool look with clear hover and checked states.
- **Devices on USB are read in a process of their own**, so nothing the application does - drawing,
  flows, scripts, its garbage collection - can make a stream overflow (the Pico firmware's ring lasts
  22 ms at 6 MB/s). A Pico, Arduino or DSLogic runs in a *device process*: its driver and facets live
  there, its samples come through shared memory, progress and completion come back in order (a busy
  application lags behind, the reading never waits). The device process stamps the start command and
  every block, which places the samples more closely. A device process that dies fails its capture
  with a message and disconnects the instrument; one whose application went away stops the device.
  On by default (*Settings → Devices*); simulators, remote devices and the Rigol stay in the
  application, the Python API opens in a device process with `process=True`. Also: drivers hand
  their events to a notifier thread of their own, the startup heap is frozen for the garbage
  collector, serial ports get a large receive buffer (Windows). The simulated Pico emulates the
  firmware's ring: `tools/benchmark.py stream` shows its stream overflowing under load in the
  application and not in a device process.
- **Decoding never stalls recording and synchronisation.** The protocol decoders run in a process
  of their own (started once, the samples through shared memory, the annotations back in small
  parts); a long decode in the application used to make the threads that receive a stream or a
  remote device wait up to about 100 ms, now as long as when nothing else runs. Cancelling ends it
  at once; a decoder process that died is started again. Annotation segments are tuples the
  garbage collector stops looking at (no long pauses after a large decode). The sync path is
  vectorized: pairing edges (15x), finding the sample of a time (500x), analog sync inputs (5x,
  and noise no longer makes edges before the signal shows its levels). `tools/benchmark.py`
  measures all of it.
- **The time of samples, for every device** ([docs/timing.md](docs/timing.md)). Captures and
  streams of openSciLab's own instruments are placed between the command that started them and the
  arrival of their data (the lower envelope of the arrivals removes the jitter of USB) instead of at
  the moment the flow asked; the device card shows the method and the accuracy (*Timing*). Better:
  - **a sync signal from openSciLab**: `timing.sync` drives a pin with edges at irregular intervals;
    `timing.align` places an instrument on a reference's time by it (offset and drift, or only the
    offset for a shared sample clock), `remote.sync` a remote device - also one that records the
    signal on an input of its own (a spare channel of a DAQ), with the edges placed again with all
    its arrivals. Accuracy: a sample period of the slower recording.
  - **a measured latency**: `timing.calibrate` switches an output wired back to an input and keeps
    the latency per instrument and rate; later captures and streams use it without the wire.
    Device scripts do the same (`input.calibrate()`, `latency=`) and stamp their blocks from when
    they arrive (`input.start()`).
  - **a time scale (PTP, GPS, NTP)**: a device whose clock follows UTC or TAI is converted without
    measuring when this computer's clock follows it too (*Settings → Time*); drivers of local
    instruments that stamp their samples report it the same way.
  - The simulated Pico emulates USB (frames, latency, jitter), so all of it works without hardware;
    `remote-sim:daq` is a DAQ behind USB on another computer (`?shared_clock=1`, `?timescale=utc`);
    `examples/remote/ni_daq.py` is a template for a real NI DAQ; four examples in the category
    *Time*.
- **Standard profiles for the common buses**, loaded without importing: every profile file in the
  new profiles folder (`profiles/` in the settings directory, *Profiles → Open the profiles
  folder*) is there at once. openSciLab puts its standard profiles into it – UART, MIDI, DMX512,
  LIN, I²C, SPI, 1-Wire, CAN, PWM, WS2812, I²S, parallel, SWD, JTAG, IR (NEC), USB and the C64
  expansion port (moved from `examples/c64-expansion-port-profile.json` to
  `examples/profiles/c64-expansion-port.json`) – each with its channels named, a trigger, the
  decoders and the wiring in its notes. *Profiles → Standard profiles* loads one with a click; a
  profile needing more channels than the device has is off (with the reason), and rate and length
  are fitted to the device when a profile is loaded. Profiles from the folder are edited and
  deleted in their own file; a deleted standard profile stays deleted.
- **Flows and their panels find each other.** The flow editor's *Panel* button has a menu of the
  panels that use the flow (open ones and the panel files of the project or the flow's folder) and
  *New panel for this flow*; the flow's inspector lists them too. A panel opens its flow with
  *Open flow* (tool bar and inspector).
- **The simulators of the device list are one group** that opens and closes with a click (it
  remembers how it was left).
- The example library is part of the packaged application (the *Examples* menu was empty there).
- **Capturing happens in the data view.** Its capture tool bar chooses the device (connected
  instruments, or *Connect a device…*) and holds *Capture* (`F5`, also the header's button),
  *Again*, *Stop*, the quick settings, *Settings…* and *Profiles*; the *Capture* tab shows the
  next capture's settings and channels. The template *Logic Analyzer* asks which connected device
  to use. The device card no longer captures: *Capture…* opens its data view, and the functions of
  the device (information, self-test, test signals, network, bootloader, firmware) are buttons on
  its *Details* tab instead of a *Device* menu.
- **A tool bar instead of the device names.** The top bar starts with the new logo of openSciLab
  (a lab flask holding a digital signal; also the application icon) and a button for each basic
  element – *Flow*, *Panel*, *Data view*, *Waveform*, *Project* (from a template) – then *Open*,
  *Save* and *Examples*; the connected devices are in the device list and the status bar.
- **The selection on the time axis shows over every track** (digital, analog, pinned, buses), and
  *Fit selection* (view bar, the ruler's menu, `Ctrl+E`) shows exactly the selected samples.
- **Time axis and tracks line up again:** with annotation rows (or many channels) the ticks of the
  time axis drifted away from the lines of the tracks by the width of the tracks' scroll bar.
- **Fixes of the window:** menus that open from a full tool bar (or from its buttons) and the scroll
  buttons of the document tabs were transparent; the tabs drew stray white lines and cut names to
  a few letters; on macOS the menus of the application (Examples, Devices, …) were replaced by those
  of a data view once it had the focus.
- **Remote devices over IP.** A Python script on another computer becomes a device: with the
  package `openscilab_device` (standard library only, Python 3.8+) it describes its inputs (numbers,
  truth values, sampled signals, events), outputs, commands and sync signals and connects to
  openSciLab (*Settings → Remote devices*, with a token; a beacon lets devices find openSciLab).
  The device appears in the device list with a *Remote* tab (latest values, outputs, commands, how
  well its clock is known) and in flows as `remote:<name>`: `remote.receive`, `remote.set`,
  `remote.call`, `remote.sync`; `gpio.write`, `gpio.read` and `device.monitor` work with it too.
  **Time stays right by itself:** values are stamped on the device; openSciLab measures offset and
  drift of its clock all the time (a fraction of a millisecond on a wired network), outputs are sent
  ahead with the time they should happen, values of several devices are handed on in the order of
  their time. **A sync signal** (an output of the device that a logic analyzer records too) aligns
  it to microseconds. Demo devices (`python -m openscilab_device.demo climate`), simulated remote
  devices (`remote-sim:climate`, `audio`, `echo`) behind an emulated network, four examples, a
  Raspberry Pi template and `docs/remote.md` (with the protocol).
- **Examples menu with a library of 63 examples**, sorted by topic like the examples of the Arduino
  IDE: Basics, Digital I/O, Analog, Protocols, Measurement, Signal generation, Control, Data and
  reports, Panels, Python and Advanced. Each one is a small project with simulated devices (logic
  analyzer, Arduino Uno, Pico, oscilloscope with function generator) that runs without hardware
  and explains itself; it opens as a copy, so the library stays as it is. The examples are also in
  the command palette; `examples/library/README.md` lists them.
- **Fixes found with the examples:** a counter missed the edges between the blocks of a stream
  (200 ms of 1 kHz counted 180); a buffer joins the captures of a stream (it kept only single
  channels); channels with a dot in their name (`B2.GP5` of a multi device) can be wired; a
  simulated capture in virtual time sees outputs the flow sets after it was armed (a pulse train);
  values read better on wires and in logs (the value of a single event, bytes in hex, the fields of
  a bundle, numbers without a unit without an SI prefix).
- **The data of a run in the graph and in data views.** While and after a flow runs, its nodes
  show their data below their parameters: a trace of the latest capture, the latest value with its
  course, the rows of a table; a view node shows what arrives at it. Double-click a node (or *Show
  data* in its menu, or the corner of its data) to open its data in a data view – a table opens as
  a table. *Run data* in the flow's toolbar shows all data of the run in one data view on one time
  axis: the channels of every capture, sampled signals, measurements as lines that step from value
  to value, checks as digital lines. Open data views follow a running flow and the next run.
  *Show data in the nodes* and *Clear run data* are in the Flow menu. Nodes keep room for their
  data, so an arranged flow stays in order when data arrives.
- **Flows are arranged along their wires.** Nodes without a place are laid out from left to right
  in columns, with their real height (no more nodes on top of each other) and with feedback wires
  (a sweep waiting for its measurement) not turning the flow around. *Arrange* (Flow menu,
  `Ctrl+Shift+L`) does it for the selected nodes or the whole flow, as one undo step.
- **The inspector offers what the device has.** Pins come as a list of the pins that can do what
  the node needs (PWM pins for a PWM node, outputs for *Write pin*, reserved pins left out),
  channels and analog inputs are ticked in a menu, generator outputs are chosen from the device's
  outputs. A decoder's channels are chosen one by one from the channels of the capture wired to
  it; a report image offers the columns of its table. The values come from the instruments open in
  the device list and from the simulator profiles.
- **Completions while typing YAML and Python.** The YAML view of a flow offers the sections, node
  types after `type:`, the parameters of a node's type that are not set yet, the values a
  parameter can have (choices, the pins and channels of the device – a node typed just now uses the
  flow's only device) and the ports in the edges (outputs before `->`, inputs after it). The code
  of a Python node is highlighted and completes `ctx.`, `np.`, `signals.`, `units.`, the names of
  the code and its port names in `ctx.emit("…")`, `ctx.receive("…")` and `port == "…"`. The Python
  console completes the attributes of its names. The list opens by itself or with `Ctrl+Space`;
  `Enter` or `Tab` takes a completion.
- **Values that do not fit are found while editing.** A word where a number belongs, a frequency
  given in seconds, a choice that does not exist, a pin the device does not have or that cannot do
  PWM, a channel the capture does not record: each is a warning at its node, in the inspector and
  in *Problems* (the flow still runs – a project may use another device).
- **A panel from the graph.** *Panel* in the flow's toolbar (and the Flow menu) makes a front
  panel for the flow – also for the unsaved flow the application starts with, which the panel then
  uses as it is edited; saving the panel saves the flow first. A new panel starts with suggested
  widgets: a start button of a sweep, a slider for the duty cycle of a PWM, inputs the flow needs,
  numbers and LEDs for measurements and checks, scopes and charts for what the flow shows.
- **The template flows are laid out and the test bench passes.** Every node of the templates has
  its place; the test bench captures long enough to find the UART text wherever the capture
  starts (it failed in real time) and writes its report when the flow ends, with both checks. The
  empty template shows its device as a node.
- **Pico boards with firmware 8 are full instruments.** The device card, flows, scripts and the
  command line get their pins (the board's pin table, reserved pins with the reason), outputs,
  pulses and pulse trains timed by the board, PWM with the frequency the board makes, the monitor,
  analog inputs A0–A2 in captures and streams, the pattern generator, the square wave of GP22,
  UART/SPI/I²C sent by the board and the live state stream. A pulse can be triggered while a
  capture waits for its trigger on the same board. A loaded pattern and analog channels share the
  capture memory, so the capture settings offer fewer samples then.
- **Arduino boards** (`arduino:<port>`) with the openSciLab Arduino firmware: Uno, Nano, Mega 2560,
  Uno R4 Minima, ESP32 and ESP32-S3. They are found by their USB identifiers and offer stream,
  buffer and live state captures with analog channels, GPIO, the monitor, the pattern generator,
  square and arbitrary waveforms (DAC on the R4 and the ESP32) and UART/SPI/I²C. A simulated board
  speaks the same protocol (`arduino-sim:uno`, *Simulation: Arduino Uno (firmware protocol)*).
- **Rigol DHO900 oscilloscopes** (`rigol:<address>`): D0–D15 and CH1–CH4 in one capture with edge,
  pattern or no trigger and adjustable logic thresholds, and the built-in generator from the
  waveform document (standard waveforms, arbitrary points, sweep, burst). With the openSciLab
  bridge app on the instrument a large capture is shown at once and its samples follow tile by
  tile, the visible range first; an interrupted transfer resumes; the *Cache* tab of the device card
  lists the captures the app keeps. Oscilloscopes with the app appear in the device list by
  themselves. Without the app the driver reads the instrument directly, more slowly.
  `rigol-sim:bridge` and `rigol-sim:direct` simulate one. The SCPI commands are provisional until
  checked on an instrument.
- `tools/dho_la_probe.py` records what a DHO900 answers to the commands the driver needs.
- Pulses take a count and a period (device card, flow node `gpio.pulse`, simulators).

- **Outputs stay as they were set.** The driver keeps a device's watchdog satisfied while it is
  connected – not the device card: an output a flow or a script sets no longer falls back to an
  input after a second (it did on the simulated Arduino). Disconnecting asks while any pin is an
  output, whoever set it.
- **Sending UART, SPI and I²C with the device's own hardware.** Devices that can (capabilities
  `TX_UART`, `TX_SPI`, `TX_I2C`) send through the generator facet and give back what a target
  answers: the bytes on MISO, or the bytes an I²C device returns after a write. The nodes
  *Send UART/SPI/I²C* use it (new output *received*, I²C parameter *read*, SPI parameter *miso*)
  and play a pattern on devices without it; the device card has a new tab *Send*. The simulated
  Pico, Uno and Uno R4 can, with a temperature sensor at I²C address 0x48; the Uno got a pattern
  output (D2–D7) and a square wave output (D9).
- Devices get their facets (GPIO, monitor, analog inputs, generator) from what their driver
  offers, the same way in the device list, in flows, on the command line and in scripts.
- **A second check of the review.** Saving a capture file keeps the form it has (a file of
  the original software was silently packed by *Save*). The dialog shown while a file is
  written can no longer be closed (closing it ended in an error and left the document
  "unsaved"); data edited or captured while a file is being written stays marked as unsaved.
  Files are written through links, keep their permissions, respect write protection and may
  have long names again. A flow gives the Ctrl+C handler of the program around it back. A
  decoder channel assigned to a capture channel that a profile or the capture does not have is
  kept instead of being dropped by saving the profile or pressing *OK* in the decoder options.
- **Command line and smaller corrections.**
  - `capture` checks the ending of the output file and its options before it captures (a
    capture was thrown away for `-o x.txt`). Errors of damaged files, flows that cannot be
    read and devices are one line instead of a traceback (`OPENSCILAB_TRACEBACK=1` shows it).
  - **`capture --duration 5m` is 5 milliseconds now**, as in flows and `run --duration`
    (it was 5 minutes there and 5 ms here); minutes are `5 min`.
  - Analog to digital with hysteresis is computed without a loop (2 million noisy samples:
    0.6 s → a few milliseconds).
  - Patterns (SDL): `//` inside a string is no comment, `\\n` is a backslash and an n, and a
    repeat count that would make a pattern longer than 64 million samples is refused instead
    of hanging.
  - Removed what could not be reached or was not used: the help menu of a data view (the
    window has it), a few helper functions.
- **Scripts and the command line without the user interface.** No module of the core, the
  drivers or the lab loads Qt any more (the colours of the waveform moved to the user interface,
  a region holds its colour as four numbers): `import openscilab.api` takes 110 ms instead of
  195 ms, and the lab is loaded when a script first uses it.
- **Start-up.** The theme *Follow the system* asks the application for the colour scheme (it
  was always dark on Linux and started a helper program on macOS); the style sheet is built
  and applied once instead of three times. Restarting after a change of the theme opens the
  documents again (the session was saved a second time, empty).
- **Drivers: what happens when two things happen at once** (checked against simulated
  transports, not on hardware):
  - Pico: asking a board for its capabilities or details while a capture starts no longer
    writes the question into the capture and reads samples as the answer; a stopped capture
    leaves no reader behind that could take the first bytes of the next one. A set of boards
    sends the bootloader command to every board (it stopped at the first that did not answer)
    and reports the result of a capture without holding its lock.
  - DSLogic: every capture has its own stop flag, so a capture started right after one was
    stopped is neither read nor ended by the reader of the old one. A stream the computer
    cannot keep up with ends with a message instead of filling the memory.
  - Software trigger: a second capture started while one waits for its trigger is refused
    without taking away the watch of the first (its endless stream would never have stopped);
    analog channels with a rate of their own are cut at the right place.
  - Simulator: stopping a capture no longer interrupts the transfer of the capture before it.
  - Scripts: `Device.capture` waits until all samples of a capture that arrives piece by piece
    (an oscilloscope over the network) are there; saving or decoding it at once saw zeros.
  - The window no longer stutters every few seconds while the serial ports are listed (read in
    a thread now), and writing a firmware with avrdude, bossac, esptool or adb no longer
    freezes it for minutes.
- **The data view stays quick with large captures** (measured with 20 million samples on 24
  channels and 47,000 annotations):
  - A change of the data – an edit, an undo, a renamed channel – blocked the window for 2.1 s;
    now 20 ms. Moving a channel: 2.2 s → 60 ms. The index of the edges and the values of the
    buses are kept for the channels whose samples did not change, the rows of the channel
    column are updated instead of built again, and the statistics of the *Measure* tab are
    read from the edge index, only while the tab is shown.
  - Annotation rows with more entries than pixels are drawn as blocks: 640 ms → 5 ms per
    picture, moving a cursor over them 65 ms → 3 ms. An entry that began far before the view
    (a frame around many bytes) is drawn; it could be missing.
  - Bus rows zoomed out: 245 ms → 7 ms per picture, pixel for pixel the same.
  - Dragging a cursor no longer computes minimum, maximum and RMS of the analog channels over
    the whole range at every step.
  - The search runs in the background and can be cancelled; the filter of a long annotation
    list waits for a pause in typing, and an entry that spans the whole row no longer makes
    every lookup search the row from its start.
  - Undo keeps as many steps as fit into 2 GB of replaced samples (4 for that capture, 50 for
    small ones) instead of 5 steps of any size.
  - The preview of *Play as signal* is drawn from the runs of the pattern, not from every
    sample.
  - States shown on their real time: the regions and markers of the states wait for the way
    back instead of marking wrong times, and saving writes the states, not the picture of
    them. A state analysis no longer carries the regions of the timing capture.
- **Capture files: small, quick, any size.**
  - `.lac` files hold the samples packed (8 per byte, compressed) instead of a number per
    sample: a capture of 5 million samples on 24 channels is a file of 0.05 to 20 MB instead of
    360 MB, saved in 0.1 s instead of 5 s and opened in 0.1 s instead of 7 s with a gigabyte
    less memory. Captures recorded to disk that did not fit into memory can be saved and
    opened now. Every file written so far still opens.
    **The original LogicAnalyzer software cannot open the packed files**: *Save as → Captures
    for the original LogicAnalyzer (\*.lac)* (in scripts `Capture.save(path, compatible=True)`)
    writes its form, a number per sample. A file that has that form keeps it when it is opened
    and saved again; only *Save as* changes the form.
  - Opening, saving and exporting run in the background with a small dialog; the window stays
    usable and a second save cannot start meanwhile. A file that is damaged or cut off is
    reported as such in every case (some errors ended silently).
  - CSV and VCD exports are written block by block: 2 million samples on 16 channels in 0.06 s
    instead of 3.2 s (CSV) and 0.75 s instead of 1.9 s (VCD), without a copy of the capture in
    memory. A channel name with a comma is quoted in the CSV; a VCD never contains `nan`, and a
    capture without samples is refused with a message.
  - `.lac.gz` is compressed at level 6 (level 9 took much longer for a few percent).
- **A running flow no longer floods the window.**
  - What the engine reports is collected and shown 25 times a second: the last state of every
    node, the values of every output as a batch (charts of panels and the sparklines of the
    wires still get every value), the log, the views. A flow in virtual time sends hundreds of
    thousands of values a second; each one used to be a signal, a search through all wires and
    a repaint of the scene and the minimap.
  - A value in a chart, number or table no longer rebuilds the menus, the inspector and the
    project lists of the window (it did so for every value); neither does an undo step or a
    keystroke in a document. The data list of the project is read when the folder changes and
    when a run ends.
  - The scope of a flow keeps the zoom and position while more of the signal arrives.
  - Charts: thousands of points are drawn as their envelope (two points per pixel column);
    a value that is no number (a measurement without a result) no longer blanks the chart of a
    panel; zooming in very far no longer freezes the window; numbers, LEDs and charts of a
    panel show the last sample of a block of samples (an LED was always on for one); tables
    add their new rows instead of being filled again.
  - Flow editor: the inspector is replaced, not collected (every click left one behind), and
    typing the name or description of the flow keeps the cursor; the ports of the nodes are
    only built again when they change; search popups, the command palette and detached
    windows are freed; closing a document whose flow still runs no longer prints errors.
  - A control of a panel bound to a node the flow no longer has is skipped and named in the log
    instead of stopping the start of the run.
- **Flows that run for days.**
  - Memory no longer grows with the run: the engine keeps what every view shows now instead of
    everything it ever showed (the *Long-term logger* template took 750 MB after 60,000 values,
    now a constant 80 MB, and runs twice as fast), and the last 10,000 lines of the log.
  - Timers and the monitor keep their period (the n-th tick at n × interval; the time a tick
    took no longer adds up), a flow in real time wakes a hundred instead of a thousand times a
    second, and `duration` also ends a flow whose stream is still open.
  - The logger names the source of every row (the pin, e.g. `A0`, instead of its own name),
    writes to disk after `flush` rows or five seconds, logs the samples of analog and digital
    blocks, and the rows of a growing table once each.
  - Reports render each section, table and diagram once when they are written instead of on
    every value; diagrams of long captures show their envelope; **PDF reports contain the
    diagrams** (they were missing).
  - Two flows could not be stopped: a sequence that only sets, emits or logs with `repeat: 0`,
    and an endless timer with interval 0 (now refused). Nodes that wait for each other to take
    their values end the flow with the error *deadlock* instead of "finished".
  - Pins, PWM, voltages and the monitor of a real device are called in a thread, one call at a
    time per device: a device that is slow or does not answer no longer holds the other
    nodes, *Pause* and *Stop*.
  - Smaller: text parameters of several lines keep their line breaks exactly when saved;
    parameters may be called `position` or `node_id`; a function node with a `params`
    argument validates; a capture rate below 1 Hz is refused with a message; *Browse…* stores
    the path the flow resolves to the chosen file, also in subfolders of the project.
- **Decoders: about eight times faster, and safe to close.**
  - Waiting for an edge or a level is looked up in an index of the channel's edges instead of
    searching 65,536 samples each time: a UART capture of 2 million samples decodes in about
    3 s instead of 26 s, with identical results.
  - Closing a data view (or the application) while it decodes no longer risks a crash: the
    decoders are told to end and the view lets go of them. A new decode requested while one
    runs ends the old one instead of waiting for it.
  - An edit of the samples or an undo decodes once, not twice; the options dialog can be used
    while a decode runs without mixing old and new settings.
  - Flows and data views share one set of decoders (the decoder folders of the application
    apply to flows as well), loaded once. The node palette imports the decoders only when its
    *Decoders* group is opened or searched, so the window starts faster.
- **Review of the code base: nothing is lost or silently wrong.**
  - Decoders keep reading their channels when the channels are reordered (dragging a channel
    made them decode another one), also after the next capture and with profiles that capture
    only some channels. A decoder assigned to a channel the capture does not have says so.
  - A flow that ends with an error, with Ctrl+C or by *Stop* still writes what it measured: the
    rows of a logger, the recorded samples and the report (which then says what failed). *Stop*
    pressed right after *Run* is no longer lost.
  - Subflows with a required input run (*Make subflow* produced flows that could not start),
    and a subflow may use the other subflows of its flow.
  - Captures, profiles, waveforms, projects, panels, flows and reports are written to a
    temporary file that replaces the old one only when it is complete: a crash or a full disk
    no longer destroys the file that was there. A settings file that cannot be read is kept as
    `<name>.bad` instead of being overwritten, and one unreadable profile no longer takes the
    others with it.
  - Markers and pinned channels are saved in the capture file and come back when it is opened.
  - A selection in the ruler is cleared when other data is shown (cut or delete acted on the
    old positions); shifting channels of different length, inserting samples with an empty
    channel and search hits after an edit are right.
- **Simulators: several at once, multi device sets, and what they simulate.** Every simulator
  can be connected again and again (`sim:uno`, `sim:uno#2`, …), each an instrument of its own.
  *Simulated multi device…* combines 2 to 5 simulated boards into one device captured together
  (`sim:pico*2`: 48 channels), like a multi device set of real boards. The new *Signals* tab of
  a simulator's device card says what it simulates: a UART, SPI, I²C, all three, a counter, the
  **C64 bus** (a Commodore 64 at its expansion port, on the channels of the profile *C64
  expansion port*; needs 48 channels), the channels of a capture file, or nothing – and any
  source on a single channel (double-click it). It applies at once, names the channels for the
  next capture, is kept per device and comes back with the session. In a flow the device node
  takes it as `signals`. A pin of a simulator shows its signal again after it was an output.
- **Device handling: more reliable and faster.**
  - Opening a device no longer freezes the window: boards, network devices and the DSLogic are
    opened in the background with a small dialog (*Cancel* stops waiting), also when the session
    reconnects its devices at startup; the power of a network device is asked in the background.
  - Stopping a capture that waits for its trigger ends it everywhere (card, data view, header);
    stopping never blocks the window.
  - A device that stops answering (a failed capture or no answer over the network) is marked
    with an error and its card offers *Reconnect*; a network board that vanished is noticed
    through TCP keep-alive instead of a capture waiting forever.
  - Pico driver: a board still streaming from a program that crashed is resynchronised on open;
    stopping and starting again at once no longer lets the old capture end the new one; a board
    that does not answer the capability question is not asked again for every property (each
    time 5 s); device details are read once; serial ports are opened exclusively; the timeouts
    of the identification follow the connect timeout.
  - Flows leave the devices of the device list tidy: a capture or stream they started is
    stopped when the flow ends early, generators stop also after an error, and simulators wired
    for the run are wired as before.
  - The simulator: a failing source ends the capture with an error instead of leaving the
    device "capturing" for good; wiring loops are refused; stopping a capture that waits for a
    trigger works at once (it could take 10 s) and no longer spins a core; endless streams with
    analog channels use constant memory; state captures sample thousands of times faster;
    sources are read consistently while outputs change in another thread.
  - Less work in the background: firmware images and simulator profiles are read once, serial
    ports are listed once per look, the overview of the connected hardware only scans while it
    is shown, and the pin values of a fast monitor update 30 times a second.
  - A listener of the device hub that fails no longer breaks adding or removing devices; a
    removed device takes its capture controller, handlers and timers with it; closing a device
    card leaves the monitor of a running recording alone; closing the hardware overview while
    a version is being read no longer risks a crash.
- **Polish.** Shortcuts are written the way the platform does (⌘K on macOS); keys without
  Ctrl/Cmd only act in a data view that has the focus; *Measurements* moved to Ctrl+Shift+M
  (Cmd+M minimises on macOS); Esc means *All outputs safe* on the device card's *Pins* tab only;
  the shell's shortcuts and the document's menus work in documents opened in a window of their
  own; device card and panel commands are in the command palette; *Help → Keyboard shortcuts*
  is a searchable list with the keys and gestures of the views. The data view's view menu is
  called *Display*; file dialogs say *Captures*; saving with *Compressed captures* adds
  `.lac.gz`; code and logs use the system's fixed-width font. The device card, the data view
  and the side panels need less width, so a card and its data view fit side by side.
- **Capture settings in pages; accessibility.** The capture settings are pages – *Sampling*,
  *Channels*, *Trigger* – with a line below that says what the capture will be; a problem opens
  the page where it is fixed and marks it. Every button that shows only an icon has a name for
  screen readers (from its tooltip), the flow graph and the waveform describe what they show,
  and the status dots are sharp on high resolution screens.
- **Panels arranged with the mouse, charts that zoom.** The panel editor draws its grid with free
  rows below the widgets: drag a widget from the palette onto a cell, drag widgets to move them
  and their corner to resize them (a taken place is shown red and refused); every step can be
  undone. The Port list offers the ports of a type the widget shows, Tab and Group are chosen
  from the existing ones (or typed new), a widget whose port does not exist is marked, and Edit
  and Operate are toggles in the toolbar. Charts and the plots of panels zoom with the wheel or a
  pinch (Shift: values only), move with two fingers or by dragging, and go back to the automatic
  range on a double-click or smart zoom.
- **Undo in the data view, channels in any order.** Deleting, inserting, pasting and shifting
  samples, derived channels, renaming, colours, hiding, the order of channels, markers, regions
  and buses can be undone and redone (Edit → Undo/Redo, Ctrl+Z); large captures keep fewer
  steps. Cut, copy, paste and delete of the selected samples are in the *Data* menu with their
  usual shortcuts, as are *Insert samples…* (now also for files and test signals) and *Shift
  channels…*. Channels are reordered by dragging their name or with *Move up/down*; the order
  is saved with the capture. The button above the channel names says how many channels are
  hidden and shows them again one by one or all at once. Regions can be renamed and recoloured
  (*Markers → Edit…*). *Data → Use these channel names for the next capture* hands names given
  in the data view to the device.
- **The last session comes back.** openSciLab opens the documents of the last session again –
  files, device cards, data views of a device, the start page, the connected hardware – in their
  groups and side by side, with the active one in front, flows and captures zoomed as they were,
  and the console as it was; the devices are connected again. Files that are gone are left out
  (noted in *Log*), devices that are not there are named in the device list. Unsaved documents
  are not kept. *Settings → Startup* turns this off and chooses whether the start page shows. A
  window saved on a screen that is no longer there opens on one that is.
- **Settings and a light theme.** *Project → Settings…* (Cmd+, on macOS) sets the theme (dark,
  light, like the system; applied after a restart the dialog offers), the text size, what happens
  at startup, whether the mouse wheel zooms or moves and in which direction, the folder for data
  and new projects, the number of recent entries, how often devices are looked for and whether
  simulators are listed. The light theme covers the shell, the documents, the flow graph,
  comments, charts, LEDs and the pin strip; the waveform display stays dark like an instrument
  screen. Text keeps a contrast of at least 4.5:1 in both themes.
- **A friendlier flow editor.** While a wire is dragged, the ports it fits light up and the others
  fade; it snaps to a fitting port nearby or to the right port of the node it is dropped on. A
  wire dropped on the empty canvas offers the nodes it fits (placed there and wired), a wire that
  does not fit says why above the graph and offers the conversion node. Right-click a wire to
  insert a node into it or remove it, a port to add a connected node or disconnect it. Copy,
  cut, paste, duplicate (Ctrl+D) and select all work on nodes with their wires; new nodes no
  longer land on top of each other; Tab or *Add node* in the toolbar opens the node search, and
  an empty flow says how to begin. Required inputs without a wire are marked, nodes show a badge
  for their problems and their type next to their name, the breakpoint dot shows on hover.
  Adding the first device wires the nodes waiting for one (the status bar says what was wired).
  Group frames carry the nodes inside them, are grabbed by their title and edge and resize at
  their corner (comments too). The inspector marks required parameters, lists the node's
  problems, offers the pins of the wired device and *…* to choose a file, says when changes
  apply only to the next run, keeps multi-line descriptions and checks seed and duration. Less
  used tools in the flow toolbar show their icon only; the inspector wraps instead of cutting
  text off.
- **Clearer ways through the application.** The start page comes first with *Try a simulator*
  (a simulated device, its card open; its first capture shows data at once) and *Connect a
  device*; with every document closed the area offers a new flow, connecting, opening a file or
  the start page. *Devices → Connect…* lists detected boards, network and multi devices and the
  simulators in one dialog; the Devices menu reaches the card, the data and *Disconnect* of every
  open device, *Install or update firmware…* and outdated firmware lead to *Connected hardware*,
  and a device whose firmware was installed there opens again by itself. Disconnecting asks while
  a capture runs, outputs are driven or a flow uses the device; an unplugged or disconnected
  device card offers *Reconnect*. The first capture opens its data view beside the device card,
  the header's *Capture* / *Capture again* / *Stop* work for device cards and data views, and the
  card shows the state of the capture. Connection errors, failed captures and flows that cannot
  run appear as a banner where they happened (a run without its device offers *Run with
  simulators*); *Check* and running a flow open the console, the status bar counts the problems
  and a double-click on a problem shows its node. *Use settings* (formerly *Apply*) keeps the
  capture settings, Enter in a channel name moves to the next name, and the card's *Profiles* is
  the only profile menu. The Project menu has *New project from template…*, *Open project…* and
  recent projects; the Project sidebar lists the flows, panels and waveforms of the project.
  Untitled documents are numbered (*Untitled flow 2*), a data view names its device, Ctrl+Tab
  switches tabs, and *Split* needs a second document. The Devices menu's mnemonic is Alt+V
  (Alt+D is the data view's *Data*).
- **Data, device and project fixes.** Every data view has its own decoders (adding one no longer
  changes the others). The data view of the *Logic Analyzer* template receives the first capture.
  Opening a file or test signals over unsaved data asks first; renaming channels, colours,
  markers, regions and buses count as unsaved changes; a capture never replaces data the user
  changed (it opens in another data view); *Capture again* uses the settings of the last capture,
  not samples edited since; a capture whose data view was closed opens in a new one. The device
  card shows the settings of the *next* capture, its capture bar works for simulators, and
  loading a profile sets the next capture of this device only (and updates the bar). A status
  change keeps switches and values in the pin table, a declined switch goes back, and the
  *Monitor* box follows the monitor (also when a recording starts or stops it); a new rate
  applies at once. Devices found by autodetect, over the network or as a multi device carry
  their real address, and unplugged devices are shown as disconnected. *Choose an image…* no
  longer restarts the port of an open device behind the application's back.
- **Projects and closing.** Closing the last document of a temporary project asks whether to
  keep it; its files no longer fill the recent files. *Save as* in a temporary project saves the
  project first, and *Save project as* lets you name the project folder. Closing the window asks
  once for everything unsaved, and *Cancel* closes nothing. Opening a project without flows or
  panels says so instead of doing nothing. The status bar shows real or virtual time of the
  active flow or panel and which documents are unsaved; the window title marks unsaved changes.
  The sidebar stays as you left it at startup. Messages point to the right menus (*Data → Align
  boards*, *Devices → Connected hardware*).
- **Flow and panel editor fixes.** A double-click in the node palette adds one node (not two);
  a new device node starts with the default device instead of failing when the flow runs; a click
  on a node no longer marks the flow as changed, and two moves are two undo steps; saving a flow
  opened from a Python script saves it as `.flow.yaml` instead of overwriting the script; YAML
  with an error is kept when switching views (keep editing or discard), and the edits of one visit
  of the YAML view are one undo step; an error tooltip goes when the node is idle again; Run is
  available again as soon as a run has ended; renaming a node keeps the ports of a subflow; the
  palette shows the node types of the active flow's project. The panel inspector keeps the
  cursor while typing, steps of one setting are one undo step, Redo is enabled after Undo, and a
  panel runs the current flow (the open flow document with its changes, or the file read again
  when it changed). Save dialogs without a project start in `~/Documents/openSciLab`.
- **Trackpad and pinch in the flow graph.** Two fingers move the canvas instead of zooming it,
  pinch zooms at the fingers, a two-finger double tap fits the flow, Cmd + swipe zooms; the mouse
  wheel zooms at the pointer (Cmd + wheel sideways, Shift + wheel up and down). The zoom stops at
  its limits instead of ignoring the step, far nodes stay reachable, *Flow → Zoom in / Zoom out /
  Fit (Ctrl+0) / Actual size* are new, and switching to YAML and back keeps zoom and position.
  The overview can be dragged and passes wheel and pinch on to the canvas.
- **The same navigation in every part of the data view.** Analog tracks and buses follow the
  trackpad instead of zooming on every swipe, pinch works over the ruler, buses and analog tracks,
  Cmd + swipe zooms the waveform, and the overview below the waveform follows the trackpad. The
  panel editor scrolls also with the pointer over a widget.
- **Clearer device card.** The card has tabs: *Capture* (settings of the next capture, Start,
  the channels with their names), *Pins* (pin, channel, name, mode, value, actions; PWM and DAC
  controls on lines of their own), *Events* (simulators) and *Details*. Device information,
  self-test, network, bootloader and firmware moved into the *Device* menu of its header. A
  declined or failed switch goes back to *Off*.
- **Apply in the capture settings.** *All settings… → Apply* keeps the settings without capturing;
  the channel names appear in the card's channel and pin tables at once.
- **Connected hardware.** *Devices → Connected hardware* lists every board and device on the
  computer with its firmware and whether it is current. *Install/Update firmware* picks the image
  that fits the board, restarts it into its bootloader, writes the image and waits until it is
  back; *Choose an image…* keeps the manual dialog.
- **PiPiLogicAnalyzer is now openSciLab.** The Python package is `openscilab`, the commands are
  `openscilab`, `openscilab-cli` and `openscilab-gui`, the settings live in the folder
  `openSciLab` and the environment variables are `OPENSCILAB_SETTINGS_DIR` and
  `OPENSCILAB_DECODERS`. The old names are gone (no aliases, settings are not taken over). The
  packaged applications are called `openSciLab-<version>-<platform>`. The Pico firmware keeps
  its name until the next firmware release.
- **New lab shell.** The application opens in the openSciLab shell: an activity bar and sidebar
  (project, devices, search), documents in tabs that can be split side by side or below each
  other and opened in a window of their own, an inspector on the right, a console below
  (problems, execution, log and a Python console with access to the shell) and a command palette
  (Ctrl+K) that reaches every menu entry, with fuzzy search and the recently used commands first.
  A start page lists recent files and the *Logic Analyzer* template. Layouts can be saved and
  restored (View → Layout).
- **The logic analyzer is a document.** Everything the main window did is unchanged inside an
  analyzer document: its *Capture, Device, Analyze* and *Profiles* menus appear while it is
  active, its view menu is called *Waveform*. Several captures can be open at once. A capture
  with unsaved changes is marked in its tab and closing it asks first. Files given on the command
  line open in analyzer documents.
- **Several devices at once.** Opened devices are instruments in the device hub: the sidebar
  section *Devices* lists the open ones with their status and the ones that can be opened, the
  header shows a chip per device, and each device has a device card (capabilities, pins,
  connection). An analyzer document captures with a device of the hub; closing the document
  keeps the device open for others, *Disconnect* closes it.
- For scripts and later steps: typed signals (`openscilab.core.signals`: digital, analog,
  scalar, bool, event, states, capture, table, with time bases and conversions), instruments
  with facets (`openscilab.core.instrument`) and the hub with trigger routes and time base
  offsets (`openscilab.core.hub`).
- **Flows.** A measurement program is a flow: nodes (devices, timers, sweeps, tables, files,
  views) and the wires between them, stored as `*.flow.yaml` or written in Python with the DSL
  of `openscilab.lab` (`with flow(...) as f: n.device.capture(...) >> n.data.file(...)`); both
  convert into each other unchanged. The engine runs flows in real time or, with simulators, in
  virtual time (fast and deterministic), with back pressure, breakpoints and single steps.
  Projects are folders with `project.yaml`, `flows/`, `data/` and own nodes in `nodes/`.
- **`openscilab run <flow> [--sim] [--fast] [--device name=address] [--report out.html]`** runs a
  flow without the window, `openscilab sim` lists the simulator profiles; `openscilab capture`
  and the other commands of `openscilab-cli` work as `openscilab <command>` too.
  `api.run_flow()` does the same from scripts.
- **Simulated instruments** (`sim:free`): a 16 channel logic analyzer with test signals
  (counter with clock, UART, SPI, I²C, square wave), buffer captures with edge and pattern
  triggers and streams, with the limits of its profile. `api.open("sim:free")` opens it.
- **Visual flow editor.** A flow document has three views: *Graph* (nodes and wires on a canvas,
  wires coloured by type, minimap, zoom and pan), *YAML* (the file itself, editable) and *Python*
  (the same flow as a DSL script). Nodes come from the palette in the sidebar (search, drag and
  drop) or by typing on the canvas; the inspector edits their parameters. Wires whose types do
  not fit are refused, with an offer to insert a conversion node. Undo/redo for every edit,
  comments, groups, alignment, subflows (opened as documents of their own). Running a flow shows
  the state of every node and the latest values on the wires (a sparkline for numbers);
  breakpoints and single steps work in the graph, scope nodes open analyzer documents.
- **Node library:** measurements (frequency, period, pulse width, duty cycle, min/max/mean/RMS,
  peak to peak, counts, setup/hold), signal processing (threshold with hysteresis, debounce,
  FIR/IIR filters, FFT, derivative, integral, averaging, resampling, expressions), every sigrok
  decoder as a node (`decode.uart`, `decode.i2c`, ...), control (sequences without code, state
  machines, Python nodes, compare, limit, counter), data (tables, buffers, reading and writing
  captures and CSV, long-term logger on disk), views (scope, strip chart, XY chart, spectrum,
  table, number, LED, log – each opens a document while the flow runs), bundles. Projects add
  their own nodes as Python functions in `nodes/`.
- **Analog channels.** Devices with analog inputs capture them together with the logic channels;
  the analyzer shows them as tracks below the digital channels (envelope zoomed out, line and
  points zoomed in), with their value at the pointer and at the cursors, min/max/mean/RMS/peak to
  peak in the measurements, and a digital channel from a threshold with hysteresis. `.lac` files
  keep them (older files load unchanged), sigrok sessions for PulseView carry them as analog
  traces, CSV exports them in volts and VCD as `real`.
- **Large captures arrive progressively:** an overview of the whole capture at once, the samples
  in full resolution after it, the part on screen first; saving, export and decoders wait for the
  rest; an interrupted transfer can be resumed.
- **Live:** roll mode (the view follows the newest samples or holds still), analog channels and
  losses while streaming.
- **State captures with times:** the time of every state is kept; the view shows the states one
  by one (numbered axis, cursors with state and time) or on their real time. State mode on a
  stream (live state) and software triggers on states; the clock input is chosen from the pins
  that can clock.
- Simulator: analog sources (sine, triangle, ramp, noise, sums), state captures, the provisional
  profile `sim:dho924s` (4 analog and 16 logic channels, progressive transfer).
- **Controlling pins from the device card.** Devices with GPIO show their pins as a strip and a
  table: mode per pin (input, pull-up, output, PWM, analog), switches, pulses of a set width, PWM
  and voltages of analog outputs; reserved pins are greyed out with the reason. The first output
  on a pin asks once about its level. *All outputs safe* (Esc) turns every output back into an
  input. While outputs are driven the card keeps the device's watchdog fed. The event log lists
  what the device did.
- **Monitor:** the card reads the inputs periodically (levels and voltages with a short history);
  *Record* records the monitor as a slow capture in a new analyzer document.
- **Markers for actions:** everything set on a device while a capture runs is a marker in that
  capture (at once in a live display, placed when it completes otherwise, marked with ≈ when the
  position is estimated).
- **Stimulus and capture:** the card arms a capture with any connected instrument (also another
  device) and then pulses the selected pin.
- GPIO nodes for flows: `gpio.write`, `gpio.pulse`, `gpio.pwm`, `gpio.read`, `gpio.dac` and
  `device.monitor`.
- Simulator `sim:uno`: an Arduino Uno with GPIO latency, exact pulses, a watchdog, D7 wired to D3,
  the PWM of D9 through an RC low pass to A0 and a bouncing push button on D2; faults can be
  injected (disconnect, delay, restart). Buffer captures of the simulator wait for their trigger
  in real time, so a stimulus after arming is captured.
- **Signal generation.** A waveform document builds what a generator output plays: standard
  shapes (sine, square with duty cycle, triangle, ramp, pulse, DC, noise) with sweep and burst,
  arbitrary waveforms from a formula or a CSV file, and digital patterns as SDL per pin with a
  preview of the tracks. It lists the generator outputs of all connected instruments, explains
  what does not fit an output (frequency, voltage, points, rate – a too fast pattern can be
  limited to the output's rate) and starts and stops it. Waveforms are saved as `*.wave.yaml`;
  `*.sdl` files open too.
- **Play as signal** (Analyze menu of the analyzer): digital channels of a capture as a pattern,
  or an analog channel as an arbitrary waveform, between the cursors if they are set.
- Generator nodes for flows: `gen.waveform`, `gen.arbitrary`, `gen.pattern`, `gen.tx_uart`,
  `gen.tx_spi`, `gen.tx_i2c`, `gen.replay` and `gen.output`; their `sync` output marks the start.
  The capture node reports `armed` once it waits for its trigger.
- Simulator: `sim:free` has a pattern generator on D0-D7 (looped back to the inputs) and a
  trigger input on D15, `sim:dho924s` a 25 MHz generator output on CH1 with its sync on D15;
  trigger routes of the hub between simulated instruments are wired (a sync output triggers a
  capture on another instrument).
- **Panels.** A panel document (`*.panel.yaml`) is the front panel of a measuring station:
  switches, push buttons, sliders, inputs, choices, numbers, LEDs, charts, an embedded scope and
  labels on a grid with tabs and groups, each bound to a port of a flow (inspector). In
  *Operate* the controls send their values into the running flow and the displays show its
  outputs; F11 shows the panel full screen as an application of its own. Undo/redo for every
  change of the layout.
- **Project data:** the project section of the sidebar lists the captures, tables, logs and
  reports of the project's `data/` folder; captures open in the analyzer, two of them can be
  compared, one exported as CSV; tables and reports open in their system application.
- **Reports:** nodes `report.section`, `report.table`, `report.image` (XY diagrams and traces as
  SVG), `report.check` (limits or an expected value, passed or failed) and `report.write` (HTML,
  or PDF). `openscilab run --report` puts the same parts into its report, and its exit code is 1
  when a check failed.
- New template *Characteristic curve* (start page): a PWM duty sweep on the simulated Uno
  measured behind an RC low pass, with a panel, a report and a check of the slope. Templates are
  projects copied to a folder you choose; recent projects open from the start page.
- `data.table` keeps the values of each column in order, so a column that runs ahead no longer
  overwrites values before their row is complete; `control.sweep` can be started by a value at
  `trigger` (a start button).
- **Simulators complete:** profiles `sim:uno`, `sim:uno_r4` (DAC on A0 wired to A1), `sim:pico`
  (24 channels, depth by the number of channels, ADC, pattern generator), `sim:dho924s` and
  `sim:free`, as JSON files in `examples/sim/` (own profiles in the settings folder `sim`). The
  device list of the sidebar offers every profile (*Simulation: …*); simulators opened there run
  on the clock of the hub. *Devices → Simulation* injects faults (disconnect, delay, overflow,
  restart), scripts use `device.inject()`; `api.instrument("sim:uno")` gives an instrument with
  all its facets. A project wires simulated circuits further (`simulation.wiring` in
  `project.yaml`), the source type `pulse` is new, and the same seed gives the same samples in
  virtual time. The profile keys, sources, wires and faults are described in `docs/simulator.md`.
- **Templates** on the start page: *Empty lab*, *Logic Analyzer*, *Characteristic curve* (now with
  the simulated Uno and DHO924S: the scope measures the RC voltage), *Test bench* (checks with a
  report, a test on the command line) and *Long-term logger* (CSV and strip chart).
- Panels run in virtual time if wanted (*Virtual time*); a project wires two simulated devices to
  each other (`simulation.wiring`: `{from: "uno:A0", to: CH1}`).
- Documentation: the README starts with the lab, the analyzer is a chapter; `docs/lab.md`
  (window, instruments, flows, all nodes, panels, generation, reports, projects),
  `docs/simulator.md`, `docs/protocols.md`, facets in `docs/drivers.md`.
- **Devices are nodes of a flow.** A `device.instrument` node stands for an instrument (a
  simulator, a device address, or an instrument already open in the device list, which is used as
  it is); its `device` output is wired to the nodes that use it (capture, stream, monitor, GPIO,
  generator). The palette offers the open instruments, the project's devices and the simulators,
  the device list *Add to the flow*; a new node is wired to the flow's only device at once. The
  `devices:` section and the `device:` parameter of flow files are gone.
- **The analyzer document is a data view.** It shows, analyses, decodes and edits captures (also
  files and test signals) and no longer connects devices or holds their settings. Capturing is the
  **device card's** business: its capture bar (rate, length, trigger, *Settings…*, *Start* F5,
  *Capture again*, *Stop*), *Profiles*, *Test signals of the board*, *Device information*,
  *Self-test*, *Network*, *Bootloader* and *Update firmware*; the result appears in the device's
  data view, whose toolbar names the source (a click opens its card) and can capture again or
  stop. Devices are opened only in the device list, which also notices boards without firmware
  and offers the update of outdated ones. *Forget multi device sets* moved to the *Devices* menu.
- **A flow from the start:** the application opens with an untitled flow beside the start page; it
  writes into a scratch folder until it is saved. Templates open as temporary projects without
  asking for a folder; the first *Save* (or *Project → Save project as…*) asks where to keep the
  project and moves its open documents along.
- New dependency: PyYAML.

### Firmware

- **openSciLab Pico, protocol 8.** The Pico firmware is now *openSciLab Pico* in `firmware/pico/`
  (source files without the old prefix), released with the application (0.1.0b1) and speaking
  **protocol 8**. It identifies itself as
  `OPENSCILAB_PICO_<BOARD>_V8_0`, its USB product name is *openSciLab Pico* (VID/PID unchanged),
  the images are called `openSciLab-pico_<BOARD>[_Turbo].uf2`. Boards with PiPiLogicAnalyzer 7
  are recognised and offered the update; the application only works with protocol 8.
- The capability strings of the Pico firmware come from `capabilities.h`; `docs/protocols.md`
  specifies the whole vocabulary (capabilities, the facets they unlock, pin capabilities, the
  wire protocols) and a test checks the firmware header and the application against it. The
  firmware reports the same capabilities as before.
- `firmware/build_all.sh` builds by firmware (`--pico [boards]`, `--arduino`, `--bridge`); a dev
  container (`.devcontainer/`) has the Pico SDK, the ARM toolchain, picotool, PlatformIO and the
  Android tools. The release file with all Pico images is called
  `openSciLab-firmware-pico-uf2.zip`.
- *Devices → Install or update firmware…* and the device card update the firmware of a Pico. For
  the other boards the update tools (avrdude, bossac, esptool, adb) are prepared in the
  application, but no driver uses them yet: Arduino boards are flashed with PlatformIO or
  avrdude, the bridge app with adb.
- **openSciLab Pico 8 does more** (protocol 8): pin table, pin modes, outputs, PWM, pulses
  exact to a clock cycle, the monitor, the watchdog (outputs are released 1 s after the host is
  gone), analog channels by DMA, the pattern generator on up to 24 pins, UART/SPI/I²C transmitters
  and the live state stream. Host tests of the frame parser, pattern data and pin tables run in CI.
- **openSciLab Arduino firmware** (`firmware/arduino/`, PlatformIO) for Uno, Nano, Mega 2560,
  Uno R4 Minima, ESP32 and ESP32-S3, with native tests and a benchmark project.
- **Bridge app** for the Rigol DHO900 (`firmware/bridge-android/`): passes SCPI through, keeps
  captures in a cache, sends overviews and compressed tiles and announces itself on the network.
- None of the three has been tried on hardware yet.

## [7.2.0] - 2026-10-01

### Application

- **The application requires the firmware it comes with.** Boards report a protocol version
  (`PROTOCOL:<n>` after the identification); a board with another or older firmware, including
  the original 6.5 firmware, is no longer opened: connecting it offers the firmware update. The
  48 byte capture request of V6_0 and the fallbacks for firmware without capabilities were
  removed.
- **Analysis tools in the style of professional logic analyzers:** cursors A and B with Δt and
  1/Δt, statistics of the channels (frequency, duty cycle, pulse widths) and setup/hold times;
  named markers; search for edges, patterns with don't-cares, pulse widths, gaps, bus values and
  decoder output (F3); buses and groups with symbol tables; a listing of every change or of the
  decoder output; charts of bus values and histograms; comparison with a reference capture; state
  analysis on the edges of a clock channel.
- **Trigger sequences:** stages of patterns, edges, pulse widths and gaps, with counts and time
  limits; evaluated by the application on a stream (software trigger, any device that streams)
  or by the device when it reports `TRIGGER_SEQUENCE`. State mode with an external clock for
  devices that report `STATE_MODE`.
- **New main window layout:** rate, length and trigger of the next capture in the toolbar,
  *Start* captures at once (F5), all settings with Ctrl+F5; the decoders, measurements, search,
  markers and capture information as tabs of a panel, the listing as a panel below; both can be
  moved and closed, and the layout is restored. The toolbar groups file, device, capture settings,
  start/repeat/stop and the panels with icons; every menu entry has an icon; zoom and channel
  height moved from the side panel to a bar below the waveform.
- **Start, Repeat and Stop in the middle of the toolbar**; while a device is connected the device
  list makes room for the capture settings.
- **Detailed list of an annotation row:** the other rows of the decoder are columns of their own,
  e.g. the bytes read during each instruction of a disassembly (`D0 F5 EE`) and its memory region;
  the details of the selected entry show every form of its text, those entries one by one with
  their time, and the levels of the channels the decoder read. The filter and *Copy* include them.
- **Hovering an annotation marks what belongs to it** in every row: the bus cycles of an
  instruction, or the instruction of a bus cycle (dashed outline). An entry made of several values
  shows where each was read and lists them next to the marked span, instead of one read point with
  the levels of every channel; a single value keeps its read point, now with the bus values next
  to it rather than in a corner.
- **State analysis reads where the data is stable:** it suggests the clock edge and read offset at
  which the other lines do not change (on a C64: the falling edge of Φ2, one sample before it;
  the rising edge read the VIC phase and gave wrong addresses), keeps the clock channel so the
  decoders still find their channels, and *Analyze → Back to the timing capture* returns to the
  capture it was made of.
- **sigrok sessions** (`.sr`, PulseView) are exported and opened; decoder output is exported as
  CSV or JSON.
- **Command line and Python API** (`pipilogicanalyzer-cli`, `pipilogicanalyzer.api`): list
  devices, capture, decode, convert between formats; see [docs/cli.md](docs/cli.md).
- **New look of the waveform:** a time grid and a ruler in time units (0 at the trigger, steps of
  1, 2 or 5 of ns/µs/ms/s) instead of sample numbers; thinner antialiased lines with the area under
  a high level lightly filled; dense signals as a translucent band with its envelope instead of a
  solid block; a calmer default palette of 16 distinct colours; the trigger as a dashed amber line
  with a flag on the ruler; the overview dims what lies outside the view.
- **Channel list** in one line per channel: colour strip (click to change), visibility, name and
  channel number; rename with a double-click or the context menu.
- **Device information with tabs:** *Overview*, *Capture limits* and *Self-test*; *Device →
  Self-test…* opens it on the self-test (for the PiPiLogicAnalyzer boards and the DSLogic).
- **DreamSourceLab DSLogic Plus, U2Pro16, U3Pro16 and U3Pro32** (experimental, tested on a
  U2Pro16): a USB driver following the DSLogic driver of DSView, including its FPGA security
  handshake. Buffer and stream captures at the rates of the device (up to 400 MHz / 1 GHz), edge,
  level pattern and immediate triggers, adjustable input threshold. The FPGA bitstreams come from
  an installed DSView or are downloaded once from the DSView repository; they are not shipped.
- DSLogic stream captures are shown while they run, following their end; *Stop* keeps the samples
  received so far. *Until stopped* streams endlessly and keeps the latest samples in a ring
  buffer. Reading and unpacking run in separate threads, so a stream at the USB 2 limit
  (20 MHz × 16 channels) keeps up.
- Board self-test for the DSLogic: the internal test counter of the FPGA checks the capture memory,
  buffer and stream transfers and the triggers bit by bit; the inputs are checked to read low.
- Self-test titles keep their acronyms ("RAM", "FPGA").
- **Stream captures with the PiPiLogicAnalyzer boards** (firmware of this project, over USB):
  the samples are sent while capturing and shown live, with a fixed length or until stopped.
  Up to 800 kHz with 8 channels, 400 kHz with 16, 200 kHz with 24; an overflow of the USB link
  ends the stream with a warning and keeps the samples before it. The board self-test streams
  a test counter and checks it sample by sample.
- Capture dialog: the highest rate follows the acquisition mode (a stream is limited by its
  link); devices whose stream starts at once offer only "None" as its trigger.
- **Record to disk** (streams of the DSLogic and the PiPiLogicAnalyzer boards): the samples go
  into memory-mapped files, so a stream is limited by the free disk space instead of about one
  billion samples. The edge index of such a capture is built in blocks and kept on disk too, the
  live index is kept when the stream ends, decoders run on request, and a disk that fills up
  stops the stream. Sample counts beyond 32 bits in the dialog.
- *Max* next to the sample count, and *Until stopped* starts with the most samples that fit
  (it kept a small number from earlier settings, e.g. 30,000).
- The capture overview is drawn from the edge index (two searches per column instead of a sum
  over every sample) and follows a stream live; the display no longer converts the edge index to
  floats on every frame, which made large captures slow. Progress of a stream the display cannot
  keep up with is shown at its newest state instead of queueing.
- Capture dialog: devices with a fixed list of rates get a list instead of the free value; an
  acquisition mode (buffer/stream), a threshold and "no trigger" appear where the device has them.
  The edge trigger offers all channels of devices with more than 24.
- `.lac` files store the acquisition mode and the input threshold of the capture settings.
- Selecting the new analyzer after a firmware installation works again (the device list lookup
  missed its entries).

### Firmware

- The identification ends with `PROTOCOL:<n>`, the protocol version the application requires
  (`FIRMWARE_PROTOCOL`, raised with every protocol change).
- Trigger sequences evaluated on the board (command 10, trigger type 7) and the state mode with an
  external clock, reported as `TRIGGER_SEQUENCE`, `TRIGGER_CONDITIONS`, `SEQUENCE_MAX_RATE`,
  `STATE_MODE` and `STATE_MAX_CLOCK`.
- Stream capture (capture request with trigger type 6, USB only): the DMA channels fill the
  capture buffer without end and the samples are sent in chunks while they arrive, until the host
  stops the stream; an overtaken buffer ends it with an overflow marker. `STREAM=800000` in the
  capabilities; `triggerValue = 1` streams a PIO test counter instead of the inputs.

### Project

- Dependencies `pyusb` and `libusb-package` (the packaged applications include libusb); the smoke
  test of the builds checks that libusb loads. Linux udev rule for the DSLogic.
- **Device drivers are independent of the application:** the main window and the dialogs no longer
  ask which kind of device is connected, the driver describes what its device can do
  (`is_hardware`, `boards()`, `supports_bootloader`, `has_self_test`, `describe()`, ...). The
  device list is filled by one backend per kind of device (`ui/devices/`). The driver of the Pico
  boards (gusmanb's LogicAnalyzer hardware) moved to `driver/pico/`, next to `driver/dslogic/`.
  [docs/drivers.md](docs/drivers.md) explains how to add a device.

## [7.1.1] - 2026-09-24

### Application

- Multi device sets: the boards started through the trigger line were compensated for the
  trigger delay with far too few samples (the delay in clock cycles was divided by the sample
  period in nanoseconds), so their samples were shifted against the board evaluating the trigger.
- Pattern and edge-out captures whose post-trigger samples do not cover the trigger delay are
  rejected by the settings check instead of failing on the device.
- LogicAnalyzer Interceptor: the capture mode is chosen by the bit of every channel in the
  samples (channel 0 = GPIO 6), so channels 4–7 in 8 channel mode and 12–15 in 16 channel mode no
  longer read as 0.
- Dragging a selection in the ruler beyond the left edge produced negative sample numbers; cut
  and delete then removed the wrong samples. Cut no longer deletes when copying was refused.
- Automatic decoder channel assignment missed capture channel 0 when it matched by id.
- Changes made while the decoders run (sample edits, decoder settings) are decoded afterwards
  instead of being ignored; the decoders work on a snapshot of the channels.
- Loading capture settings or a profile in blast mode kept the default post-trigger samples.
- Aborting a WiFi capture closes the connection properly, which also ends the waiting read.
- VCD export: the time stamps no longer drift at sample periods that are not whole nanoseconds
  (e.g. 24 MHz).
- The board self-test dialog can no longer be closed with `Esc` while the test runs.
- Smaller fixes: the first pixel column of dense waveforms was drawn as toggling, the burst
  timestamp wraparound was off by one tick, stale input could be read as device details.
- Unused code removed.

### Firmware

- WiFi: received data is acknowledged to lwIP (`tcp_recved`); without it the receive window shrank
  with every request until the connection hung after about 11 KB of requests.
- WiFi: the error callback no longer closes the PCB lwIP has already freed, a closed connection
  no longer returns `ERR_ABRT`, responses are sent immediately (`tcp_output`), and a client that
  stops reading is dropped after 5 s.
- WiFi: received data waits in lwIP instead of blocking the WiFi core on a full event queue, so
  the two cores can no longer block each other during a large transfer.
- WiFi: an invalid stored IP address keeps the address assigned by DHCP.
- Captures are rejected when a channel does not fit into the samples of the requested mode
  (Interceptor) instead of silently reading 0; unknown trigger types are answered with
  `CAPTURE_ERROR` instead of starting an edge capture.
- Undefined shifts in the burst timestamps and the blast trigger mask fixed; a blast capture
  releases its GPIOs; the power status line cannot overflow its buffer.
- CMake applies a changed `BOARD_TYPE` to an existing build directory; unused code removed.

### Project

- `publish.ps1` works again after the rename (settings file, image name) and names the images
  like `build_all.sh`. The VS Code kit uses the SDK 2.1.1 toolchain.
- Corrected references to gusmanb's original LogicAnalyzer that the rename had changed.
- The release check also compares the firmware version in `CMakeLists.txt` with the tag.

## [7.1.0] - 2026-09-15

### Project

- **The project is now called PiPiLogicAnalyzer.** The Python package is `pipilogicanalyzer`, the
  command `pipilogicanalyzer`, the settings live in a directory of that name (the settings of the
  previous name are taken over on the first start) and the environment variables are
  `PIPILOGICANALYZER_SETTINGS_DIR` and `PIPILOGICANALYZER_DECODERS`; the previous names still work.
- The firmware is called PiPiLogicAnalyzer as well: the sources are in `firmware/PiPiLogicAnalyzer`,
  the images are named `PiPiLogicAnalyzer_<BOARD>[_Turbo].uf2` and a board identifies itself as
  `PIPI_LOGIC_ANALYZER_<BOARD>_V<major>_<minor>`. The application also accepts the previous
  identification, so boards with an older firmware keep working. USB VID/PID (0x1209/0x3020) and
  the hardware of Agustín Giménez Bernad are unchanged.
- `SECURITY.md` (private vulnerability reporting, where problems are plausible) and a Contributor
  Covenant `CODE_OF_CONDUCT.md`.
- Corrected license statements: a few bundled sigrok decoders are MIT or BSD, not GPL, and the
  AppImage runtime is distributed with the Linux build. The copies of the Raspberry Pi
  `lwipopts.h` and `pico_sdk_import.cmake` carry their BSD-3-Clause notice again.
- Source files carry a copyright and `SPDX-License-Identifier` header; `pyproject.toml` declares
  `GPL-3.0-or-later`.
- Pull requests that only change documentation now run the tests as well, so a required check can
  pass.

### Application

- The device list and the multi device dialog only offer detected analyzers; other serial ports
  are no longer listed.
- Clicking the name of an annotation row (or double-clicking an annotation) opens the row as a
  list in a window of its own: filter, copy (e.g. a disassembly listing), and selecting an entry
  shows it in the waveform.
- Multi device sets are no longer triggered by the master only: the board of the trigger channel
  evaluates a pattern or an edge (firmware of this project) and starts the other boards, or every
  board waits for an edge on its external trigger input.
- Pattern triggers use every group of consecutive trigger inputs the firmware reports (Pico:
  channels 1–21 and 22–24 instead of 1–16), fast matching included.
- C64 bus decoder: the *Bus cycles* row keeps the value read or written visible when zoomed out
  (`R $FFFC=$E2`, `FFFC=E2`, `E2`) instead of showing only the address.
- *Device → Install or update firmware*: every image shows the firmware version it contains, in
  the list and in its description.
- Channels can be made lower (or taller) to fit more of them on the screen: `Alt` + mouse wheel,
  *View → Taller/Shorter channels* (`Ctrl+Shift+Up/Down`) or the *Channel height* slider; the
  height is remembered.
- *Help → Online documentation* opens the wiki of this project; the wiki of the original software
  by gusmanb, which documents the hardware, has an entry of its own.

### Firmware

- Identifies itself as `V7_1`. A multi device set only accepts boards with the same version, so
  flash every board of a set with this firmware.
- Trigger type 5: edge trigger that also drives the trigger output.
- Pattern triggers accept all channels on consecutive GPIOs; the capabilities report
  `EDGE_TRIGGER_OUT` and `PATTERN_GROUPS`.

## [7.0.0] - 2026-09-14

First release, published under the name *LogicAnalyzer 7*. It is based on version 6.5 of the
[LogicAnalyzer](https://github.com/gusmanb/logicanalyzer) by Agustín Giménez Bernad (gusmanb)
(branch `version/v6_5`, commit `3fa3703`) and extends his firmware and software. Thank you,
Agustín, for creating the LogicAnalyzer and sharing it under the GPL.

### Application

- Cross-platform Python/Qt application with the feature set of the original 6.5 software:
  serial, network and multi device (2 to 5 boards) capture, `.lac` files, sigrok protocol
  decoders, profiles, sample editing, measurements and the Signal Description Language.
- Ready-to-run builds for Windows, macOS (Apple Silicon and Intel) and Linux (AppImage) that
  include the protocol decoders and the firmware images.
- Reworked user interface: toolbar with connection status, start page, capture menu, dialogs
  with named actions and inline validation, consistent dark theme and icons.
- *Install or update firmware*: flashes boards in bootloader mode, restarts connected boards
  into the bootloader and shows the installed firmware and Pico model of every board.
- *Board self-test*, *simulated capture* (on the board or computed locally) and *device
  information* for boards running this firmware.
- Much faster drawing of large captures, decoding on a worker thread, VCD/CSV export and
  compressed captures.
- Profiles can be edited: name, notes, capture settings and decoders.
- Navigation: the mouse wheel and `+`/`-` zoom, the arrow keys scroll horizontally, a trackpad
  scrolls horizontally through the samples and vertically through the channels, pinching zooms.
- Channels can be pinned; pinned channels stay at the top while the waveform scrolls.
- `examples/c64-demo.lac`: a generated C64 capture (reset, screen output, IRQs) for the C64 bus
  decoder.
- Multi device captures are aligned after capturing: exactly through a reference line (a slave
  signal also connected to the master) or estimated from a clock channel; *Capture → Align
  boards* does the same for captures opened from files and lets you choose the method when several
  are possible; the capture information names the method used. The C64 profile captures at 20 MHz and
  uses A0 as reference line.
- Hovering a decoder annotation marks its samples in the waveform and shows how the value is
  composed: the level of every channel at the sample the decoder read it, bit weights and bus
  values such as `A15…A0 = 1111 1111 1111 1100 = $FFFC`.
- C64 system bus decoder (`c64bus`) and an example profile for the C64 expansion port. It reads
  the bus at the last sample before the falling Φ2 edge (configurable, with an offset), flags
  cycles whose lines change next to the read point, and disassembles the 6510 code including
  undocumented opcodes, subroutine calls, branches and IRQ/NMI sequences, although the 6510 has
  no SYNC signal.
- Numerous bug fixes compared with the original software, see
  [docs/improvements.md](docs/improvements.md).

### Firmware

- Based on the 6.5 firmware; identifies itself as `V7_0`. The original 6.5 software accepts
  this version and keeps working with it.
- New commands: capabilities, board self-test, simulated capture and device information.
- Bug fixes in the receive buffer, request validation, blast and burst captures, pattern
  trigger, WiFi handling and the event machine, see [firmware/README.md](firmware/README.md).
- Turbo mode (overclocking and overvoltage) is off by default.

### Build

- GitHub Actions workflow that builds the firmware for all boards and the applications,
  publishes the pre-release *latest-build* from `main` and creates releases from tags.
