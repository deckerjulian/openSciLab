# The lab: flows, nodes, panels, reports

openSciLab is a measurement and control lab: instruments in a hub, flows that capture, process,
check and generate signals, panels that operate them, reports that document them. Everything
works with [simulated instruments](simulator.md) as well; the logic analyzer of the
[README](../README.md#the-logic-analyzer) is one document of the lab.

## The window

How the parts look and what they are called follows one set of rules: [design.md](design.md).

* **Header**: the command palette right after the logo (Ctrl+K: every command, open document and
  recent file by name), *New* (a flow; its arrow: a panel, a data view, a waveform, a project from an
  example), *Open*, *Save*, *Examples*, then **Run, Pause, Stop** for whatever the active document
  runs – a flow or a panel (*Run*), a capture in a data view or with a device (*Capture*), a
  waveform (*Play*) – and beside them *Real time | Virtual time* for a flow or panel. F5, F6,
  Shift+F5 and F10 (a step of a paused flow) do the same; the *Run* menu has them too, and *Use
  simulators for every device*. A right click (or *View → Customize the header…*) arranges it.
* **Menus**: *File* (new, open, recent, save, save as template, settings), *Edit*, the menus of the
  active document (*Flow*, or *Data*, *Analyze* and *Display* of a data view), *View*, *Run*,
  *Devices* (connect, the device list, the open devices and their data views, *Boards and
  firmware*, simulated faults), *Examples* (every example project by category) and *Help*.
* **Activity bar and sidebar** (left): *Project* (open documents, the flows, panels and waveforms
  of the project, its data, recent files) and *Devices* (connected devices with their status and
  load, the devices to connect – a click connects one –, the simulators, *Boards and firmware*); at
  the bottom the start page and the settings. A click on an entry opens it (with Ctrl or Shift held
  a click selects, e.g. two captures to compare). The parts of a section open and close with a click
  on their header; a right click on the activity bar shows or hides its entries, orders them and puts
  their names under the icons. The sidebar is as it was left (Ctrl+B shows and hides it).
* **Node palette**: beside a flow whose graph is edited (Ctrl+Shift+N hides it); its groups keep
  whether they are open (the decoders start closed).
* **Documents** in tabs, split side by side or below each other, or in windows of their own (the
  tab's menu, or drag a tab off its bar): flow, panel, data view, waveform, a device, the views of a
  running flow (*Views · flow*, a tile per view node), *Boards and firmware*.
* **Inspector** (right): properties of the selection – a node (its settings, the advanced ones
  folded away, what is wrong under the field it is wrong in), a panel widget, the flow – or the
  analysis of a data view (decoders, measure, search, markers, the capture). Where the flow knows the
  values a parameter can take (the channels and pins of the device, the clocks of its state mode,
  the channels of the capture or states wired in, the columns of a table, the states of a state
  machine) it offers a choice instead of free text; a parameter that does not count now – a wire
  sets it, or it belongs to another setting – is locked and says why in its tooltip.
* **Console** (bottom, Ctrl+J): problems (a double-click shows the node), the execution log of flows,
  the application log and a Python console with access to the shell. The status bar shows notes,
  the problems (a click opens them), the state and the time of the run, the connected devices (a
  click shows them) and which documents are unsaved.
* **Start page**: *Try a simulator*, *Connect a device*, *New flow*, *Open file…*, *Open
  project…*, every example as a tile by category (with a search; your own templates first, then
  *Start a project* with *Empty lab*, *Logic Analyzer*, *Data acquisition*, *Synchronized
  instruments* and *Remote measuring device*) and the recent files and projects (beside the tiles,
  or behind *Recent* when the page is narrow). The application opens the saved files of the last
  session again (*Settings → Startup*; devices only when asked for there); when nothing was saved
  it starts with the start page. An unsaved flow writes its files into a scratch folder (shown
  under *Data*). An example opens as a copy in a temporary project; the first *Save* (or *File →
  Save project as…*) asks where to keep it, closing its last document asks whether to keep it.
* **Settings** (*File → Settings…*, Cmd+, on macOS): theme (dark, light, like the system), text
  size, startup, mouse wheel, data folder, devices.

## Devices

A device has facets – capture, GPIO, monitor, analog inputs and outputs, generator – which follow
from the capabilities it reports ([protocols.md](protocols.md)). Devices are opened from the device
list, *Devices → Connect…* or the start page (*Try a simulator*, *Connect a device*); each opens a
document of its own. Its header has *Data view* (where its captures are set up and shown: the
**data view** shows, analyses, decodes and edits data and holds the setup of the next capture and
the capture profiles), *Use in a flow* (a device node in the open flow, or in a new one), *Stream
to disk* (a stream written to a file until it is stopped) and *Disconnect*; *Run* in the header
(F5) captures with the setup of the next capture. Its tabs: *Pins* – every pin with its capture
channel and name, and for GPIO a pin strip and table (the mode of each pin and the actions of that
mode: switch and pulse, PWM, voltage); *Signals* and *Events* (simulators); *Timing* – the time of
its samples; *Overview* – its functions (capture setup, self-test, network, restart, bootloader,
firmware, reconnect), each with what it does, its capture limits, capabilities and connection.
*Devices → Boards and firmware* lists every board with its firmware and installs the image that fits
it. On the *Pins* tab: *All outputs safe* (Esc), the monitor (*Record* puts it into a data view as
a slow capture) and *stimulus and capture* (arm a capture, also on another device, then pulse a
pin). Everything set on a device while a capture runs becomes a marker in it. Several devices are
open at once; trigger routes of the hub tell how their trigger lines are wired.

Loading a capture profile (*Profiles* in the data view) fits it to the device: its rate and length
are held within the device's limits, and its channels are renumbered onto the device's own channels
in order – the clock, buses, trigger and decoders following – so a profile made for another device
loads here with its probes re-plugged to match. A profile needing more channels than the device has
stays off; a pattern trigger whose channels no longer sit together is dropped rather than pointed at
the wrong ones. What was changed is reported when it loads.

## Flows

A flow is a graph of nodes and wires, stored as `*.flow.yaml`:

```yaml
flow: Counter
nodes:
  sim: {type: device.device, address: sim:free}
  cap: {type: device.capture, channels: [D0, D1, D8], rate: 4 MHz, samples: 20000,
        trigger: {edge: rising, source: D8}}
  scope: {type: view.scope, title: Counter}
  file: {type: data.write, path: counter.lac}
edges:
  - sim.device -> cap.device
  - cap.capture -> scope.in
  - cap.capture -> file.in
```

**Devices are nodes.** A `device.device` node stands for an instrument: its `address` is a
simulator (`sim:uno`), a device (`pico:/dev/cu.usbmodem1`, `dslogic`) or the address of an
instrument open in the device list – in real time that instrument is used as it is. Without an
address it is the project's device of the same name. Its `device` output is wired to the
`device` input of every node that uses the instrument (capture, stream, monitor, GPIO,
generator); a device wire fits only device inputs. The palette offers the open instruments, the
project's devices and every simulator under *Devices*, the device list *Add to the flow*; a node
that needs a device is wired at once when the flow has exactly one. A simulator simulates what
the node's `signals` say (`{scenario: uart, text: Hello, baud: 9600}`, `{scenario: c64}`, single
channels); `sim:uno#2` is a second Uno, `sim:pico*2` a multi device set of two boards
([simulator.md](simulator.md)).

**Channel names bend to the device.** A flow names channels and pins the way the device it was
written for names them (`D9`, `GP5`, `A0`). Run it on a device that names them differently and a
name the device does not have is matched to the next free channel of the same kind – the n-th
unknown digital name to the n-th free digital channel, an analog name to the next analog input – so
the flow runs unedited. The name you wrote stays the label, so decoders, views and panels still find
the channel, and every substitution is noted in the run's log.

The same flow in Python (the two convert into each other unchanged):

```python
from openscilab.lab import flow, nodes as n

with flow("Counter") as f:
    sim = f.device("sim", "sim:free")
    cap = n.device.capture(sim, channels=["D0", "D1", "D8"], rate="4 MHz", samples=20000,
                           trigger={"edge": "rising", "source": "D8"})
    cap.capture >> n.view.scope(title="Counter")
    cap.capture >> n.data.write(path="counter.lac")

result = f.run(fast=True)
```

`n.<group>.<name>(...)` adds a node; `a.out >> b.in` wires two ports, `a >> b` the first output
to the first free input. `f.device(name, address)` adds a device node; given to a node (`sim`, or
`uno.pin("D9")` for a pin) it is wired to the node's `device` input.

The **flow document** has three views: *Graph*, *YAML* and *Python* (a flow opened from a script
is saved as `.flow.yaml`; broken YAML is kept until it is fixed or discarded). In the graph:

* a node shows its group by the colour of its title bar and its icon, its title and – where it
  says more – its name, the settings of its summary with their values (defaults too: a new timer
  shows its interval), its ports by name; the shape of a port tells its kind (round: values and
  events, square: signals, diamond: captures and tables, hexagon: devices), its colour the type;
* nodes come from the palette (drag or double-click; the search finds them by title, group,
  keywords and description), *Add node* or Tab, or by typing a name on the canvas; they do not land
  on top of each other;
* while a wire is dragged the ports it fits light up and it snaps to them (or to the right port
  of the node it is dropped on); dropped on the canvas it offers the nodes it fits; a wire that
  does not fit says why above the graph and offers the conversion node by name (*Insert Analog*);
  an `Event` input (trigger, arm, start, stop, reset) takes any value;
* the inspector edits the selected node: its settings, the advanced ones folded away, a field per
  setting with what is wrong right under it; the steps of a sequence and the states of a state
  machine are tables (and YAML, a click away); a double-click on a node opens the same in a window;
* right-click a wire to insert a node or remove it, a port to add a connected node or disconnect
  it; cut, copy, paste, duplicate (Ctrl+D) and select all work with the wires between the nodes;
  the arrow keys move the selected nodes (Shift: farther);
* required inputs without a wire are marked, nodes show a badge for their problems and broken wires
  are drawn dashed with the reason in their tool tip; groups carry the nodes inside them and resize
  at their corner, as comments do; *Make subflow…* asks for its name;
* navigation: two fingers move, pinch zooms, the wheel zooms at the pointer, *Flow → Zoom in /
  Zoom out / Fit (Ctrl+0) / Actual size*;
* undo/redo of every edit (*Undo Add Timer*, *Undo Change interval of timer*), subflows. A running
  flow shows the state of every node and the latest values on its wires; what its view nodes show
  is in one document, *Views · flow* (a tile per view node; the scope in a data view); breakpoints
  (the dot at the right of a node's title) and single steps work in the graph. Why a flow cannot
  run, or how a run failed, appears above the graph (a missing device offers *Run with simulators*).

The **engine** runs a flow in real time or – with simulators – in virtual time (as fast as
possible, the same result every run). Values wait in mailboxes with back pressure; nodes run when
values arrive, timers and sweeps sleep in the flow's time. A flow ends when nothing can happen any
more, after `duration`, or when it is stopped. On the command line: `openscilab run` (see
[cli.md](cli.md)).

Wire types: `Digital`, `Analog`, `Scalar`, `Bool`, `Event`, `States`, `Capture`, `Table` and
`Any`, with conversions between them (`openscilab.core.signals`).

### Own nodes

A project's `nodes/*.py` add node types:

```python
from openscilab.lab import In, Out, Param, node


@node("example.double", inputs=[In("value", "Scalar", optional=False)], outputs=[Out("out", "Scalar")])
def double(value):
    return value.value * 2


@node("example.blink", outputs=[Out("level", "Bool")], params=[Param("period", "quantity", "1 s", "s")])
async def blink(ctx):
    for _ in range(3):
        ctx.emit("level", True)
        await ctx.sleep(ctx.quantity("period") / 2)
        ctx.emit("level", False)
        await ctx.sleep(ctx.quantity("period") / 2)
```

A plain function is called with the latest values of its inputs whenever one changes; an
`async def` taking `ctx` runs with the flow (`ctx.emit`, `await ctx.receive(port)`,
`await ctx.sleep(seconds)`, `ctx.param`, `ctx.quantity`, `ctx.device(name)`, `ctx.log`). For
periodic work `await ctx.sleep_until(start + n * period)` keeps the period (sleeping `period`
after each round adds the time the round took); `await ctx.device_call(device, function, ...)`
calls a device without holding the other nodes while a real one answers. The
example project (`examples/project/`) has both kinds; the node `control.python` holds such code
inside the flow. Nodes for every flow and project come from a plugin, with the same `@node`
([drivers.md](drivers.md#nodes-of-a-plugin)).

## Nodes

<!-- nodes:begin -->

Every node with its inputs and outputs; the inspector shows what each port and setting means. Written from the node registry (`python tools/gen_node_docs.py`).

### Devices

| Node | Title | In | Out | What it does |
| --- | --- | --- | --- | --- |
| `device.capture` | Capture | `device`, `arm` | `capture`, `states`, `done`, `armed` | Captures with a device: channels, rate, length and trigger. Without a wire at 'arm' it captures once when the flow starts, otherwise on every value at 'arm'. With a 'clock' channel it takes one sample on each edge of the clock instead (state mode, 'samples' states; 'rate' stamps their times) and sends them at 'states'. |
| `device.device` | Device | – | `device` | A device of the flow: its address (sim:uno, pico:/dev/cu.usbmodem1, or the address of a device open in the device list, which is then used as it is). Without an address it is the project's device of the same name. Wire 'device' to the nodes that use it. A simulator (sim:uno, sim:pico*2 for a multi device of two boards, sim:uno#2 for a second one) simulates what 'signals' says: {scenario: uart, text: Hello, baud: 9600}, {scenario: c64}, {scenario: file, path: capture.lac}, or single channels: {channels: {D5: {type: square, frequency: 1 kHz}}}. |
| `device.monitor` | Monitor | `device` | `state` | Reads pins periodically at 'rate' for 'duration' (until the flow ends with 0): a value per pin and report (1 s at 10 Hz: 10 reports, the first at once). |
| `device.stream` | Stream | `device`, `start`, `stop` | `capture`, `done` | Streams the channels of a device; blocks of samples flow while it runs. Starts with the flow (or on 'start'), ends after its duration or on 'stop'. |

### GPIO

| Node | Title | In | Out | What it does |
| --- | --- | --- | --- | --- |
| `gpio.analog_output` | Analog output | `device`, `value` | `done` | Sets an analog output to the voltage arriving at 'value' (or the parameter once). |
| `gpio.input` | Input | `device`, `trigger` | `value` | Reads a pin for every value at 'trigger' (once at the start without a wire): the level of a digital pin, the voltage of an analog input ('analog'). |
| `gpio.output` | Output | `device`, `value` | `done` | Drives an output pin to the value arriving at 'value' (≥ 0.5 is 1). Without a wire it sets 'level' once when the flow starts. |
| `gpio.pulse` | Pulse | `device`, `trigger` | `done` | 'count' pulses of 'width', one every 'period', on a pin for every value at 'trigger' (once at the start without a wire). The device times the pulses exactly. |
| `gpio.pwm` | PWM | `device`, `duty` | `done` | PWM on a pin: 'frequency' and the duty cycle (0..1) from 'duty' or the parameter; a duty of 0 stops it. |

### Generators

| Node | Title | In | Out | What it does |
| --- | --- | --- | --- | --- |
| `generator.arbitrary` | Arbitrary | `device`, `signal` | `sync`, `done` | Arbitrary points on an analog output: a formula over one pass (x from 0 to 1, numpy functions), a CSV file (one column of volts, or time and volts), or the analog signal arriving at 'signal'. 'frequency' sets the passes per second; without it a CSV file with times and a signal play at their own rate. |
| `generator.file` | Waveform file | `device`, `waveform`, `stop` | `sync`, `done` | Plays the waveform arriving at 'waveform' (from a *.wave.yaml 'file' without a wire); 'sync' marks every start, e.g. to arm a capture. |
| `generator.i2c` | I²C | `device`, `data` | `sync`, `done`, `received` | Writes 'data' to the I²C 'address' on the pins 'pins' (SCL, SDA) and reads 'read' bytes back (devices with their own I²C, TX_I2C; else the write as a pattern). |
| `generator.pattern` | Pattern | `device`, `capture` | `sync`, `done` | A digital pattern on a pattern output: SDL text per pin ('tracks', e.g. {D0: 'l5;h5;'}) at 'rate', or the capture arriving at 'capture'. 'repeat': passes (0: until stopped). |
| `generator.replay` | Replay | `device`, `capture` | `sync`, `done` | Plays a capture: its digital channels on a pattern output under their own names, or only the channels of 'pins' under new names (e.g. {CH1: D0}); or one analog channel ('analog') on an analog output. |
| `generator.spi` | SPI | `device`, `data` | `sync`, `done`, `received` | Sends 'data' as SPI mode 0 (MSB first) on the pins 'pins' (CS, SCK, MOSI); with the device's own SPI (TX_SPI) the bytes on 'miso' come back at 'received'. |
| `generator.uart` | UART | `device`, `data` | `sync`, `done`, `received` | Sends the bytes of 'data' (or of each value at 'data') as 8N1 UART frames on 'pin': with the device's own UART where it has one (TX_UART), else as a pattern on a pattern output. |
| `generator.waveform` | Waveform | `device`, `start`, `frequency`, `amplitude` | `sync`, `done` | A standard waveform (sine, square, triangle, ramp, pulse with 'duty', DC, noise with 'amplitude' as its standard deviation) on a generator output. Values at 'frequency' and 'amplitude' change it while it plays; 'start' starts it (without a wire it starts with the flow). On a square-only output (PWM) a square or pulse without 'amplitude' and 'offset' swings over the output's whole range. |

### Decoders

Every sigrok protocol decoder is a node (133 of them, e.g. `decoder.uart`, `decoder.i2c`, `decoder.spi`, `decoder.can`, `decoder.onewire_link`, `decoder.usb_packet`, `decoder.jtag`): a capture at `in`, the annotations as `events`, a `table` and `text` at the outputs. A stacked decoder (e.g. `decoder.eeprom24xx` on I²C) takes the capture itself, runs the decoder below it and needs that decoder's `channels`.

### Measurements

| Node | Title | In | Out | What it does |
| --- | --- | --- | --- | --- |
| `measure.count` | Count | `in`, `reset` | `out` | Counts: the edges of a digital signal, the events of an event stream, the rows of a table – or, for other values, the values that arrived. 'total' adds up over the flow (from 'start'); a value at 'reset' starts again. |
| `measure.duty` | Duty cycle | `in` | `out` | Share of the period the signal is high (0..1). |
| `measure.frequency` | Frequency | `in` | `out` | Frequency of a digital signal (from its periods). |
| `measure.max` | Maximum | `in` | `out` | The highest value of an analog signal (of each block of a stream). |
| `measure.mean` | Mean | `in` | `out` | The mean of an analog signal (of each block of a stream). |
| `measure.min` | Minimum | `in` | `out` | The lowest value of an analog signal (of each block of a stream). |
| `measure.peak_to_peak` | Peak to peak | `in` | `out` | Highest minus lowest value of an analog signal (of each block of a stream). |
| `measure.period` | Period | `in` | `out` | Period (rising edge to rising edge) of a digital signal. |
| `measure.pulse_width` | Pulse width | `in` | `out` | Width of the high (or low) pulses. |
| `measure.rms` | RMS | `in` | `out` | The root mean square of an analog signal (of each block of a stream). |
| `measure.setup_hold` | Setup/hold | `data`, `clock` | `setup`, `hold` | Shortest setup and hold time of 'data' around the edges of 'clock'. |

### Signal processing

| Node | Title | In | Out | What it does |
| --- | --- | --- | --- | --- |
| `signal.average` | Average | `in` | `out` | 'captures': the sample-by-sample mean of the last 'count' signals of the same length and unit (scope averaging); 'moving': a moving average over 'count' samples (without delay: placed half its length earlier). |
| `signal.debounce` | Debounce | `in` | `out` | Removes pulses shorter than 'time' (a bouncing switch becomes one edge). In a stream a change that goes on into the next block counts with its whole length (it shows from that block on). |
| `signal.derivative` | Derivative | `in` | `out` | Change per second (unit/s): each sample minus the one before, by the time between them (the first sample of a signal takes the change to the second). |
| `signal.fft` | FFT | `in` | `out` | Spectrum of an analog signal: a table of frequency and amplitude (dB or linear). The mean (DC) is taken off first, so the 0 Hz line shows only what the window leaves of it. |
| `signal.filter` | Filter | `in` | `out` | Low-pass, high-pass, band-pass, band-stop or moving average. 'fir' (windowed sinc, numpy; without delay: its output is placed half its length earlier) or 'iir' (Butterworth, needs scipy). 'cutoff' is one frequency, or [low, high]. |
| `signal.integral` | Integral | `in`, `reset` | `out` | Running integral over time (unit·s, trapezoids), across the blocks of a stream; it starts at 0 with the first sample, 'reset' starts again at 0. |
| `signal.math` | Math | `a`, `b`, `c`, `d` | `out` | An expression of the inputs a, b, c, d with numpy (e.g. 'a * 2 - b', 'sqrt(a**2 + b**2)', 'where(a > 1.5, 1, 0)', 'max(a)'). Signals are arrays, scalars numbers. Several signals are taken at the times of the first one where they all have samples (blocks of a stream once, when they have arrived); a comparison of signals gives a digital signal. |
| `signal.resample` | Resample | `in` | `out` | A signal at another rate: linear interpolation for analog, nearest sample for digital. |
| `signal.threshold` | Threshold | `in` | `out` | 1 above the threshold, 0 below; with hysteresis the level changes only beyond threshold ± hysteresis/2. |

### Conversions

| Node | Title | In | Out | What it does |
| --- | --- | --- | --- | --- |
| `convert.analog` | Analog | `in` | `out` | Logic levels as voltages: 0 becomes 'low', 1 becomes 'high'. |
| `convert.bit` | Bit | `in` | `out` | One channel of the states (values taken on the edges of a clock) as a digital signal, one sample per state. |
| `convert.channel` | Channel | `in` | `out` | One channel of a capture: 'channel' by its name, else the first one of 'kind' (any, digital, analog). |
| `convert.events` | Events | `in` | `out` | The edges of a digital signal as events. |
| `convert.number` | Number | `in` | `out` | 1 for true, 0 for false. |

### Control

| Node | Title | In | Out | What it does |
| --- | --- | --- | --- | --- |
| `control.compare` | Compare | `a`, `b` | `result`, `passed`, `failed` | Compares 'a' with 'b' (or with 'value' when 'b' has no wire): ==, !=, <, <=, >, >=, within (\|a - b\| <= tolerance), contains, startswith. Sends the result and 'passed' or 'failed'. |
| `control.python` | Python | – | – | Your own code: 'async def run(ctx)' runs with the flow (ctx.emit, await ctx.sleep, await ctx.receive(port), ctx.device(name), ctx.log); 'def on_input(ctx, port, value)' is called for values on the inputs. 'inputs' and 'outputs' name the ports. |
| `control.range` | Range | `in` | `result`, `out`, `passed`, `failed` | Checks a value against 'low' and 'high': 'result' tells whether it is inside, 'out' is the value clipped to the range, 'failed' comes for a value outside. The limits are numbers in the unit of the value, or quantities (3.3 V, 20 mA). |
| `control.sequence` | Sequence | `start` | `step`, `done`, `timeout` | Steps without code, one after the other: {set: out, value: 1} sends a value, {emit: out} an event, {wait: 100 ms} waits, {wait_for: in, timeout: 1 s} waits for a value on an input. 'repeat' runs it several times (0: for ever). |
| `control.state_machine` | State machine | – | `state`, `done` | States with what they set and when they go on. It starts in 'initial'; entering a state sends its 'enter' values on outputs of those names (enter: {red: 1, green: 0} makes the outputs red and green) and 'state' says which state it is. 'on' lists the ways out: {after: 1 s, to: green} after a time, {input: button, when: 1, to: green} when a value arrives at an input of that name; the first that happens wins. A state without 'on' ends the machine ('done'). |
| `control.sweep` | Sweep | `next`, `trigger` | `value`, `step`, `index`, `done` | Steps a value from 'start' to 'stop' (or through 'values'): sends 'value', waits 'dwell', then sends 'step'. With a wire at 'next' it waits for it before the next value; with one at 'trigger' it sweeps for every value there (a start button). |
| `control.timer` | Timer | – | `tick`, `index` | Ticks every 'interval' (after 'delay'); 'count' ticks, or endless with 0. |

### Time

How the samples of several devices get one time axis: [docs/timing.md](timing.md).

| Node | Title | In | Out | What it does |
| --- | --- | --- | --- | --- |
| `time.align` | Align | `device`, `signal`, `reference`, `edges`, `in` | `out`, `offset`, `uncertainty`, `matched` | Aligns a device (at 'device') with a reference by a sync signal both recorded: 'signal' is the signal as this device recorded it, 'reference' as the reference device did (or 'edges' of time.sync: as precise as its output). The matched edges give offset and drift (drift none: their sample clocks are shared, only the offset); the device's later samples are placed with them, and what arrives at 'in' leaves 'out' moved onto the reference's time (a capture of the device, its other channels). |
| `time.latency` | Latency | `device` | `latency`, `uncertainty` | Measures how late the samples of a device arrive: an output pin, wired to one of its channels, is switched 'repeats' times while the device streams (or captures) that channel. No edge is earlier than its command and no sample later than its arrival: half of the shortest loop is the latency. It is kept for the device at this rate (and length of a capture) and used by its captures and streams from then on. Real time only. |
| `time.sync` | Sync output | `device`, `stop` | `edges` | Drives a pin with a sync signal: edges at irregular intervals (the same for the same 'seed') that recordings of it can be matched by without ambiguity. Wire the pin to a channel of every device to align (time.align) and to the sync input of a remote device (remote.align). 'edges' are the times the edges were commanded (as precise as the output). Runs for 'duration' (0: until 'stop' or the end of the flow). |

### Remote devices

Measuring devices on other computers ([docs/remote.md](remote.md)); they run in real time.

| Node | Title | In | Out | What it does |
| --- | --- | --- | --- | --- |
| `remote.align` | Align clock | `device`, `signal`, `edges` | `offset`, `uncertainty`, `matched` | Aligns the clock of a remote device with a sync signal: the device reports the times of the signal's edges in its clock (a sync output it drives, a sync input it sees, or an input whose blocks record it). 'signal' is the same signal recorded by a channel of another device (a capture or stream); 'edges' instead takes the edge times of time.sync (as precise as its output). The matched edges give offset and drift with the precision of the recording (drift: auto fits it unless the device's sample clock is shared; none: only the offset); from then on the device's values are converted with them. 'offset' is the device time minus the local time, 'uncertainty' its scatter. |
| `remote.command` | Command | `device`, `trigger` | `result`, `done` | Calls a command of a remote device with 'args' for every value at 'trigger' (once at the start without a wire); 'result' is what it answers. |
| `remote.input` | Input | `device` | `out` | The values of an input of a remote device, with the time they were measured on the device (converted with its measured clock): numbers and truth values one by one, samples as blocks of an analog or digital signal. 'playout' waits that long for late values so the values of several devices come in the order of their time (auto: what the network needs; 0: at once). Ends after 'duration' (0: with the flow). |
| `remote.output` | Output | `device`, `value` | `done` | Sets an output of a remote device to the value arriving at 'value' (or to the parameter 'value' once at the start). The value is sent 'lead' ahead with the time it should take effect, and the device applies it at that time (auto: what the network needs, 0: as soon as it arrives). 'done' tells the time it happened. |

### Data

| Node | Title | In | Out | What it does |
| --- | --- | --- | --- | --- |
| `data.buffer` | Buffer | `in`, `trigger` | `out` | Keeps the latest values: the last 'length' seconds of a signal (blocks are joined) or the last 'count' other values. Sends the buffer on every value, or only on 'trigger'. |
| `data.bundle` | Bundle | – | `out` | Several values on one wire: a mapping of the 'fields', sent when every field has a value (or on every value with 'partial'). |
| `data.logger` | Logger | `in` | `written` | Records for a long time on disk. A .csv path: values, events and the new rows of tables as lines time, source, value (a table with several columns: a line per column), written at once and appended to the file of earlier runs. A .lac or .sr path: the samples of streams and signals (digital and analog), in a capture on disk saved when the flow ends. |
| `data.read` | Read file | `trigger` | `capture`, `table` | Reads a capture (.lac, .sr) or a table (.csv) when the flow starts, or on every value at 'trigger'. |
| `data.table` | Table | `in` | `table` | Collects values into a table. With 'columns', each column has an input and a row is complete when every column got a value (values wait in the order they came, so a column may run ahead); otherwise every value on 'in' adds rows. |
| `data.unbundle` | Unbundle | `in` | – | The fields of a bundle on wires of their own. |
| `data.write` | Write file | `in` | `written` | Writes what arrives: captures as .lac/.sr/.csv/.vcd (by the extension), tables, bundles and events as CSV, every value replacing the file; other values (numbers, text) are lines of the file, one per value of the run. 'numbered' writes name-001, name-002, ... instead, after the numbers already there. |

### Views

| Node | Title | In | Out | What it does |
| --- | --- | --- | --- | --- |
| `view.chart` | Chart | `in` | – | Values over time, like a chart recorder: numbers, events, the samples of analog and digital signals, the channels of a capture or stream (a trace each). More traces: 'inputs' (▾ picks outputs of the flow: each gets an input and its wire). |
| `view.led` | LED | `in` | – | On for true, a number of 0.5 or more, or a text like on/yes/true; off otherwise. |
| `view.log` | Log | `in` | – | Every value that arrives as a line of text. |
| `view.number` | Number | `in` | – | Shows the latest value as a number with its unit (of a signal: its last sample). |
| `view.scope` | Scope | `in` | – | Shows captures in a data view (with decoders, cursors and measurements). Blocks of a stream are joined. |
| `view.spectrum` | Spectrum | `in` | – | The spectrum of an analog signal (or a table of frequency and magnitude from signal.fft). |
| `view.table` | Table | `in` | – | Shows a table (or the rows of other values). |
| `view.xy` | XY chart | `x`, `y` | – | Points (x, y): a point for every value at 'y', with the x it belongs to – the x values in the order they came, the latest one again for more values at 'y' (a characteristic curve: several measurements per step are fine). |

### Report

| Node | Title | In | Out | What it does |
| --- | --- | --- | --- | --- |
| `report.chart` | Chart | `in` | – | A diagram in the report: an XY chart of two table columns ('x', 'y') or of (x, y) values, or the traces of a capture or signal. |
| `report.check` | Check | `in` | `result`, `passed`, `failed` | Checks the values at 'in': at least the minimum, at most the maximum, or the 'expected' value ± tolerance; 'result' tells whether it passed, 'passed' and 'failed' come as events, the report lists it under its title. With 'last' only the last value counts, checked when the flow ends: a measurement that settles. |
| `report.section` | Section | `in` | – | A heading and text in the report; values at 'in' are added below it. |
| `report.table` | Table | `in` | – | The last table (or the values) arriving at 'in' as a table in the report. |
| `report.write` | Write | `trigger` | `written`, `result` | Writes the report (sections, tables, charts, checks with passed or failed) to 'path' when the flow ends, or on every value at 'trigger'. HTML, or PDF for a .pdf path. |

### Structure

| Node | Title | In | Out | What it does |
| --- | --- | --- | --- | --- |
| `structure.comment` | Comment | – | – | A note on the canvas. |
| `structure.group` | Group | – | – | A frame around nodes that belong together. |

<!-- nodes:end -->

## Panels

A panel (`*.panel.yaml`) is the front panel of a measuring station: widgets placed freely on a
surface (tabs, groups), each bound to a port of a flow.

```yaml
panel: Characteristic curve
flow: ../flows/curve.flow.yaml
width: 960
height: 400
widgets:
  start: {kind: button, bind: sweep.trigger, title: Start, x: 16, y: 16, width: 224, height: 80}
  volts: {kind: chart, bind: mean.out, title: Mean voltage, x: 16, y: 112, width: 456, height: 272, unit: V}
  slope: {kind: number, bind: fit.slope, title: Slope, x: 488, y: 112, width: 224, height: 80, unit: V}
```

`width` and `height` are the size of the panel, `x`, `y`, `width` and `height` of a widget its
place and size on it, in pixels from the top left corner; `tab` and `group` name its tab and the
framed group it belongs to.

| Widget | Binding | Options |
| --- | --- | --- |
| `switch` | sends true/false | `value` |
| `button` | sends an event per press | – |
| `slider` | sends the value | `min`, `max`, `step`, `unit`, `value` |
| `input` | sends the number | `unit`, `value` |
| `choice` | sends the chosen option | `options`, `value` |
| `number` | shows a value | `unit`, `digits` |
| `led` | on for true or above `threshold` | `color`, `threshold` |
| `chart` | values over time (or x/y pairs) | `points`, `unit` |
| `scope` | a capture or signal as traces | – |
| `label` | text | `text` |

Controls send into an input of a node (as if a wire brought the value) or out of an output to the
inputs wired to it; displays show outputs. Controlled inputs count as wired. *Edit* shows the
surface of the panel like a GUI editor: drag widgets from the palette onto it, drag them to move
them and their eight handles to resize them. Edges and middles snap to those of the other widgets
and of the panel within 6 pixels - red guides show it - and otherwise to a raster of 8 pixels; Alt
places freely. Shift or Ctrl and a click add to the selection, a frame drawn on the free surface
selects what it touches, `Ctrl+A` all; the arrow keys move the selection by a pixel (Shift: by
the raster). *Arrange* (tool bar, right click) aligns edges or middles, distributes three or more
evenly and brings widgets to the front or the back; the corner of the panel resizes it. The
inspector places a widget exactly and binds it (ports of a fitting type, tabs and groups to
choose); a widget whose port does not exist is marked. *Operate* runs the flow (the open flow
document with its unsaved changes, else the file) until it is stopped; the panel grows or shrinks
with its window as a whole (to half its size at least, then it scrolls), F11 shows it full screen.
Charts zoom and move like the waveform; a double-click goes back to the automatic range.

## Signal generation

A waveform (`*.wave.yaml`, `openscilab.core.waveform`) is a standard shape (sine, square, triangle,
ramp, pulse, DC, noise) with sweep and burst, arbitrary points (formula over one pass with `x`
from 0 to 1, CSV file, a captured channel), or a digital pattern (SDL per pin, a capture, UART/SPI/
I²C blocks). The **waveform document** builds it with a preview, lists the generator outputs of all
instruments, explains what does not fit an output and starts it. *Play as signal* in the
analyzer turns a capture into a waveform. In flows: the `gen.*` nodes.

## Reports

`report.section`, `report.table`, `report.chart` (diagrams as SVG) and `report.check` collect a
report, `report.write` writes it as HTML or PDF with passed or failed. `openscilab run --report`
puts the same parts into its report and ends with 1 when a check failed.

## Projects

```
my-lab/
├── project.yaml     devices, settings, simulation wiring, files to open
├── flows/           *.flow.yaml, Python flows
├── panels/          *.panel.yaml
├── nodes/           own nodes
├── waveforms/       *.wave.yaml, *.sdl
├── data/            captures, tables, logs, reports (listed in the sidebar)
└── tests/           flow tests against the simulator
```

Devices named in `project.yaml` are available to every flow of the project; `simulation.wiring`
adds wires to simulated circuits, also between two simulated devices
(`scope: [{from: "uno:A0", to: CH1}]`). A new project starts as a copy of one of the library
(`examples/library`); `open` lists the files it opens, and `data` a data view that captures with the
connected device (the *Logic Analyzer*).
