# Circuits

Draw a circuit - analog, digital, RF, with Arduinos running their firmware - and simulate it live:
probes show their voltages on the schematic, LEDs light, switches and knobs can be operated while it
runs, and the probes are the channels of a simulated device that the data view captures and flows
record. Operating point, DC and AC sweeps, transients, noise and S-parameters are computed on request.

*Circuits* is openSciLab's own plugin of the kind that adds a section (`openscilab/plugins/circuits.py`,
see [drivers.md](drivers.md#sections-and-documents-of-a-plugin)): the *Circuits* entry of the activity
bar (Ctrl+Shift+C), the document *Circuit* (`*.circuit.yaml`, *File → New → Circuit*) and the folder
`circuits/` of every project.

| What | With |
| --- | --- |
| analog and mixed simulation, analyses | [ngspice](https://ngspice.sourceforge.io) as a shared library, in a process of its own |
| digital parts | ngspice's XSPICE models, met by analog nets through bridges |
| microcontrollers | [simavr](https://github.com/buserror/simavr) (`libopenscilab_avr`), in lockstep with ngspice |
| the catalog | openSciLab's components, KiCad's symbol libraries (~23 000 parts), SPICE model files |

The examples (*Examples → Circuits*) show it step by step: an RC low pass, a transistor switch, an
inverting amplifier, a half adder, an Arduino blinking as fast as its potentiometer says, and a hybrid
AC/DC lab power supply: a set DC voltage, a set AC voltage or both, up to 230 V, at the terminals `OUT`
and `COM`. The potentiometers `RV_DC` and `RV_AC` at a 5 V reference set them; two switching regulators
(PWM) keep the rails 10 V beyond the peak the output needs, and a linear power amplifier on those rails
makes the output exact and clean - its blocks in frames with their names and comments, every connection
a wire.

## The editor

The *Circuits* section holds the circuits of the project, the catalog of components and the circuits
that run. Drag a component from the catalog onto the circuit, double-click it, or **type its name on
the canvas** (any letter opens the search; Ctrl+Shift+A too): it follows the pointer until it is
clicked down (R rotates, X mirrors, Shift places another).

| On the canvas | |
| --- | --- |
| press on a pin, or Shift+W | draws a wire from it: click its corners, click a pin, a wire or double-click to end it; `/` turns the corner the other way; Esc stops |
| Shift+L, Shift+G, Shift+P | a net label, a ground, a voltage probe (the tools are in the tool bar and the *Circuit* menu too) |
| R, X, Delete | rotate, mirror, delete the selection (with nothing selected, R and X are typed into the search) |
| drag | moves the selection; its wires grow along while it moves, wires between moved components move with them, and a pin that touched a wire in its middle, another pin or a label gets a new piece of wire from where it was: nothing loses its connection |
| Ctrl+G, Ctrl+Shift+G | put the selection into a group, take a group apart |
| Ctrl+C, Ctrl+X, Ctrl+V | copy, cut, paste (as the YAML of the file; comments and groups come along) |
| wheel, pinch, two fingers, middle button | zoom and move as in the flow editor |

**Comments and groups.** A component, a wire or a net label can carry a comment (its inspector): a
note shown beside it on the schematic, in italics. A **group** gathers components, wires and labels that
belong together - a block such as "Positive rail" - in a dashed frame with its name and comment above;
a click on the frame or its title selects it (its inspector: name, comment, an optional light background
colour - blue, green, yellow, red, purple or gray - and *Ungroup*), dragging it moves all its members
and their wires, and the frame follows them. Rotate and Mirror on a frame turn its components. Delete on
a frame asks whether the members go too or only the frame.

The inspector shows the selected component - its reference, its parameters with their units (a
value that is no value is said under its field: `10k`, `4.7 uF`, `4k7`, `1meg` are values), its unit
of a component with several, how it is simulated - or, with nothing selected, the circuit: its
settings, model libraries and analyses. Pins that are not connected are marked red, a component whose
pins are all on one net (a wire runs through it) is said to be shorted, a probe whose name another net
has already keeps its name and its net gets one of its own (two nets of one name would be one node in
the netlist); a component that cannot be simulated has a dashed frame. *Circuit → View → Netlist*
shows the SPICE netlist the circuit gives.

## Simulating

**Run** (the header, F5) simulates the circuit live: a transient without end, in **real time** (the
simulation waits for the clock) or in **virtual time** (as fast as it can). *Pause* and *Stop* act on
it. **Speed**, the slider in the tool bar of the circuit (its *Speed* setting), sets how many simulated
seconds run per second, from 0.001× to 100× - slower to watch the current flow or an edge rise, faster
for a slow circuit; the computer sets the upper limit. Moved while the circuit runs, it runs on at the
new speed at once (real time only: virtual time is as fast as it can anyway). Meanwhile

* probes show their name and voltage (`C 105 mV`: the net C against ground; logic probes 0 or 1,
  current probes the current) next to them, wires and labels the voltage of their net in their tool
  tip - four times a second, readable;
* LEDs light with their current (fully at their *Full brightness* current);
* switches toggle with a click, push buttons are closed while held, potentiometers turn by dragging
  (the wiper follows the pointer along the potentiometer, whichever way it lies; a lying one also turns
  up and down) or with the wheel;
* a changed value goes in at once (ngspice's `alter`), a changed circuit starts the simulation again,
  its time going on where it was (with *Start: power on* every restart starts as switched on; its
  microcontrollers go on where they were, with the new wiring); a probe added meanwhile gets its course
  from then on;
* oscilloscopes draw their channels (below);
* **the current flows** (*Current* in the tool bar, on by default): a glowing yellow stream runs along
  the wires and through the two-pin components - and along the pins of the others (a potentiometer, a
  transistor, a microcontroller: the current into or out of each pin) -, its waves moving in the
  direction of the conventional current (from plus to minus), wider and faster the more flows, from a
  microampere (thin, slow) to an ampere (wide, fast); a wire's tool tip says how much. Wires and pins take the colour of their voltage - grey at 0 V, green the more positive, red
  the more negative, against the largest voltage of the circuit - and a two-pin component shows the
  voltage across it as a gradient from one pin to the other.
  Every current is measured (ngspice computes the currents of the devices, an ammeter sits in front of
  each pin of a subcircuit) or follows from Kirchhoff's law; where wires form a loop the split between
  them is a choice, the sums are right. Measuring costs time (about a sixth for a large circuit): switch
  it off for the fastest run. To watch AC, lower the *Speed* (the slider) - at 50 Hz the direction
  changes faster than the eye follows;
* *Circuit → View → Results*, the entry *Live*, draws the probes;
* when ngspice gives up on the circuit (a time step too small, a singular matrix) or its process ends,
  the run stops and the banner says why.

**Oscilloscopes** (*Measure → Oscilloscope*, `probe.scope`) have four channels, measured against their
GND (a GND that is not on ground is subtracted). While the circuit runs each one draws its picture on
its symbol and on its screen below the schematic (a tab beside the serial monitor; a double-click on
the oscilloscope shows it): ten divisions of time and eight of voltage, the trigger in the middle. An
edge of the trigger channel through the level holds the picture still; without one it runs on (*auto*)
or stays as it was (*normal*). From 100 ms per division *auto* rolls - the newest values come in on the
right, as oscilloscopes do, since a triggered picture would wait seconds for its edge - and *roll* rolls
at every time base; *Hold the picture* keeps it. Beside the screen: time and volts per
division, offset, trigger channel, level, edge and mode - parameters of the component, saved with the
circuit and undone like any change - and peak to peak, mean and frequency of each channel. Its
channels are analog channels of the circuit's device too (`XSC1_CH1`, ...), against its GND like the
screen.

The probes are the channels of the device `circuit:<file>` (*Devices*, data view, flows): voltage
probes as analog inputs and as logic channels (`<name>_L`, thresholds from the supply), logic probes as
logic channels, current probes as analog inputs in milliamperes (`<name>_mA`). A flow can open a circuit
that is not open:

```yaml
nodes:
  rc:  {type: device.device, address: "circuit:circuits/filter.circuit.yaml"}
  cap: {type: device.capture, channels: [OUT_L], rate: 100 kHz, samples: 2000}
edges:
  - rc.device -> cap.device
```

**Analyses** are computed from *Circuit → Analyses* or the inspector (*Compute*) and shown under
*Results*: the operating point as a table, DC sweeps and transients as charts, AC sweeps as magnitude
(dB) and phase on a logarithmic frequency axis, S-parameters in dB, phase and on a Smith chart.
*File → Export results* writes CSV (and Touchstone `.s2p` for S-parameters). ngspice runs in a
process of its own: several circuits can run at once, nothing else in openSciLab slows it down, and a
circuit that makes ngspice crash ends only that process.

| Setting | Default | |
| --- | --- | --- |
| Time step | 10 µs | the longest step of the live simulation (with microcontrollers 1 ms: their edges get points of their own, inputs are read at least every millisecond - and up to a millisecond late, see below) |
| Longest step | - | a longest step of your own |
| Logic supply | 5 V | the supply of digital parts and microcontrollers, the thresholds of logic channels |
| Temperature | 27 °C | |
| Tolerance | 0.001 | the relative error ngspice allows per step (its `reltol`); 0.01 runs switching circuits several times faster |
| Speed | 1 | simulated seconds per second in real time |
| Start | settled | *settled*: from the operating point, as if it had run for long; *power on*: every capacitor empty, as when it is switched on (inrush, soft start - and a controller that settles from its rail at the operating point does not start there) |

**Fast enough to run live.** ngspice computes a few ten thousand steps a second; a circuit runs in real
time when it needs fewer. Switching circuits need many - every edge takes several steps - so a PWM of
50 kHz cannot run live, one of 500 Hz with a larger choke can (the lab power supply example). Live,
ngspice reads at every step only the probes and the currents of LEDs (the voltages of all nets come
30 times a second); a larger *Time step* and *Tolerance* 0.01 take fewer steps, an undamped LC (a diode
switching off into a transformer's leakage) takes many: an RC snubber across it is what a real circuit
has too.

## Components

openSciLab's own components (`openscilab/circuits/library/*.yaml`) can all be simulated:

| Family | Components |
| --- | --- |
| Passive | resistor, capacitor, electrolytic capacitor, inductor, potentiometer (a knob), transformer, variable transformer (a variac: its output in volts, a knob), crystal |
| Sources | DC voltage, sine, sine reference (its RMS from a control voltage: a potentiometer sets an AC source), square (clock, PWM), pulse, triangle (a PWM carrier), piecewise linear, DC current, battery |
| Semiconductors | diode, Zener diode, LED (lights), bridge rectifier, NPN, PNP, N- and P-channel MOSFET |
| Analog | operational amplifier, comparator, voltage regulator, 555 timer |
| Switches | switch, push button, voltage-controlled switch (a power transistor with its driver) |
| Logic | AND, NAND, OR, NOR, XOR, inverter, buffer, D flip-flop |
| Connect, measure | ground, supply rail (with its voltage), terminal (a named output socket: OUT, COM ... - measured like a voltage probe), voltage, logic and current probe, oscilloscope, net labels |
| Microcontrollers | Arduino Uno, Nano, Mega 2560 |
| Devices | device input, device output (pins of connected devices, below) |
| RF | port, transmission line, attenuator |

Models are named by the component's value: `1N4148`, `UF4007`, `2N3904`, `BC547B`, `MJE13007`,
`IRLZ44N`, `LM358`, `TL072`, `NE555`, `PA94` (a 900 V power op-amp) ... come with openSciLab (`openscilab/circuits/models/standard.lib`: typical behaviour from data
sheets, op-amps as macro models of their gain-bandwidth, slew rate and output swing). Models of makers
are added as **model libraries** - of a circuit (its inspector), or of every circuit (the folders in
*Settings → Circuits*, and `<settings>/circuits/models`): every `.model` and `.subckt` of `.lib`,
`.mod`, `.sub`, `.cir`, `.sp` files, used before openSciLab's of the same name. Each of them is in the
catalog too (`spice:<name>`): models of diodes and transistors with their symbol, subcircuits as a box
with their pins.

**KiCad's symbol libraries** are part of the catalog: those of a KiCad installation, of folders in the
settings, or - without KiCad - installed into the vendor folder on first use. The catalog reads them
once (in a process of its own) and keeps an index in the settings' cache. Which of KiCad's parts are
simulated follows KiCad: their `Sim.*` fields, else what their reference and pins say - resistors,
capacitors, inductors with a value; diodes, LEDs, transistors and MOSFETs with the model of their
value, or a standard one; and further op-amps and comparators (a macro model of the part when
openSciLab has it, a generic one else), linear regulators by their voltage (78xx, -3.3 ...), the 555,
switches and push buttons, the gates of the 74xx and 4000 series, and the microcontrollers openSciLab
emulates (ATmega328P, ATmega168, ATmega2560, Arduino Uno and Nano modules). The catalog says how a
part is simulated (*standard model*) or that it is only drawn. Parts with several units (a dual
op-amp, a 7400) are placed unit by unit (`U1A`, `U1B` ...); a supply pin that is not on the placed unit
takes the net of that pin on another unit of the part, else the net named like the pin (`V+`), as
KiCad's hidden power pins do.

Components of your own are YAML files in `<settings>/circuits/library`:

```yaml
- type: my.pullup
  title: Pull-up resistor
  category: Mine
  prefix: R
  description: A resistor to the supply.
  symbol:
    shapes:
      - {rect: [-20, -6, 20, 6]}
      - {text: "↑", at: [0, -12], size: 8}
    pins:
      - {number: "1", at: [-30, 0], angle: 0, length: 10}
      - {number: "2", at: [30, 0], angle: 180, length: 10}
  params:
    - {name: value, label: Resistance, unit: Ω, default: 10k, description: The resistance., live: true}
  spice: "{ref:R} {pin.1} {pin.2} {value}"
```

Shapes are `line` (a polyline), `rect`, `circle`, `arc` (start, a point on it, end), `bezier` and
`text` in scene units (pins 20 apart, y down); `fill` is `none`, `outline` or `background`; `when:
"state=1"` draws a shape only while a parameter has that value. A pin's `at` is where wires connect,
its `angle` points into the body. The template `spice` writes the netlist lines: `{ref:R}` the
reference with that letter in front, `{pin.N}` the net of pin N, `{name}` a parameter as a number for
SPICE, `{{expression}}` a value computed from the parameters (`{{value*position}}`); `definitions`
adds model lines, `models` names models of the model libraries, `includes` names model files to include as
they are (relative to the circuit), `digital: true` makes the pins digital, `{name=value?text}` writes
`text` only when the parameter has that value (`PWL({points}){repeat=yes? r=0}`),
`control: {kind: knob|switch|button, param: ...}` makes it operable (a knob from `min` to `max`,
numbers or names of parameters: `{kind: knob, param: value, max: maximum}`; 0 to 1 by default), `current: "@{ref:D}[id]"` names the
vector of its current.

## Connected devices (hardware in the loop)

A circuit can use the pins of the devices that are connected (*Devices*: a Pico, an Arduino, a DAQ,
also a simulated one). *Probe → Device input* is a pin the device **reads**: what it measures there -
the level of a digital input (0 V or the device's logic level) or the voltage of an analog input -
drives the net of the component while the circuit runs. *Probe → Device output* is a pin the device
**drives**: it follows the net - high above half the logic supply of the circuit, low below (with 10 %
hysteresis) - or, as an analog output (DAC), takes its voltage within the output's range. The inspector
offers the connected devices and the free pins of the chosen one that can do it; with one device
connected, the device can stay empty.

The values are exchanged 50 times a second in a thread of its own (devices answer over USB or a
network), so this is for signals of a few hertz: a button on a real board switching a simulated circuit,
a simulated filter or controller driving a real LED, relay or DAC. Problems - a device that is not
connected, a pin that cannot do it or is in use, a device that stops answering - are shown at the
component and above the schematic; the circuit runs on. When the circuit stops, the outputs it drove
are set back to safe inputs (analog outputs to 0 V). Analyses run without the devices: a device input
gives 0 V there. A `circuit:` device of a flow does not exchange values with devices.

## Microcontrollers

An Arduino (Uno, Nano, Mega 2560) or a bare ATmega of KiCad's libraries (328P, 168, 2560, 1280) runs the firmware chosen in
its inspector (*Firmware*: a `.hex` or `.elf` file - Arduino IDE: *Sketch → Export compiled binary*;
PlatformIO: `.pio/build/<board>/firmware.hex`), cycle by cycle at 16 MHz. Its pins are output stages of
the circuit: driving when the firmware makes them outputs (25 Ω), with their pull-up (35 kΩ) when it
switches that on, high impedance else; inputs read their net with the thresholds of the supply, the
analog inputs its voltage. The board's 5V (+5V, VCC) and 3V3 pins supply the circuit, its GND is the
circuit's ground. Before ngspice takes a step the microcontrollers run ahead to its end - at most the
*Time step*, 1 ms by default - and the step is shortened to their next edge, so every edge comes at its
time. Inputs follow each accepted point, which the microcontroller sees up to a *Time step* late (it has
run ahead already): fast handshakes with the circuit (a software UART, a pulse measured to the
microsecond) want a smaller *Time step*, at the cost of speed.

The **serial monitor** below the schematic shows what the firmware sends on its serial ports (Serial1 to
Serial3 of a Mega with their name in front) and sends a line to it; *Reset* starts the firmware again
as switched on - its pins drive nothing until the firmware sets them. A pin that is not connected gets
no output stage (the simulation stays fast).

## The file

```yaml
circuit: Low pass
version: 1
settings: {step: 10 us, vcc: 5 V}
libraries: [models/vendor.lib]
components:
  V1: {type: source.sine, at: [0, 0], params: {amplitude: 1 V, frequency: 1 kHz}}
  R1: {type: resistor, at: [60, -30], params: {value: 1k}}
  C1: {type: capacitor, at: [120, 0], rotation: 90, params: {value: 159n}}
  U1A: {type: "kicad:Amplifier_Operational:LM358", at: [300, 0], unit: 1}
  GND1: {type: ground, at: [0, 60]}
  P1: {type: probe.v, at: [120, -30], params: {name: OUT}}
wires:
  - [[0, -30], [30, -30]]
  - {points: [[90, -30], [120, -30]], comment: to the output}
labels:
  - {name: OUT, at: [200, -30]}
groups:
  - {name: Filter, comment: the corner at 1 kHz, color: blue, members: [R1, C1, w2]}
analyses:
  ac: {start: 10 Hz, stop: 100 kHz}
```

One line per component, wire, label and group, in a fixed order: a change of the circuit is a small
change of the file. Positions are scene units (a grid of 10); `rotation` is counter-clockwise in steps
of 90, `mirror` flips left and right before; parameters not written have their default; a `comment` may
stand on a component, a wire (then written as `{points, comment}`) or a label. A group names its members:
components by their reference, wires and labels by their place in the file (`w2`: the second wire, `l1`:
the first label; no component may be called so). Keys this version does not know are kept.

## The tools

ngspice, the AVR emulator and KiCad's symbols are found or installed into openSciLab's **vendor
folder** (`openscilab/core/vendor.py`). The applications bring ngspice and the AVR emulator, each
with its license text; a source installation gets them, and every installation KiCad's symbols, from
their own projects:

1. the path in the settings (*Settings → Circuits*),
2. `openscilab/vendor/<platform>/` - the applications bring ngspice and the AVR emulator there (the CI
   builds them with `python tools/build_vendor.py`; a release carries the sources of both); in a
   checkout that tool puts them there (ngspice from its sources on macOS and Linux, ngspice's DLL
   package on Windows; the AVR emulator needs a C compiler and avr-libc's headers),
3. `<settings>/vendor/<platform>/` - installed by openSciLab (always in the applications),
4. the system: Homebrew (`brew install libngspice`), the package manager (`apt install libngspice0`),
   a KiCad installation (its libngspice and its symbols).

What is missing (in a source installation; KiCad's symbols everywhere) is installed when it is first
needed (*Settings → Circuits → Install the tools
automatically*): ngspice when a circuit runs, the AVR emulator when a circuit with a microcontroller
runs, KiCad's symbols when the catalog is first read. Every download is checked against a pinned
SHA-256, a loaded library is never written over:

- **ngspice**: on Windows x64 ngspice's own DLL package (`ngspice-<version>_dll_64.7z` from
  ngspice's releases, unpacked with Windows' `tar`); on macOS and Linux built from ngspice's sources
  (a C compiler and make; a few minutes) - or installed with Homebrew (`brew install libngspice`) or the
  package manager (`apt install libngspice0`; S-parameters need ngspice 44 or newer).
- **The AVR emulator**: built from simavr's sources and the C shim of the package on every platform -
  a C compiler and avr-libc's headers are needed (macOS: `xcode-select --install`, `brew install
  avr-gcc`; Linux: `apt install build-essential avr-libc`; Windows: MSYS2 with `mingw-w64-x86_64-gcc`
  and `mingw-w64-x86_64-avr-libc`).
- **KiCad's symbols**: KiCad's archive of a pinned version.

A banner in the circuit says what happens and, when a tool could not be installed, what to install.

## Python

```python
from openscilab.circuits import spice, yaml_io
from openscilab.circuits.catalog import default_catalog
from openscilab.circuits.engines import analysis, live

catalog = default_catalog()
circuit = yaml_io.load("circuits/filter.circuit.yaml")
result = analysis.run_analysis(spice.emit(circuit, catalog.spec, catalog.models, analysis="ac"), "ac")
print(analysis.corner_frequency(result.x, result.traces["out"]))

simulation = live.LiveSimulation(spice.emit(circuit, catalog.spec, catalog.models), realtime=False)
simulation.start()
...
times, volts = simulation.course("v(out)")
simulation.stop()
```
