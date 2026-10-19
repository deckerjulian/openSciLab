# neurosim – Plugin für openSciLab zur Simulation funktioneller neurologischer Systeme

Stand: 2026-10-19 · Status: Planungsdokument, noch kein Code

> Zweck: neurosim bildet neurologische Systeme (Gefäße, Liquor, Transmittersysteme,
> Kleinhirn, sensible, motorische und vegetative Systeme bis auf Zellebene) als
> Knoten-Graphen im openSciLab-Editor ab, simuliert sie mit Modellen aus der Literatur
> und erzeugt daraus klinische Messsignale (EMG, Neurographie, EEG, Farbduplex/TCD).
> Jeder Parameter und jede Gleichung trägt eine überprüfbare Quelle.

---

## 0. Leitlinien

1. **Rahmen statt Katalog.** Der Kern (Ebenen, Läsionen, Instrumente, Wissensbasis) ist
   endlich. Systeme sind Module, die diesen Rahmen benutzen. Nie läuft „das ganze
   Gehirn“, sondern nur der Ausschnitt, den eine Fragestellung braucht.
2. **Keine Zahl ohne Quelle.** Jeder Parameter hat ein `source`-Feld. Parameter ohne
   Quelle sind im Editor sichtbar markiert und blockieren den Bericht-Export.
3. **KI extrahiert, der Mensch bestätigt.** Automatisch aus PDFs gewonnene Fakten sind
   Vorschläge im Review-Dialog, nie direkt Wissensbasis.
4. **Der simulierte Patient ist ein Instrument.** Er meldet Fähigkeiten (Kanäle,
   Abtastraten, Einheiten) wie Rigol oder Arduino und liefert typisierte Signalströme.
   Damit funktionieren alle vorhandenen Panels, Knoten und Analysen unverändert.
5. **Ein Modell, mehrere Ansichten.** Ein neurosim-Flow ist YAML; der Circuits-Editor
   und die Python-DSL sind zwei Ansichten darauf (wie bei openSciLab-Flows).
6. **Circuits-Analogie zuerst.** Gefäße und Liquor sind elektrische Netzwerke
   (Druck = Spannung, Fluss = Strom, Widerstand = R, Compliance = C, Trägheit = L).
   Der Circuits-Editor bekommt dafür eine neue Physik, keinen neuen Editor.
7. **Nichts nachbauen, was es als Modell gibt.** Import von CellML, SBML und NeuroML,
   damit publizierte Modelle (Physiome Model Repository, ModelDB) direkt nutzbar sind.

---

## 1. Drei Achsen des Rahmens

### 1.1 Ebenen (Zoomstufen)

Jeder Knoten hat feste Schnittstellen (Ports mit Typ, Einheit, Zeitbasis) und
austauschbare Implementierungen auf verschiedenen Ebenen:

| Ebene | Was wird gerechnet | Typische Modelle |
| --- | --- | --- |
| `molecular` | Transmitterspiegel, Rezeptorkinetik, Pharmakokinetik | Kompartiment-ODEs, Hill-Kinetik |
| `cellular` | Einzelzellen, Axone, Synapsen | Hodgkin-Huxley-Typ, MRG-Doppelkabel-Axon, Endplatte |
| `population` | Raten, neuronale Massen, Motor-Unit-Pools | Jansen-Rit, Wilson-Cowan, Fuglevand |
| `organ` | Kompartimente, Hydraulik, Reflexe | Windkessel, Marmarou, Ursino-Lodi, Baroreflex |

Beispiel: Ein Knoten `Nervus medianus` läuft als Leitungsverzögerung (`population`)
oder aufgeklappt als Bündel aus n Axonen mit Durchmesserverteilung (`cellular`).
Die Ports (Eingang: Spikezug oder Rate; Ausgang: dito, plus Feldpotential) sind gleich.

### 1.2 Systeme (Katalog-Module)

Jedes Modul liefert: Knotentypen, Referenz-Flows, Parametersätze mit Quellen,
Validierungsdaten, Testfälle.

