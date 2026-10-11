"""Exercise a Bome configuration against real MIDI.

WHY THIS EXISTS. A Bome configuration is a mute state machine: nothing says whether a
translator fired, and one that does not match never complains -- it simply does nothing,
silently, for the whole show. The only verdict is to send a message and watch what comes
out.

THE LESSON THAT PROMPTED IT, paid on 2026-09-27: a hand-written test returned 0 of 6 and
looked like a regression. The configuration was fine; the test aimed at channel 11 while
the triggers listen on 12. Two guards follow, and they are the ones that matter:

  - `expect_silence` forces you to DECLARE that you expect nothing, so a test that is
    mute because it missed its target can no longer read as a pass;
  - `find_trigger` reads the channel and number FROM the configuration file instead of
    retyping them. A test plan never repeats what the config already states -- that is
    exactly how it drifts out of sync.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .project import is_variable, mid2, section_end

try:
    import mido
except ImportError:  # pragma: no cover
    mido = None


# ── reading triggers out of the configuration ───────────────────────────────────


@dataclass
class Trigger:
    """What a translator LISTENS for, as the file declares it."""

    preset: int
    index: int
    name: str
    port: str
    kind: str  # ControlChange, NoteOn, ProgramChange…
    channel: int | None  # 1-16, or None for "any channel"
    number: int | None  # CC or note number, None when the value is a variable

    @property
    def mido_channel(self) -> int:
        """The channel as mido counts it, 0-15, to aim a message at this trigger.

        Raises when the trigger listens on ANY channel: there is no single channel to
        aim at then, and the `t.channel - 1` this replaces failed as a TypeError on
        None, far from the cause. Found by mypy on 2026-10-11.
        """
        if self.channel is None:
            raise ValueError(
                f"preset {self.preset} translator {self.index} ({self.name!r}) listens on "
                "any channel: pick the channel to send on explicitly"
            )
        return self.channel - 1


def triggers(path: Path) -> list[Trigger]:
    """Every MIDI incoming action of a project, channel and number included.

    The channel is 0-BASED in the file -- `num="11"` is channel 12. That is the trap
    which cost a false diagnosis, and the reason a test plan should read here rather
    than retype numbers by hand.

    Inactive presets are skipped: their translators do not fire, so asserting on them
    would be asserting on nothing.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    out: list[Trigger] = []
    for m in re.finditer(r"^\[Preset\.(\d+)\]$", text, re.M):
        start = m.end()
        end = section_end(text, start)
        body = text[start : end if end > 0 else len(text)]
        if (a := re.search(r"^Active=(\d)", body, re.M)) and a.group(1) != "1":
            continue
        names = dict(re.findall(r"^Name(\d+)=(.*)$", body, re.M))
        for mi in re.finditer(r"^Incoming(\d+)=(MID[23].*)$", body, re.M):
            i, action = mi.group(1), mi.group(2)
            raw = mid2(action)
            if raw:
                out.append(raw_trigger(int(m.group(1)), int(i), names.get(i, ""), *raw))
                continue
            xml = action[4:]
            port = p.group(1) if (p := re.search(r"<Port>([^<]+)</Port>", xml)) else ""
            kind = k.group(1) if (k := re.search(r'Simple Type="(\w+)"', xml)) else ""
            ch = re.search(r'<Channel num="(\d+)"(\s+Type="Any")?', xml)
            num = re.search(r'<Value1 num="(0x[0-9A-Fa-f]+|\d+)"', xml)
            out.append(
                Trigger(
                    preset=int(m.group(1)),
                    index=int(i),
                    name=names.get(i, ""),
                    port=port,
                    kind=kind,
                    channel=None
                    if (ch and ch.group(2))
                    else (int(ch.group(1)) + 1 if ch else None),
                    number=(
                        int(num.group(1), 16)
                        if num and num.group(1).startswith("0x")
                        else int(num.group(1))
                        if num
                        else None
                    ),
                )
            )
    return out


# The status byte's high nibble, named as MID3 names its Simple Type.
STATUS = {
    "8": "NoteOff",
    "9": "NoteOn",
    "A": "PolyPressure",
    "B": "ControlChange",
    "C": "ProgramChange",
    "D": "ChannelPressure",
    "E": "PitchBend",
}


