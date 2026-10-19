"""The node types follow one scheme (docs/design.md, *Nodes*): their groups, names, titles, ports and
parameters say what they are in the same way everywhere."""

from __future__ import annotations

import inspect
import re

import pytest

from openscilab.core import signals
from openscilab.lab.nodes.registry import GROUP_ICONS, GROUPS, OWN_GROUPS, Registry

#: the words a parameter is never called (the word it is called instead)
PARAM_WORDS = {"freq": "frequency", "op": "operator", "volts": "value"}
#: ports a check sends: ``result`` (Bool), ``passed`` and ``failed`` (events) – nothing else
CHECK_PORTS = {"pass", "ok", "violation"}


@pytest.fixture(scope="module")
def specs():
    registry = Registry()
    return [spec for spec in registry.specs(lazy=False)]


def test_every_group_is_one_word_with_a_title_and_an_icon():
    for key, title in GROUPS.items():
        assert re.fullmatch(r"[a-z]+", key), key
        assert title and title[0].isupper(), key
        assert key in GROUP_ICONS, key
    assert "panel" not in GROUPS  # (no empty group in the palette)


def test_every_node_type_is_named_after_its_group_and_what_it_is(specs):
    titles: dict[tuple[str, str], str] = {}
    for spec in specs:
        assert spec.group in OWN_GROUPS, spec.type
        assert re.fullmatch(r"[a-z]+\.[a-z0-9_]+", spec.type), spec.type
        assert 1 <= len(spec.title.split()) <= 3, spec.title
        assert not spec.title.lower().startswith(spec.group_title.lower().rstrip("s") + " "), \
            f"{spec.type}: the title repeats its group ({spec.title})"
        # one title once in a group (two nodes called "Sync signal" were two different things)
        assert (spec.group, spec.title) not in titles, f"{spec.type} and {titles[(spec.group, spec.title)]}"
        titles[(spec.group, spec.title)] = spec.type
        assert spec.full_title == f"{spec.title} ({spec.group_title})"


def test_every_port_and_parameter_is_described(specs):
    missing = []
    for spec in specs:
        for port in spec.inputs + spec.outputs:
            if not port.description:
                missing.append(f"{spec.type}: port {port.name}")
        for param in spec.params:
            if not param.description:
                missing.append(f"{spec.type}: parameter {param.name}")
            if not param.label:
                missing.append(f"{spec.type}: label of {param.name}")
    assert not missing, "\n".join(missing)


def test_names_of_ports_and_parameters_follow_the_scheme(specs):
    for spec in specs:
        for param in spec.params:
            assert param.name not in PARAM_WORDS, f"{spec.type}: {param.name} → {PARAM_WORDS[param.name]}"
        names = [port.name for port in spec.inputs + spec.outputs]
        assert not CHECK_PORTS & set(names), f"{spec.type}: {names}"
        result = next((port for port in spec.outputs if port.name == "result" and spec.group in ("report",)), None)
        if result is not None:
            assert result.type == signals.BOOL, spec.type
        # an output and an input of one node never share a name with different meanings
        outputs = {port.name: port.type for port in spec.outputs}
        for port in spec.inputs:
            if port.name in outputs and port.type != outputs[port.name]:
                pytest.fail(f"{spec.type}: '{port.name}' in ({port.type}) and out ({outputs[port.name]})")


def test_inputs_that_only_start_something_take_any_value(specs):
    """*trigger*, *arm*, *start*, *stop*, *reset*, *next* act on whatever arrives: they are Event
    inputs, and an Event input takes every type."""
    for spec in specs:
        for port in spec.inputs:
            if port.name in ("trigger", "arm", "start", "stop", "reset", "next"):
                assert port.type == signals.EVENT, f"{spec.type}.{port.name}"
    assert signals.compatible(signals.SCALAR, signals.EVENT) and signals.compatible(signals.CAPTURE, signals.EVENT)
    assert not signals.compatible(signals.EVENT, signals.SCALAR)
    assert not signals.compatible(signals.DEVICE, signals.EVENT)


def test_every_node_shows_its_settings_on_the_canvas(specs):
    for spec in specs:
        names = {param.name for param in spec.params}
        assert set(spec.summary) <= names, f"{spec.type}: summary {spec.summary} not in {sorted(names)}"
        if spec.params and spec.group != "structure":
            assert spec.summary_params(), spec.type


def test_the_conversions_name_nodes_and_their_parameters():
    registry = Registry()
    for conversion in signals.CONVERSIONS:
        spec = registry.get(conversion.node)
        names = {param.name for param in spec.params}
        defaults = [name for name, parameter in inspect.signature(conversion.function).parameters.items()
                    if parameter.default is not inspect.Parameter.empty]
        # (a conversion that inserts its node with parameters of its own says how it is set up)
        if not conversion.params:
            assert set(defaults) <= names, f"{conversion.node}: {defaults} not in {sorted(names)}"
        assert set(conversion.params) <= names, f"{conversion.node}: {conversion.params}"


def test_the_node_reference_of_the_documentation_is_up_to_date():
    """docs/lab.md lists every node as the registry describes it (tools/gen_node_docs.py writes it)."""
    import os
    import sys

    tools = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
    sys.path.insert(0, tools)
    try:
        import gen_node_docs
    finally:
        sys.path.remove(tools)
    assert gen_node_docs.main(["--check"]) == 0, "python tools/gen_node_docs.py"