| Modul | Kerninhalt |
| --- | --- |
| `vascular` | Windkessel, Autoregulation, Circulus Willisii, CO2-Reaktivität, Stenose/Spasmus |
| `csf` | Liquorproduktion/-resorption, Druck-Volumen-Beziehung, Monro-Kellie-Bilanz |
| `basal_ganglia` | Striatum D1/D2, GPe, GPi, STN, SNc/SNr, Thalamus; direkter/indirekter/hyperdirekter Pfad |
| `cerebellum` | Moos-/Kletterfasern, Körnerzellen, Purkinje-Zellen, Kleinhirnkerne, untere Olive |
| `somatosensory` | Rezeptor → Spinalganglion → Hinterstrang / Tractus spinothalamicus → Thalamus → S1 |
| `motor` | M1 → Pyramidenbahn → Alpha-Motoneuron → motorische Einheit → Muskel; Renshaw, Ia-Afferenz |
| `autonomic` | Baroreflex, Sympathikus/Parasympathikus, Herzfrequenzvariabilität, Blasenregulation |
| `cortex` (später) | Kortexareale als neuronale Massen, thalamokortikale Schleife |
| `brainstem`, `vestibular`, `visual` (später) | nach Katalogprinzip |

### 1.3 Läsionen (Primitive für Krankheiten)

Wenige generische Läsionstypen, auf jeden passenden Knoten anwendbar:

| Läsion | Angreifende Parameter | Beispiel-Krankheiten |
| --- | --- | --- |
| `demyelination` | Myelindicke, Internodienlänge, paranodale Leckleitfähigkeit; räumlich fleckig oder diffus | MS (zentral), CIDP, GBS (peripher) |
| `axonal_loss` | Faserzahl, Durchmesserverteilung, längenabhängig | axonale Polyneuropathie, Diabetes |
| `cell_loss` | Zellzahl pro Population, Verlauf über Zeit | Purkinje (SCA, paraneoplastisch), Motoneuron (ALS), SNc (Parkinson) |
| `channelopathy` | einzelne Leitfähigkeiten (gNa, gK, gCa …), Kinetik | Myotonien, episodische Ataxien |
| `synaptic` | Quantenfreisetzung, Rezeptordichte, Wiederaufnahme | Myasthenie, Lambert-Eaton, Botulismus |
| `reinnervation` | kollaterale Aussprossung → MU-Größe, Faserdichte | chronisch neurogen (ALS, nach Trauma) |
| `neuronopathy` | Zellkörper im Spinalganglion, nicht Axon | sensible Ganglionopathie (Sjögren, Cisplatin) |
| `compression` | lokale Demyelinisierung + Block auf Segment | Karpaltunnelsyndrom, Ulnarisneuropathie |
| `transmitter_deficit` | Tonus eines Transmittersystems | Dopamin (Parkinson), Acetylcholin |

Eine Krankheit ist ein benanntes Bündel aus Läsionen + Zielknoten + zeitlichem
Verlauf + erwarteten Befunden. Sie liegt als eigene YAML-Datei mit Quellen vor.

---

## 2. Virtuelle Instrumente

Messknoten mit Vorwärtsmodell. Jedes Instrument meldet sich wie ein echtes Gerät an
(Fähigkeiten, Kanäle, Abtastrate, Einheit) und liefert openSciLab-Signalströme.

| Instrument | Eingang aus Modell | Vorwärtsmodell | Ausgaben |
| --- | --- | --- | --- |
| `emg_needle` / `emg_surface` | Spikezüge der motorischen Einheiten, Faserpositionen | MUAP-Formen aus Faser-APs, Volumenleiter (analytisch, Farina-Merletti; später FEM), Elektrodengeometrie | Roh-EMG, MUP-Amplitude/-Dauer/-Phasen, Rekrutierung, Interferenzmuster, Fibrillationen, Faszikulationen |
| `ncs` (Neurographie) | Axonbündel mit Durchmesserverteilung | Stimulus → Leitungszeit pro Axon → Summation am Ableitort | NLG, distale Latenz, CMAP/SNAP-Amplitude, Dispersion, Leitungsblock, F-Welle, H-Reflex |
| `evoked` | Leitungsketten (SEP, MEP, VEP, AEP) | Latenz-Summation + Generatormodell | Latenzen, Amplituden, zentrale Leitungszeit |
| `eeg` | Kortexareale als neuronale Massen | Lead-Field (Kugelkopf 3-Schalen, später BEM) | Kanäle nach 10-20, Grundrhythmus, Verlangsamung, epileptiforme Dynamik |
| `duplex` / `tcd` | Druck/Fluss im Gefäßsegment | Womersley-Profil → Geschwindigkeitsverteilung → Doppler-Spektrum (Insonationswinkel, Gate, spektrale Verbreiterung) | PSV, EDV, Vmean, RI, PI, Lindegaard-Index, Stenosejet, Spasmusverlauf; B-Bild nur schematisch |
| `autonomic_tests` | Herzfrequenz, Blutdruck aus Baroreflex-Modell | Testprotokolle als Stimulus-Skripte | HRV (Zeit/Frequenz), Valsalva-Ratio, Kipptisch, Schellong, Sympathische Hautantwort |
| `icp` | Liquor/Gefäßmodell | direkt | ICP-Kurve, Plateauwellen, PVI, Infusionstest |