def raw_trigger(preset: int, index: int, name: str, ports: list[str], data: list[str]):
    """A Trigger out of a MID2 pattern. What it listens for is only known when the status
    byte is a literal: `B3 07 pp` is a ControlChange, channel 4, number 7. A variable
    status (`pp qq rr`, "any message") gives kind "Raw" and no channel or number."""
    status = data[0] if data else "pp"
    if is_variable(status):
        kind, channel, number = "Raw", None, None
    elif status == "F0":
        kind, channel, number = "SysEx", None, None
    else:
        kind = STATUS.get(status[0], "Raw")
        channel = int(status[1], 16) + 1
        second = data[1] if len(data) > 1 else "pp"
        number = None if is_variable(second) else int(second, 16)
    return Trigger(preset, index, name, ports[0] if ports else "", kind, channel, number)


def find_trigger(all_triggers: list[Trigger], preset: int, number: int) -> Trigger:
    """The trigger of a preset for a given number -- channel read from the config."""
    for t in all_triggers:
        if t.preset == preset and t.number == number:
            return t
    raise LookupError(f"no trigger with number {number} in preset {preset}")


def find_any(all_triggers: list[Trigger], preset: int, kind: str) -> Trigger:
    """The first trigger of a preset matching a message KIND, whatever its number.

    Some translators capture the controller number into a variable rather than
    fixing it -- `<Value1 var="rr"/>` means "any CC". There is no number to look up
    then, and asking for one raises on a configuration that is perfectly correct.
    """
    for t in all_triggers:
        if t.preset == preset and t.kind == kind:
            return t
    raise LookupError(f"no {kind} trigger in preset {preset}")


# ── send, listen, compare ───────────────────────────────────────────────────────


@dataclass
class Case:
    """One test case: what goes in, what is expected back.

    `expected` is a set of (channel, number) observed on the way out.
    `expect_silence` must be set to assert that nothing comes back: without it, a case
    that observes nothing counts as a FAILURE, because that is almost always a missed
    target rather than the silence you meant.
    """

    title: str
    send: Any  # a mido.Message -- Any because mido is optional and untyped
    expected: set = field(default_factory=set)
    expect_silence: bool = False
    keep: Callable[[Any], bool] | None = None  # which outgoing messages to retain
    wait_s: float = 0.5


class Probe:
    """Opens one port for reading and writing, and plays cases through it."""

    def __init__(self, port: str):
        if mido is None:
            raise SystemExit("mido is missing: pip install mido python-rtmidi")
        self.port = port
        self._seen: list = []
        self._stop = threading.Event()

    def __enter__(self):
        self._out = mido.open_output(self.port)
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        time.sleep(0.4)  # let the port settle before the first send
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._out.close()

    def _listen(self):
        with mido.open_input(self.port) as inp:
            while not self._stop.is_set():
                for m in inp.iter_pending():
                    self._seen.append(m)
                time.sleep(0.01)

    def play(self, case: Case) -> tuple[bool, set]:
        self._seen.clear()
        self._out.send(case.send)
        time.sleep(case.wait_s)
        keep = case.keep or (lambda m: m.type == "control_change")
        seen = {
            (m.channel + 1, getattr(m, "control", getattr(m, "note", None)))
            for m in list(self._seen)
            if keep(m)
        }
        # The message we just sent comes back to us: a virtual port loops.
        seen.discard(
            (case.send.channel + 1, getattr(case.send, "control", getattr(case.send, "note", None)))
        )
        if case.expect_silence:
            return not seen, seen
        # An EMPTY `expected` without `expect_silence` is a case that declared nothing.
        # Comparing set() == set() made it pass on silence -- exactly the missed-target
        # case this module exists to catch. Until 2026-10-11 the guard promised by the
        # docstring of `Case` was not in the code; a test found it.
        return bool(case.expected) and seen == case.expected, seen


def run(port: str, cases: list[Case]) -> int:
    """Play every case, print a table, return the number of failures."""
    print("%-38s %-16s %-16s %s" % ("case", "expected", "observed", ""))
    print("-" * 82)
    failures = 0
    with Probe(port) as probe:
        for case in cases:
            ok, seen = probe.play(case)
            failures += not ok
            want = "silence" if case.expect_silence else str(sorted(case.expected))
            print(
                "%-38s %-16s %-16s %s"
                % (
                    case.title[:38],
                    want[:16],
                    (str(sorted(seen)) if seen else "nothing")[:16],
                    "OK" if ok else "!! MISMATCH",
                )
            )
    print("-" * 82)
    print("%d case(s) · %d mismatch(es)" % (len(cases), failures))
    return failures
