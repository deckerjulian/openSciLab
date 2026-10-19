# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Every component there is, in one catalog to search.

======== ======================================================== ===============================
source   what                                                     type of its components
======== ======================================================== ===============================
builtin  openSciLab's own (``circuits/library``), all simulated   ``resistor``, ``opamp``
user     YAML components of the settings and of the project       their ``type``
kicad    KiCad's symbol libraries (~220 libraries, ~23 000 parts) ``kicad:Device:R``
spice    each ``.model``/``.subckt`` of the model libraries       ``spice:2N3904``
======== ======================================================== ===============================

Searching uses an index (:class:`Entry`), so that the whole catalog is searched at once: KiCad's
libraries are read once and remembered in the settings' cache (by the size and time of each file);
a part's symbol is read when it is first used. A circuit's own model libraries are added for that
circuit (:meth:`Catalog.models_for`).
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace


from ...core import settings
from ..library import ComponentSpec, ParamSpec, SimModel, builtin, load_folder
from ..symbols import Field, Pin, Shape, Symbol
from .models import Definition, ModelLibrary, standard_library

log = logging.getLogger(__name__)

CACHE_FILE = "circuits-catalog.json"
CACHE_VERSION = 3


@dataclass
class Entry:
    """A component of the catalog as the search knows it."""

    type: str
    title: str
    category: str
    description: str = ""
    keywords: str = ""
    source: str = "builtin"
    library: str = ""
    #: it can be simulated; "exact" (its own model), "generic" (a standard model stands in), "" (not)
    simulation: str = "exact"
    units: int = 1
    role: str = ""

    def text(self) -> str:
        return f"{self.type} {self.title} {self.category} {self.description} {self.keywords} {self.library}".lower()


def entry_of(spec: ComponentSpec) -> Entry:
    simulation = (spec.model.quality if spec.model is not None else ("exact" if spec.simulated else ""))
    if simulation == "none":
        simulation = ""
    if spec.role == "mcu":
        simulation = "exact"
    units = spec.symbol.units if not callable(spec.symbol_source) else int(spec.extra.get("units", 1))
    return Entry(spec.type, spec.title, spec.category, spec.description, spec.keywords, spec.source, spec.library,
                 simulation, units, spec.role)


# ---------------------------------------------------------- SPICE models
_SPICE_SYMBOLS = {"diode": "diode", "npn": "npn", "pnp": "pnp", "nmos": "nmos", "pmos": "pmos"}