Realaufnahmen (z. B. eigenes EMG-Gerät später über openSciLab-Treiber) lassen sich
im selben Panel neben die Simulation legen.

---

## 3. Architektur und Paketstruktur

```
openscilab/
  plugins/
    neurosim/
      __init__.py             # Plugin-Registrierung (Knoten, Instrumente, Physiken)
      core/
        model.py              # Node, Port, Parameter, Lesion, Experiment (Datenklassen)
        schema/               # JSON-Schemas für die YAML-Formate (Abschnitt 4)
        solver.py             # ODE/DAE-Integration, Stiff-Solver, Ereignisse, Zeitbasis
        coupling.py           # Kopplungsknoten zwischen Domänen
        provenance.py         # Quellenbindung, Prüfung „keine Zahl ohne Quelle“
      physics/
        hydraulic.py          # R, C, L, Starling-Widerstand, Druckquelle, Fluss-Sensor
        neural_rate.py        # Populationsknoten, Projektion (Transmitter, Vorzeichen, Gewicht, Delay)
        neuron_adapter.py     # YAML-Knoten → NEURON-Sektionen, Mechanismen, Synapsen, Ausgaben
      nmodl/                  # .mod-Dateien nach Modul, mit Quelle und ModelDB-Nummer im Kopf
        pk.py                 # Pharmakokinetik (später)
      modules/
        vascular/  csf/  basal_ganglia/  cerebellum/
        somatosensory/  motor/  autonomic/
          nodes.py            # Knotentypen des Moduls
          reference_flows/    # YAML-Flows aus der Literatur
          params/             # Parametersätze (YAML) mit Quellen
          validation/         # Zielkurven + Tests (pytest)
      lesions/
        demyelination.py  axonal_loss.py  cell_loss.py  ...
      instruments/
        emg.py  ncs.py  evoked.py  eeg.py  duplex.py  autonomic_tests.py  icp.py
        volume_conductor/     # analytisch + FEM-Schnittstelle
        leadfield/
      knowledge/
        store.py              # SQLite + Dateiablage (PDFs, Snapshots)
        ingest/
          pdf.py              # Text/Tabellen (pdfplumber, Camelot/Tabula), OCR-Fallback
          web.py              # URL + Abrufdatum, lokaler Snapshot
          extract.py          # LLM-gestützte Faktvorschläge (Wert, Einheit, Seite, Zitat)
        review.py             # Review-Dialog-Logik (akzeptieren, korrigieren, verwerfen)
        export.py             # Bibliographie (BibTeX, CSL-JSON), Parametertabellen
      importers/
        cellml.py  sbml.py  neuroml.py
      ui/
        physics_palette/      # Symbole für Hydraulik und neurale Physik im Circuits-Editor
        evidence_panel.py     # „Woher kommt diese Zahl?“
        lesion_panel.py       # Läsionen auf Knoten legen, Verlauf über Zeit
        experiment_panel.py   # gesund vs. krank, Parameter-Sweeps
        patient_instrument.py # Instrumentenfenster des simulierten Patienten
      report/
        builder.py            # Simulation → Bericht (Markdown/PDF) mit Bibliographie
```

Abhängigkeiten: numpy, scipy (solve_ivp: `Radau`/`BDF` für steife Systeme),
pydantic (Schema), pdfplumber, pybtex oder citeproc-py, optional NEURON (python-Modul)
als externer Solver für `conductance`-Modelle, optional libsbml/libcellml.

### 3.1 Solver

- Jedes Modell ist ein System `dx/dt = f(x, u, p, t)` plus algebraische Beziehungen.
- Der Graph wird vor dem Lauf zu einem Gesamtsystem zusammengefasst
  (Domänen-Kopplung über Kopplungsknoten, Abschnitt 3.2).
- Zeitbasen: `organ` im Sekunden- bis Minutenbereich, `cellular` in Mikrosekunden.
  Gemischte Flows laufen als Multi-Rate-Simulation: schnelle Teilsysteme in eigenen
  Zeitfenstern, Austausch über gemittelte Größen (Rate, Mittelwert, Hüllkurve).
- Ereignisse (Spikes, Stimuli, Testprotokolle) über die Event-Schnittstelle von
  `solve_ivp` oder eigenem Fixed-Step-Integrator für Spiking-Modelle.
- Ausgabe: typisierte Signalströme (Typ, Einheit, Zeitbasis) ins openSciLab-Signalmodell.

### 3.2 Kopplungsknoten

| Kopplung | Verbindet | Modell |
| --- | --- | --- |
| `monro_kellie` | vascular ↔ csf ↔ Hirngewebe | Volumenbilanz, nichtlineare Druck-Volumen-Kurve (Marmarou) |
| `neurovascular` | population ↔ vascular | Aktivität → Arteriolenwiderstand (Vereinfachung, Quelle nötig) |
| `dopamine_gain` | molecular ↔ basal_ganglia | Dopaminspiegel → Verstärkung D1/D2-Projektionen |
| `rate_to_spikes` / `spikes_to_rate` | population ↔ cellular | Poisson-Erzeugung, gleitende Rate |
| `baroreflex` | autonomic ↔ vascular | Baroreflex-Schleife (Ursino 1998) |

### 3.3 NEURON-Adapter für die Ebene `cellular`

Entscheidung: NEURON (Python-Modul `neuron`, Zugriff über `from neuron import h`) rechnet
alle leitfähigkeitsbasierten Modelle. Eigener Kabel-Solver entfällt.

- `physics/conductance.py` wird zum Adapter `neuron_adapter.py`: übersetzt YAML-Knoten
  in NEURON-Sektionen (Soma, Dendriten, Axon mit Knoten/Internodien), setzt Mechanismen
  und Parameter, verdrahtet Synapsen (`NetCon`, `ExpSyn`/`Exp2Syn`), sammelt Ausgaben
  (`h.Vector`) und übergibt sie als openSciLab-Signalströme.
- Mechanismen (NMODL, `.mod`) liegen unter `plugins/neurosim/nmodl/` nach Modul sortiert;
  Build über `nrnivmodl` beim ersten Start oder per `make`, Build-Status im Plugin sichtbar.
  Jede `.mod`-Datei trägt im Kopf Quelle und ModelDB-Nummer.
- Extrazelluläre Felder: `extracellular`-Mechanismus und `xtra` für Stimulation von außen
  (Neurographie, DBS) und für Transmembranströme, aus denen Volumenleiter-Modelle das
  Elektrodenpotential berechnen (Nadel-EMG, Neurographie).
- Zeitintegration: CVODE (variabler Schritt) für Einzelzellen, fester Schritt für Netze;
  Kopplung an den übergeordneten Multi-Rate-Solver über Mittelwerte und Spikezeiten.
- Modelle aus ModelDB/Physiome werden als Ganzes importiert (Ordner mit `.mod` und
  `.hoc`/`.py`) und in einem Wrapper-Knoten gekapselt; die Parameter, die Läsionen
  verändern dürfen, sind im Knoten ausdrücklich freigegeben und mit Quelle belegt.
- Ohne installiertes NEURON: Knoten mit `level: cellular` sind im Editor ausgegraut,
  der Flow läuft mit der nächstgröberen Ebene weiter, der Bericht vermerkt das.
- Parallelisierung über `ParallelContext`/MPI erst, wenn Netze (Phase 4) es brauchen.

---

## 4. Datenschema (YAML)

### 4.1 Quelle

```yaml
# knowledge/sources/westerhof2009.yaml
id: westerhof2009
type: article            # article | book | chapter | web | dataset | model_repo
authors: [Westerhof N, Lankhaar JW, Westerhof BE]
title: The arterial Windkessel
journal: Med Biol Eng Comput
year: 2009
volume: 47
issue: 2
pages: 131-141
doi: 10.1007/s11517-008-0359-2
file: knowledge/files/westerhof2009.pdf     # lokale Kopie, falls vorhanden
retrieved: 2026-10-19                        # Pflicht bei type: web
license_note: nur Zitat, keine Weitergabe des PDFs
```

### 4.2 Parameter mit Herkunft

```yaml
# modules/vascular/params/windkessel_human.yaml
set: windkessel_human_adult
parameters:
  - name: R_peripheral
    value: 1.05
    unit: mmHg*s/mL
    source:
      ref: westerhof2009
      locator: "Tab. 1, S. 134"          # Seite, Tabelle, Abbildung oder Gleichung
      quote: "..."                        # kurzes Originalzitat zur Nachprüfung
      provenance: measured                # measured | derived | fitted | estimated | assumed
      population: gesunde Erwachsene
      confidence: high                    # high | medium | low
    range: [0.8, 1.4]                     # falls angegeben
    notes: ""
```

Regeln:
- `provenance: assumed` oder fehlendes `source` → Knoten im Editor gelb markiert,
  Bericht-Export mit Warnliste.