def spec_of_model(definition: Definition) -> ComponentSpec | None:
    """A component for a ``.model`` (the standard symbol of its kind) or a ``.subckt`` (a box with its pins)."""
    part = definition.part
    own = builtin()
    base = own.get(_SPICE_SYMBOLS.get(part, ""))
    title = definition.name
    library = os.path.splitext(os.path.basename(definition.file))[0] if definition.file else "models"
    description = definition.comment[:200] or f"{part} model {definition.name}"
    if base is not None:
        params = tuple(ParamSpec(p.name, p.label, definition.name if p.name == "value" else p.default, p.unit,
                                 p.description, p.choices, p.minimum, p.maximum, p.live, p.kind) for p in base.params)
        model = base.model
        if definition.device in ("NMOS", "PMOS") and model is not None:
            # (a level 1 / BSIM model has four terminals, the VDMOS of the own symbol three: bulk to source)
            model = replace(model, template=model.template.replace(" {value}", " {pin.3} {value}"))
        return ComponentSpec(f"spice:{definition.name}", title, f"SPICE models · {base.category}", description,
                             f"{part} {definition.name}", base.prefix, base.symbol, params, model, base.controls,
                             base.role, "spice", "", library, show=base.show, current=base.current)
    if definition.kind != "subckt" or not definition.pins:
        return None
    pins = definition.pins
    count = len(pins)
    left = pins[: (count + 1) // 2]
    right = pins[(count + 1) // 2:]
    height = max(len(left), len(right)) * 20 + 20
    width = max(60, 8 * max(len(p) for p in pins) * 2 + 20)
    width = int(round(width / 20.0) * 20)
    top = -height // 2
    symbol_pins = []
    for index, name in enumerate(left):
        symbol_pins.append(Pin(str(index + 1), name, -width / 2 - 20, top + 20 + 20 * index, 0, 20))
    for index, name in enumerate(right):
        symbol_pins.append(Pin(str(len(left) + index + 1), name, width / 2 + 20, top + 20 + 20 * index, 180, 20))
    symbol = Symbol((Shape("rect", ((-width / 2, top), (width / 2, -top)), fill="background"),), tuple(symbol_pins),
                    Field(0, top - 10), Field(0, -top + 10))
    template = "{ref:X} " + " ".join(f"{{pin.{i + 1}}}" for i in range(count)) + " {value}"
    params = (ParamSpec("value", "Subcircuit", definition.name, kind="model",
                        description="The subcircuit it is."),)
    return ComponentSpec(f"spice:{definition.name}", title, "SPICE models · Subcircuits", description,
                         f"subckt {definition.name} {' '.join(pins)}", "X", symbol, params, SimModel(template), (), "",
                         "spice", "", library)


# ---------------------------------------------------------------- catalog
def kicad_folders() -> list[str]:
    """The folders of KiCad symbol libraries: those of the settings, the vendor folder, KiCad's."""
    from ...core import preferences, vendor

    if not preferences.get("circuits.use_kicad"):
        return []
    folders = [path for path in str(preferences.get("circuits.kicad_symbols") or "").split(os.pathsep) if path.strip()]
    folders += vendor.find_all("kicad")
    seen: list[str] = []
    for folder in folders:
        folder = os.path.abspath(os.path.expanduser(folder))
        if os.path.isdir(folder) and folder not in seen:
            seen.append(folder)
    return seen


def model_folders() -> list[str]:
    from ...core import preferences

    folders = [path for path in str(preferences.get("circuits.model_folders") or "").split(os.pathsep) if path.strip()]
    folders.append(os.path.join(settings.settings_directory(), "circuits", "models"))
    return [os.path.abspath(os.path.expanduser(folder)) for folder in folders]


class Catalog:
    """See the module. One per application (:func:`default_catalog`); thread safe for reading."""

    def __init__(self, kicad: list[str] | None = None, models: list[str] | None = None,
                 user: list[str] | None = None, use_cache: bool = True) -> None:
        self._kicad_folders = kicad
        self._model_folders = models
        self._user_folders = user
        self.use_cache = use_cache
        self.problems: list[str] = []
        self._specs: dict[str, ComponentSpec] = {}
        self._entries: dict[str, Entry] = {}
        #: KiCad library name -> path
        self._kicad_libraries: dict[str, str] = {}
        self._parsed: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.RLock()
        self._models: ModelLibrary | None = None
        self._indexed = False
        self._loaded = False

    # ------------------------------------------------------------- sources
    def _load_own(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for spec in builtin().values():
            self._specs[spec.type] = spec
            self._entries[spec.type] = entry_of(spec)
        folders = self._user_folders if self._user_folders is not None else \
            [os.path.join(settings.settings_directory(), "circuits", "library")]
        for folder in folders:
            specs, problems = load_folder(folder, "user")
            self.problems.extend(problems)
            for spec in specs:
                if spec.type in self._specs and self._specs[spec.type].source == "builtin":
                    self.problems.append(f"{spec.type}: a component of openSciLab cannot be replaced")
                    continue
                self._specs[spec.type] = spec
                self._entries[spec.type] = entry_of(spec)

    @property
    def models(self) -> ModelLibrary:
        """The model libraries of every circuit: of the settings, KiCad's, openSciLab's standard ones."""
        with self._lock:
            if self._models is None:
                library = ModelLibrary()
                for folder in self._model_folders if self._model_folders is not None else model_folders():
                    library.add_folder(folder)
                for folder in self.kicad_folders():
                    for name in sorted(os.listdir(folder)):
                        if name.lower().endswith((".sp", ".lib")):
                            library.add_file(os.path.join(folder, name))
                for definition in standard_library().definitions():
                    if definition.name not in library:
                        library.add_text(definition.text, definition.file)
                self._models = library
                for definition in library.definitions():
                    spec = spec_of_model(definition)
                    if spec is not None and spec.type not in self._specs:
                        self._specs[spec.type] = spec
                        self._entries[spec.type] = entry_of(spec)
            return self._models

    def models_for(self, paths: Iterable[str]) -> ModelLibrary:
        """The model libraries of a circuit: its own files first, then those of every circuit."""
        library = ModelLibrary()
        for path in paths:
            if os.path.isdir(path):
                library.add_folder(path)
            else:
                library.add_file(path)
        for definition in self.models.definitions():
            if definition.name not in library:
                library.add_text(definition.text, definition.file)
        return library

    def kicad_folders(self) -> list[str]:
        return self._kicad_folders if self._kicad_folders is not None else kicad_folders()

    def kicad_libraries(self) -> dict[str, str]:
        """KiCad's libraries: name -> file (a name found twice: the first folder's)."""
        with self._lock:
            if not self._kicad_libraries:
                for folder in self.kicad_folders():
                    for name in sorted(os.listdir(folder)):
                        for suffix in (".kicad_sym", ".kicad_symdir"):
                            if name.endswith(suffix):
                                self._kicad_libraries.setdefault(name[: -len(suffix)], os.path.join(folder, name))
            return dict(self._kicad_libraries)

    # --------------------------------------------------------------- index
    def entries(self) -> list[Entry]:
        self._load_own()
        _ = self.models
        return list(self._entries.values())

    @property
    def indexed(self) -> bool:
        """KiCad's libraries are in the index (:meth:`build_index`)."""
        return self._indexed

    def build_index(self, progress: Callable[[str, float], None] | None = None,
                    cancelled: Callable[[], bool] = lambda: False, separate: bool = False) -> int:
        """Read KiCad's libraries into the index (from the cache when they did not change); returns
        how many parts it added. Takes a few seconds the first time: call it in a thread. ``separate``:
        the libraries are read in a process of its own (reading them in Python would hold up the
        application's other threads), which leaves the cache this process then reads."""
        from . import kicad

        if separate and self.use_cache and self._stale():
            self._index_in_process(progress, cancelled)

        self._load_own()
        models = self.models
        cache = self._read_cache() if self.use_cache else {}
        libraries = self.kicad_libraries()
        fresh: dict = {}
        count = 0
        for number, (name, path) in enumerate(sorted(libraries.items())):
            if cancelled():
                break
            if progress is not None:
                progress(f"Reading {name}", number / max(len(libraries), 1))
            key = _library_key(path)
            if key is None:
                continue
            cached = cache.get(path)
            if cached is not None and cached.get("key") == key:
                entries = [Entry(**item) for item in cached.get("entries", [])]
            else:
                entries = []
                try:
                    raw = kicad.read_library(path)
                except (OSError, ValueError) as error:
                    self.problems.append(f"{name}: {error}")
                    continue
                for symbol_name, symbol in raw.items():
                    try:
                        spec = kicad.to_spec(name, symbol, raw, models)
                    except Exception as error:  # noqa: BLE001 - one bad symbol must not stop the catalog
                        self.problems.append(f"{name}:{symbol_name}: {error}")
                        continue
                    spec.extra["units"] = spec.symbol.units
                    entries.append(entry_of(spec))
            fresh[path] = {"key": key, "entries": [asdict(entry) for entry in entries]}
            with self._lock:
                for entry in entries:
                    if entry.type not in self._entries:
                        self._entries[entry.type] = entry
                        count += 1
        if self.use_cache and not cancelled():
            self._write_cache(fresh)
        self._indexed = True
        if progress is not None:
            progress("The catalog is ready", 1.0)
        return count

    def _stale(self) -> bool:
        """A library is not in the cache as it is now."""
        cache = self._read_cache()
        for path in self.kicad_libraries().values():
            key = _library_key(path)
            if key is None:
                continue
            cached = cache.get(path)
            if cached is None or cached.get("key") != key:
                return True
        return False

    def _index_in_process(self, progress, cancelled) -> None:
        import multiprocessing

        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        worker = context.Process(target=_index_worker, args=(self.kicad_folders(), self._model_folders, queue),
                                 name="openscilab-catalog", daemon=True)
        worker.start()
        while worker.is_alive() or not queue.empty():
            if cancelled():
                worker.kill()
                break
            try:
                text, share = queue.get(timeout=0.2)
            except Exception:  # noqa: BLE001, S112 - queue.Empty, or the worker went: looked at again above
                continue
            if progress is not None and share < 1.0:
                progress(text, share)
        worker.join(2)

    def _cache_path(self) -> str:
        return os.path.join(settings.settings_directory(), "cache", CACHE_FILE)

    def _read_cache(self) -> dict:
        try:
            with open(self._cache_path(), encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {}
        if data.get("version") != CACHE_VERSION or data.get("models") != self._models_cache_key():
            return {}  # (whether a part is simulated depends on the model libraries and on this version)
        return data.get("libraries", {})

    def _models_cache_key(self) -> str:
        from ... import __version__

        folders = self._model_folders if self._model_folders is not None else model_folders()
        keys = []
        for folder in folders:
            for path in sorted(glob.glob(os.path.join(folder, "*"))) if os.path.isdir(folder) else [folder]:
                keys.append(f"{path}={_library_key(path)}")
        return f"{__version__}|" + ";".join(keys)

    def _write_cache(self, libraries: dict) -> None:
        path = self._cache_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as handle:
                json.dump({"version": CACHE_VERSION, "models": self._models_cache_key(), "libraries": libraries}, handle)
            os.replace(path + ".tmp", path)
        except OSError as error:
            log.warning("The index of the catalog could not be kept: %s", error)

    # -------------------------------------------------------------- lookup
    def spec(self, type_name: str) -> ComponentSpec | None:
        """The component ``type_name`` (KiCad's parts are read when first asked for)."""
        self._load_own()
        found = self._specs.get(type_name)
        if found is not None:
            return found
        if type_name.startswith("spice:"):
            _ = self.models
            return self._specs.get(type_name)
        if type_name.startswith("kicad:"):
            _prefix, library_name, _sep, name = type_name.partition(":")[0], *type_name[6:].partition(":")
            return self._kicad_spec(library_name, name)
        return None

    def _kicad_spec(self, library_name: str, name: str) -> ComponentSpec | None:
        from . import kicad

        path = self.kicad_libraries().get(library_name)
        if path is None:
            return None
        with self._lock:
            raw = self._parsed.get(path)
            if raw is None:
                try:
                    raw = kicad.read_library(path)
                except (OSError, ValueError) as error:
                    self.problems.append(f"{library_name}: {error}")
                    return None
                self._parsed[path] = raw
                while len(self._parsed) > 6:
                    self._parsed.popitem(last=False)
            else:
                self._parsed.move_to_end(path)
            symbol = raw.get(name)
            if symbol is None:
                return None
            spec = kicad.to_spec(library_name, symbol, raw, self.models)
            self._specs[spec.type] = spec
            if spec.type not in self._entries:
                self._entries[spec.type] = entry_of(spec)
            return spec

    def search(self, text: str = "", limit: int = 300, simulated: bool = False, source: str = "",
               category: str = "") -> list[Entry]:
        """The entries matching every word of ``text`` (in their name first, then anywhere)."""
        words = [word for word in re.split(r"\s+", text.lower().strip()) if word]
        found: list[tuple[int, str, Entry]] = []
        for entry in self.entries():
            if simulated and not entry.simulation:
                continue
            if source and entry.source != source:
                continue
            if category and entry.category != category:
                continue
            haystack = entry.text()
            if not all(word in haystack for word in words):
                continue
            title = entry.title.lower()
            rank = 0
            if words:
                if title == " ".join(words):
                    rank = -4
                elif title.startswith(words[0]):
                    rank = -3
                elif all(word in title for word in words):
                    rank = -2
            rank += {"builtin": 0, "user": 0, "spice": 1, "kicad": 2}.get(entry.source, 3)
            if not entry.simulation:
                rank += 1
            found.append((rank, title, entry))
        found.sort(key=lambda item: (item[0], item[1]))
        return [entry for _rank, _title, entry in found[:limit]]

    def categories(self) -> list[str]:
        seen: list[str] = []
        for entry in self.entries():
            if entry.category not in seen:
                seen.append(entry.category)
        return seen

    def __call__(self, type_name: str) -> ComponentSpec | None:
        return self.spec(type_name)


def _library_key(path: str) -> str | None:
    """What tells whether a library changed: the size and time of its file (of its folder's files)."""
    try:
        if os.path.isdir(path):
            stats = [os.stat(os.path.join(path, name)) for name in os.listdir(path) if name.endswith(".kicad_sym")]
            return f"{len(stats)}:{sum(s.st_size for s in stats)}:{int(max((s.st_mtime for s in stats), default=0))}"
        stat = os.stat(path)
        return f"{stat.st_size}:{int(stat.st_mtime)}"
    except OSError:
        return None


def _index_worker(kicad: list[str], models: list[str] | None, queue) -> None:  # pragma: no cover - a process
    """The process that reads KiCad's libraries into the cache (see :meth:`Catalog.build_index`)."""
    catalog = Catalog(kicad=kicad, models=models)
    catalog.build_index(lambda text, share: queue.put((text, share)))
    queue.put(("done", 1.0))


_default: Catalog | None = None


def default_catalog() -> Catalog:
    """The catalog of the application (built when first asked for)."""
    global _default
    if _default is None:
        _default = Catalog()
    return _default


def reset_default() -> None:
    """Forget the catalog (the settings changed; tests)."""
    global _default
    _default = None