- Parameter aus einem Modell-Repository (CellML/ModelDB) bekommen `source.ref` auf das
  Repository **und** auf das Originalpaper.

### 4.3 Knoten und Flow

```yaml
# modules/motor/reference_flows/median_nerve_motor.yaml
flow: median_nerve_motor
physics: [neural_rate, conductance]
nodes:
  - id: mn_pool
    type: motor.motoneuron_pool
    level: population                  # oder cellular
    params: {set: fuglevand_fdi_120mu}
    ports: {in: drive, out: spikes}
  - id: n_medianus
    type: motor.myelinated_bundle
    level: cellular
    params: {set: mrg_human_motor, n_axons: 120, diameter_dist: hursh_normal}
    geometry: {length_mm: 250, nodes_every_mm: 1.0}
  - id: nmj
    type: motor.neuromuscular_junction
    level: cellular
  - id: muscle
    type: motor.muscle_fibers
    params: {set: fdi_fibers}
edges:
  - {from: mn_pool.spikes, to: n_medianus.in}
  - {from: n_medianus.out, to: nmj.in}
  - {from: nmj.out, to: muscle.in}
instruments:
  - id: emg
    type: instrument.emg_needle
    attach: muscle
    position_mm: [10, 5, 3]
  - id: ncs
    type: instrument.ncs
    stimulate: {node: n_medianus, at_mm: [60, 250]}   # Handgelenk, Ellenbogen
    record: muscle
```

### 4.4 Läsion und Krankheit

```yaml
# diseases/cidp.yaml
disease: CIDP
sources: [ref_cidp_criteria, ref_demyelination_model]
lesions:
  - type: demyelination
    target: n_medianus
    pattern: patchy                      # patchy | diffuse | focal
    severity: 0.6                        # 0..1, Bedeutung in lesions/demyelination.py dokumentiert
    params:
      myelin_thickness_factor: 0.4
      internode_length_factor: 0.7
    source: {ref: ref_demyelination_model, locator: "S. 5, Gl. 3"}
timeline:
  - {t: 0,   severity: 0.2}
  - {t: 90d, severity: 0.6}
expected_findings:                        # dient der Validierung
  ncs: {nlg_reduced: true, dispersion: true, block_possible: true}
  emg: {fibrillations: sparse}
```

### 4.5 Experiment

```yaml
experiment: cidp_vs_healthy
flow: median_nerve_motor
arms:
  - {name: healthy}
  - {name: cidp, disease: diseases/cidp.yaml, at: 90d}
sweep: {lesion.severity: [0.2, 0.4, 0.6, 0.8]}
observe: [ncs.nlg, ncs.cmap_amplitude, ncs.dispersion, emg.mup_duration]
report: true
```

---

## 5. Wissensbasis und Ingest

### 5.1 Ablauf

1. Datei oder URL ablegen (`knowledge/inbox/`).
2. `ingest/pdf.py`: Text und Tabellen seitenweise extrahieren, Seitenzahlen behalten,
   OCR nur bei Scans. Bei Büchern: Kapitel/Seite als Locator.
3. `ingest/extract.py`: Faktvorschläge erzeugen (Parameter, Wert, Einheit, Population,
   Locator, Zitat). Modell: lokal oder API, austauschbar. Jede Ausgabe hat einen
   Locator, sonst wird sie verworfen.
4. `review.py` + UI: Vorschlag ansehen, Originalstelle daneben (PDF-Seite gerendert),
   akzeptieren/korrigieren/verwerfen. Erst dann Eintrag in `store.py`.
5. Store: SQLite (Quellen, Fakten, Parameter-Sets, Revisionen) + Dateiablage.
6. Jede Änderung ist versioniert (wer, wann, Grund).

### 5.2 Online-Quellen

- Suche liefert Kandidaten mit URL; Übernahme nur mit Abrufdatum und lokalem Snapshot.
- Bevorzugt: Originalpaper (DOI), Lehrbuch, Leitlinie, Modell-Repository. Sekundärquellen
  (Reviews, Wikipedia) nur als Einstieg, Parameter daraus mit `confidence: low`.
- Modell-Repositories: Physiome Model Repository (CellML), ModelDB (NEURON),
  BioModels (SBML), Open Source Brain (NeuroML).

### 5.3 Evidenz-Panel

- Klick auf Knoten → Liste aller Parameter mit Quelle, Locator, Zitat, Konfidenz.
- Klick auf Quelle → PDF-Seite oder Snapshot öffnet sich an der Stelle.
- Filter: „nur ungesicherte Parameter“, „nur Schätzungen“, „nur aus Modell-Repo“.

### 5.4 Bericht

- Aus Experiment: Methodik (alle Gleichungen mit Quelle), Parametertabelle, Ergebnisse,
  Abbildungen, Bibliographie (BibTeX + formatiert), Warnliste ungesicherter Annahmen.
- Reproduzierbar: Bericht enthält Hash des Flows, der Parametersätze und der Plugin-Version.

---

## 6. Referenzmodelle pro Modul

Die Liste ist der Startpunkt für den Ingest. DOIs, die nicht bereits im Dokument stehen,
werden beim Ingest ergänzt und geprüft (Regel: nichts aus dem Gedächtnis eintragen).

### 6.1 Grundlagen (alle Module)
- Hodgkin AL, Huxley AF (1952). J Physiol 117:500–544. Leitfähigkeitsmodell.
- Hines ML, Carnevale NT (1997). The NEURON simulation environment. Neural Comput 9:1179–1209.
- Sanz Leon P et al. (2013). The Virtual Brain. Front Neuroinform 7:10. (Vorbild für Kortex-/Konnektom-Ebene)
- Kandel ER et al. Principles of Neural Science (aktuelle Auflage) — Lehrbuch-Locator für Anatomie/Physiologie.

### 6.2 vascular
- Westerhof N, Lankhaar JW, Westerhof BE (2009). The arterial Windkessel. Med Biol Eng Comput 47(2):131–141. doi:10.1007/s11517-008-0359-2.
- Lassen NA (1959). Cerebral blood flow and oxygen consumption in man. Physiol Rev 39:183–238. (Autoregulation)
- Womersley JR (1955). Method for the calculation of velocity, rate of flow and viscous drag in arteries when the pressure gradient is known. J Physiol 127:553–563. (Flussprofil für Duplex)
- Aaslid R, Markwalder TM, Nornes H (1982). Noninvasive transcranial Doppler ultrasound recording of flow velocity in basal cerebral arteries. J Neurosurg 57:769–774.
- Lindegaard KF et al. (1989). Cerebral vasospasm diagnosis by means of angiography and blood velocity measurements. Acta Neurochir (Wien) 100:12–24. (Lindegaard-Index)

### 6.3 csf
- Marmarou A, Shulman K, Rosende RM (1978). A nonlinear analysis of the cerebrospinal fluid system and intracranial pressure dynamics. J Neurosurg 48:332–344.
- Ursino M, Lodi CA (1997). A simple mathematical model of the interaction between intracranial pressure and cerebral hemodynamics. J Appl Physiol 82(4):1256–1269. (Plateauwellen, PVI-Test, Autoregulation)
- Ursino M, Lodi CA (1998). Interaction among autoregulation, CO2 reactivity, and intracranial pressure: a mathematical model. Am J Physiol 274:H1715–H1728.

### 6.4 basal_ganglia
- Albin RL, Young AB, Penney JB (1989). The functional anatomy of basal ganglia disorders. Trends Neurosci 12:366–375.
- DeLong MR (1990). Primate models of movement disorders of basal ganglia origin. Trends Neurosci 13:281–285.
- Gurney K, Prescott TJ, Redgrave P (2001). A computational model of action selection in the basal ganglia I + II. Biol Cybern 84:401–410 und 411–423.
- Terman D, Rubin JE, Yew AC, Wilson CJ (2002). Activity patterns in a model for the subthalamopallidal network of the basal ganglia. J Neurosci 22:2963–2976.
- Rubin JE, Terman D (2004). High frequency stimulation of the subthalamic nucleus eliminates pathological thalamic rhythmicity in a computational model. J Comput Neurosci 16:211–235. (DBS)

### 6.5 cerebellum
- De Schutter E, Bower JM (1994). An active membrane model of the cerebellar Purkinje cell I + II. J Neurophysiol 71:375–400 und 401–419.
- Medina JF, Mauk MD (2000). Computer simulation of cerebellar information processing. Nat Neurosci 3 Suppl:1205–1211.
- Ito M. The Cerebellum and Neural Control (1984) — Lehrbuch-Locator.

### 6.6 motor (inkl. EMG, Neurographie)
- Henneman E, Somjen G, Carpenter DO (1965). Functional significance of cell size in spinal motoneurons. J Neurophysiol 28:560–580. (Größenprinzip)
- Fuglevand AJ, Winter DA, Patla AE (1993). Models of recruitment and rate coding organization in motor-unit pools. J Neurophysiol 70:2470–2488.
- Booth V, Rinzel J, Kiehn O (1997). Compartmental model of vertebrate motoneurons for Ca2+-dependent spiking and plateau potentials. J Neurophysiol 78:3371–3385.
- Cisi RRL, Kohn AF (2008). Simulation system of spinal cord motor nuclei and associated nerves and muscles (ReMoto). J Comput Neurosci 25:520–542.
- McIntyre CC, Richardson AG, Grill WM (2002). Modeling the excitability of mammalian nerve fibers: influence of afterpotentials on the recovery cycle. J Neurophysiol 87(2):995–1006. (MRG-Axon; auch als CellML im Physiome-Repo und in ModelDB verfügbar)
- Hursh JB (1939). Conduction velocity and diameter of nerve fibers. Am J Physiol 127:131–139.
- Farina D, Merletti R (2001). A novel approach for precise simulation of the EMG signal detected by surface electrodes. IEEE Trans Biomed Eng 48(6):637–646.
- Farina D, Mesin L, Martina S (2004). Advances in surface electromyographic signal simulation with analytical and numerical descriptions of the volume conductor. Med Biol Eng Comput 42.
- Merletti R, Parker PA (Hrsg.) (2004). Electromyography: Physiology, Engineering, and Noninvasive Applications. Wiley-IEEE. (Kap. 8: EMG Modeling and Simulation)
- Kimura J. Electrodiagnosis in Diseases of Nerve and Muscle (aktuelle Auflage) — Normwerte, Befundmuster.
- Stålberg E, Sanders DB et al. Lehrbuch/Normwerte für Nadel-EMG (MUP-Dauer, Amplitude).

### 6.7 somatosensory
- Kandel (s. o.) für Bahnverlauf; SEP-Normwerte aus Kimura.
- Spinalganglion-Modelle: aus ModelDB auswählen (DRG-Neuron, Hodgkin-Huxley-Typ) — Quelle beim Ingest festlegen.

### 6.8 autonomic
- Ursino M (1998). Interaction between carotid baroregulation and the pulsating heart: a mathematical model. Am J Physiol 275:H1733–H1747.
- Heldt T, Shim EB, Kamm RD, Mark RG (2002). Computational modeling of cardiovascular response to orthostatic stress. J Appl Physiol 92:1239–1254. (Kipptisch)
- Task Force ESC/NASPE (1996). Heart rate variability: standards of measurement. Circulation 93:1043–1065.

### 6.9 cortex / EEG
- Jansen BH, Rit VG (1995). Electroencephalogram and visual evoked potential generation in a mathematical model of coupled cortical columns. Biol Cybern 73:357–366.
- Wendling F et al. (2002). Epileptic fast activity can be explained by a model of impaired GABAergic dendritic inhibition. Eur J Neurosci 15:1499–1508.
- Lead-Field: Kugelkopfmodell (3 Schalen), Referenz beim Ingest festlegen (z. B. MNE-Python-Dokumentation als Implementierungsquelle, Originalarbeiten für Leitfähigkeiten).

---

## 7. Phasen

Jede Phase endet mit lauffähigem Plugin, Tests und mindestens einem Referenz-Flow mit
vollständiger Quellenbindung.

### Phase 1 – Kern
- Datenklassen und Schemas (Abschnitt 4), Provenance-Prüfung.
- Solver mit Multi-Rate und Ereignissen; Signalausgabe in openSciLab.
- Circuits-Editor: Physiken `hydraulic` und `neural_rate` als Palette, Ports mit Einheiten.
- Wissensbasis: Store, manuelles Anlegen von Quellen/Parametern, Evidenz-Panel.
- Simulierter Patient als Instrument registriert (noch ohne Messknoten).
- Läsions-Mechanik generisch (Läsion auf Knoten legen, Timeline).
- Abnahme: Windkessel-Flow läuft, jede Zahl hat Quelle, Bericht exportiert.

### Phase 2 – Peripheres motorisch-sensibles System + EMG/Neurographie
- `conductance`-Physik, MRG-Axon (Import aus CellML als Start), Axonbündel mit
  Durchmesserverteilung, Motoneuron-Pool (Fuglevand), Endplatte, Muskelfaser.
- Instrumente `ncs`, `emg_needle`, `emg_surface` (analytischer Volumenleiter), `evoked` (SEP).
- Läsionen: demyelination, axonal_loss, compression, cell_loss (Motoneuron), reinnervation, synaptic.
- Krankheiten: CIDP, GBS, axonale PNP, Karpaltunnel, ALS, Myasthenie, Ganglionopathie.
- Validierung: NLG/Latenz/Amplitude gegen Normwerte (Kimura), MUP-Kennwerte gegen
  Nadel-EMG-Normwerte, Befundmuster gegen `expected_findings`.

### Phase 3 – Gefäße/Liquor + Duplex/TCD/ICP
- Marmarou- und Ursino-Lodi-Modelle als Referenz-Flows, Monro-Kellie-Kopplung,
  Autoregulation, CO2-Reaktivität, Circulus Willisii.
- Instrumente `duplex`/`tcd` (Womersley → Spektrum), `icp` (Plateauwellen, PVI, Infusionstest).
- Krankheiten: Stenose, Vasospasmus nach SAB, Hydrozephalus/NPH, idiopathische
  intrakranielle Hypertension, Liquorunterdruck.
- Validierung: Plateauwellen und PVI-Tests gegen Ursino-Lodi-Abbildungen,
  Lindegaard-Index-Verlauf gegen Literatur.

### Phase 4 – Basalganglien, Kleinhirn, Kortex + EEG/EP
- Ratenmodelle (Albin-DeLong, Gurney-Prescott-Redgrave), STN-GPe spiking (Terman/Rubin),
  Dopamin-Kopplung, DBS-Stimulusknoten.
- Kleinhirn: Purkinje-Zellmodell (De Schutter-Bower, über NEURON oder vereinfacht),
  Kleinhirnschleife nach Medina-Mauk, Läsion cell_loss/channelopathy.
- Kortex: Jansen-Rit/Wendling-Massen, Lead-Field, Instrument `eeg`, `evoked` (VEP, AEP, MEP).
- Krankheiten: Parkinson (mit/ohne DBS), Dystonie-Hypothesen, SCA, Epilepsie-Dynamik.

### Phase 5 – Vegetatives System + Pharmakokinetik
- Baroreflex (Ursino 1998), Heldt-Kipptischmodell, HRV-Analyse, Instrument `autonomic_tests`.
- `pk`-Physik: L-Dopa-Kinetik → Dopaminspiegel → Basalganglien; Pyridostigmin → Endplatte.
- Krankheiten: MSA, reine autonome Insuffizienz, diabetische autonome Neuropathie.

### Phase 6 – Katalog-Erweiterung
- Hirnstamm, Vestibularsystem, Sehbahn, Konnektom-Ebene (TVB-Import), FEM-Volumenleiter.

---

## 8. Validierung und Tests

- Jeder Referenz-Flow hat `validation/`: Zielkurven (digitalisiert aus Abbildungen, mit
  Quelle und Locator) und Toleranzen. pytest vergleicht Simulation gegen Ziel.
- Jede Krankheit hat `expected_findings`; Tests prüfen Richtung und Größenordnung.
- Provenance-Test: kein Parameter ohne Quelle in `modules/**/params/`.
- Schema-Test: alle YAML-Dateien gegen JSON-Schema.
- Import-Test: CellML/SBML/NeuroML-Beispiele laden und mit Originalsimulation vergleichen.

---

## 9. Abgrenzung und Risiken

- **Rechenlast**: `cellular`-Modelle mit vielen Zellen sind langsam. Deshalb Zoomstufen,
  Multi-Rate und NEURON als externer Solver. Default ist immer die gröbste ausreichende Ebene.
- **Kopplungen ohne gesicherte Modelle** (z. B. neurovaskuläre Kopplung, Ratenmodell ↔
  Läsion auf Zellebene): als `provenance: assumed` kennzeichnen und im Bericht ausweisen.
- **Lehrbuch-Lizenz**: PDFs bleiben lokal; der Bericht enthält Zitat und Locator, nie Seiten.
- **KI-Extraktion**: Fehlerquelle Nummer eins. Ohne Review kein Eintrag; Locator Pflicht.
- **Scope-Creep**: neue Systeme nur als Modul nach Abschnitt 1.2 mit Referenz-Flow,
  Parametersatz, Validierung. Kein Modul ohne diese drei Teile.

---

## 10. Offene Entscheidungen

1. ~~NEURON oder eigener Kabel-Solver?~~ **Entschieden (2026-10-19): NEURON** als optionale
   Abhängigkeit für die Ebene `cellular` (siehe Abschnitt 3.3). Ohne NEURON läuft das Plugin
   auf Populations- und Organebene weiter.
2. LLM für den Ingest lokal (Datenschutz bei Lehrbuch-PDFs) oder API?
3. Volumenleiter für EMG: nur analytisch (Phase 2) oder früh FEM (scikit-fem)?
4. Lead-Field für EEG: Kugelkopf selbst implementieren oder MNE-Python einbinden?
5. Wie tief die B-Bild-Simulation im Duplex gehen soll (schematisch reicht für Lehre).
