"""Read and rewrite a Bome MIDI Translator Pro project file without breaking it.

THE FORMAT IS DOCUMENTED NOWHERE. This module is what we learned by editing a real
88-translator project, and every rule below was verified against Bome 1.9.2 -- by
opening the file it produced and watching it load.

    ; ------- Bome MIDI Translator: signed project file
    [Preset.6]
    Active=1
    Comments=…\\n…                 newlines are escaped
    Incoming0=MID3<Incoming …>     one translator per INDEX, spread over 4 keys
    Incoming1=Prfm<Incoming …>
    Name=Zones etat                the PRESET name, no index
    Name0=CC112 -> ya P1 bas       translator 0's name
    Options0=Actv01Stop00OutO00StMa00000001ya=uu
    Outgoing0=None                 "None" = this translator emits nothing
    PresetSwitchIgnore=0
    [Signatures]
    Signature-1=…
    SignedBy-1=…

SEVEN RULES, each paid for at least once:

1.  A TRANSLATOR IS SPREAD OVER FOUR INDEXED KEYS -- Name, Incoming, Outgoing,
    Options -- never grouped. Touching one means recollecting all four.

2.  LINES ARE SORTED LEXICOGRAPHICALLY BY KEY -- as TEXT, not by index number:
    Active, Comments, Incoming0, Incoming1, Incoming10, … Incoming19, Incoming2,
    Incoming20, Incoming3 … then Name, Name0, Name1, Name10 … same pattern, then
    Options*, Outgoing*, PresetSwitchIgnore. `Name` comes before `Name0` because a
    prefix sorts before any longer string.
    This module sorted by (prefix, int(index)) until 2026-09-27 -- which reads better
    to a human and is NOT what Bome does. Any other order yields a file Bome accepts
    but rewrites wholesale on the first save, making every future diff unreadable.

3.  NUMBERING MUST BE CONTIGUOUS. Deleting translator 11 of 39 means renumbering the
    27 that follow, across all four keys at once.

4.  `Outgoing<N>=None` -- the literal string -- is a translator that emits nothing.
    It only sets variables through its rules. That is a free slot, and it is what
    makes some refactors cost nothing at all.

5.  THE CHANNEL IS 0-BASED: `<Channel num="11"/>` is channel 12. An adjacent
    `Type="Any"` means "any channel" and makes the number moot. This trap produced a
    false regression diagnosis on 2026-09-27.

6.  `Options` IS A FIXED-LENGTH RECORD followed by rules:
    `Actv01` active · `Stop00` does not stop processing · `OutO00` outgoing options ·
    `StMa` + EIGHT hex digits, UPPER case, giving the NUMBER of rules, then the rules concatenated
    with no separator. Get that count wrong and the extra rules are silently ignored.

7.  THE XML PREFIX NAMES THE ACTION TYPE: `MID3` for MIDI, `Prfm` for a Perform. It
    is not decorative -- Bome uses it to pick its parser. `MID2` is an older MIDI
    form, still written for raw-pattern actions: no XML, a MIDA port list right after
    the prefix (`MID2MIDA00010007Abletonppqqrr`), then the pattern.

AND THE MIDI ROUTER, which lives in the PROJECT, not in the settings (.bmts) file:

    [MIDI.routes]                  before the first preset, absent when there is none
    In0=Keyboard In                route 0: from this port...
    Out0=Ableton                   ...to this one, untouched (thru, no translator)
    In1=…

One In/Out pair per index, one destination per pair: a port sent to two places is two
routes. Observed on a single real project (5 routes, 2026-10-11), so `Project.routes`
refuses any other shape rather than guess at it.

AND TWO CONSTRAINTS THAT ARE NOT IN THE FILE:

8.  BOME REWRITES THE FILE FROM MEMORY on save. Editing while it runs means losing
    your work at the next Cmd+S. Hence `refuse_if_running`. Closing the project
    WINDOW is not enough -- the application has to quit.

9.  EDITING INVALIDATES THE SIGNATURE. The file still opens in MIDI Translator Pro,
    but BomeBox and MT Player refuse it. Only Bome can sign: the remedy is to open
    the project and save. Observed on a real rig -- an invalid signature lived five
    days with no visible consequence on a machine that only runs MT Pro.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

FIELDS = ("Name", "Incoming", "Outgoing", "Options")
EMPTY = "None"  # how an absent outgoing action is written


def section_end(text: str, start: int) -> int:
    """Index of the newline that opens the NEXT section -- any section -- or -1.

    Not just the next `[Preset.`: a saved project ends with `[Project]` and
    `[Signatures]` after its last preset. Searching for `[Preset.` only made the last
    preset run to the end of the file, so their keys were read as preset keys, and
    writing that preset -- even unchanged -- folded them into it and dropped both
    headers. Found on 2026-10-11 by the first round-trip test of the last preset.
    """
    m = re.compile(r"\n\[").search(text, start)
    return m.start() if m else -1


def bome_running() -> bool:
    """True when MIDI Translator Pro is running.

    `pgrep -x` on the exact process NAME, not `pgrep -f` on the command line: the
    latter also matches the invoking shell, whose own command line contains the
    pattern. That ambiguity cost a wrong diagnosis -- a genuinely running Bome was
    taken for a false positive, twice.

    It does not distinguish "project closed" from "application quit", deliberately:
    as long as the process lives it holds the project in memory and will rewrite the
    file. Closing the window is not enough, it has to quit.
    """
    return subprocess.run(["pgrep", "-x", "MIDITranslatorPro"], capture_output=True).returncode == 0


def refuse_if_running() -> None:
    """Call before any write. See rule 8."""
    if bome_running():
        raise SystemExit(
            "Bome MIDI Translator is running: it holds the project in memory and "
            "will rewrite the file on its next save.\n"
            "Closing the WINDOW is not enough -- the application has to quit."
        )


@dataclass
class Translator:
    """The four keys of one index, recollected."""

    name: str = ""
    incoming: str = ""
    outgoing: str = EMPTY
    options: str = ""

    @property
    def emits(self) -> bool:
        return self.outgoing.strip() not in ("", EMPTY)

    @property
    def channel(self) -> int | None:
        """1-16, or None for "any channel". See rule 5."""
        m = re.search(r'<Channel num="(\d+)"(\s+Type="Any")?', self.incoming)
        if not m or m.group(2):
            return None
        return int(m.group(1)) + 1

    @property
    def number(self) -> int | None:
        m = re.search(r'<Value1 num="(0x[0-9A-Fa-f]+|\d+)"', self.incoming)
        if not m:
            return None
        v = m.group(1)
        return int(v, 16) if v.startswith("0x") else int(v)

    @property
    def in_ports(self) -> list[str]:
        """Ports named on the incoming action itself -- empty means "the defaults".

        Two encodings: `MID3` names them in <Port> elements, the older `MID2` (still
        written by 1.9.2 for raw-pattern actions) as a MIDA list right after the prefix.
        """
        if self.incoming.startswith("MID2MIDA"):
            return read_ports(self.incoming, 4)[0]
        return re.findall(r"<Port>([^<]*)</Port>", self.incoming)

    def rules(self) -> list[str]:
        """The declared rules, as written. See rule 6.

        The count is hexadecimal and Bome writes it in UPPER case: `StMa0000000C` is
        twelve rules. Matching only [0-9a-f] silently misses every translator with ten
        or more -- which is exactly the dense ones worth auditing.
        """
        m = re.search(r"StMa([0-9A-Fa-f]{8})(.*)$", self.options)
        return [] if not m else [m.group(2)]  # not split: there is no separator

    def set_rules(self, rules: list[str], prefix: str = "Actv01Stop00OutO00") -> None:
        """Rewrites Options with the right COUNT -- the classic rule-6 mistake."""
        # %08X and not %08x: Bome writes the count in UPPER case, and rule 6 above says
        # so -- yet this writer emitted lower case. Invisible below ten rules, where the
        # count has no letters at all; it bites exactly on the dense translators worth
        # editing. Found on 2026-09-27 writing a twelve-rule translator (0000000C).
        self.options = prefix + ("StMa%08X%s" % (len(rules), "".join(rules)) if rules else "")


@dataclass
class Preset:
    number: int
    header: dict[str, str] = field(default_factory=dict)  # Active, Comments, Name…
    translators: dict[int, Translator] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.header.get("Active", "1") == "1"

    @property
    def name(self) -> str:
        return self.header.get("Name", "")

    def remove(self, index: list[int]) -> None:
        """Removes translators AND renumbers. See rule 3."""
        for i in index:
            self.translators.pop(i, None)
        self.translators = {
            n: t for n, t in enumerate(self.translators[i] for i in sorted(self.translators))
        }

    def add(self, t: Translator) -> int:
        i = max(self.translators, default=-1) + 1
        self.translators[i] = t
        return i

    def render(self) -> str:
        """The text block, lines sorted the way Bome sorts them. See rule 2."""
        lines = [f"{k}={v}" for k, v in self.header.items()]
        for i in sorted(self.translators):
            t = self.translators[i]
            for key, val in (
                ("Name", t.name),
                ("Incoming", t.incoming),
                ("Outgoing", t.outgoing),
                ("Options", t.options),
            ):
                if val or key == "Outgoing":
                    lines.append(f"{key}{i}={val}")

        def rank(line: str):
            """The KEY, compared as text. Bome sorts lexicographically, not numerically.

            Measured on a real 21-translator preset on 2026-09-27:

                Name0 Name1 Name10 Name11 … Name19 Name2 Name20 Name3 … Name9

            An earlier version of this module sorted by (prefix, int(index)) -- which
            reads better to a human and is NOT what Bome does. The file still loads, so
            the mistake is invisible until you diff: renumbering moves twenty untouched
            lines and buries the three you actually added. Comparing the key as TEXT
            reproduces Bome's order exactly, `Name` before `Name0` included, because a
            prefix sorts before any longer string.
            """
            return line.split("=", 1)[0]

        return f"[Preset.{self.number}]\n" + "\n".join(sorted(lines, key=rank))


def decode_ports(value: str) -> list[str]:
    """Port names out of a `DefaultInPorts` / `DefaultOutPorts` value.

        MIDA 0002 0007 Ableton 0008 DrumPads       (spaces added for reading)

    `MIDA`, then the COUNT in four hex digits, then for each port its LENGTH in four
    hex digits followed by the name. `MIDA0000` is "no default". Read off real files
    on 2026-10-11, where the lengths check out against the names they precede.
    """
    names, end = read_ports(value)
    if end != len(value):
        raise ValueError(f"port list does not end where its count says: {value!r}")
    return names


def read_ports(text: str, start: int = 0) -> tuple[list[str], int]:
    """A MIDA port list beginning at `start` -> (names, index just past it).

    The same encoding also opens a `MID2` action -- `MID2MIDA00010007Ableton…` -- where
    the list is followed by the message pattern, so the caller needs to know where the
    list stops.
    """
    if text[start : start + 4] != "MIDA":
        raise ValueError(f"not a port list: {text[start : start + 20]!r}")
    count, pos, names = int(text[start + 4 : start + 8], 16), start + 8, []
    for _ in range(count):
        size = int(text[pos : pos + 4], 16)
        names.append(text[pos + 4 : pos + 4 + size])
        pos += 4 + size
    return names, pos


@dataclass(frozen=True)
class Route:
    """One connection of the MIDI Router: every message from `source` goes to
    `destination` as is -- thru without spending a translator, and without remapping."""

    index: int
    source: str
    destination: str


class Project:
    """A .bmtp file, read into objects and written back byte-identical when unchanged."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.text = self.path.read_text(encoding="utf-8", errors="surrogateescape")
        self.presets: dict[int, Preset] = {}
        self._split()

    def _split(self) -> None:
        for m in re.finditer(r"^\[Preset\.(\d+)\]$", self.text, re.M):
            num = int(m.group(1))
            end = section_end(self.text, m.end())
            body = self.text[m.end() : end if end > 0 else len(self.text)]
            p = Preset(num)
            for line in body.split("\n"):
                mt = re.match(r"^(%s)(\d+)=(.*)$" % "|".join(FIELDS), line)
                if mt:
                    t = p.translators.setdefault(int(mt.group(2)), Translator())
                    setattr(
                        t,
                        {
                            "Name": "name",
                            "Incoming": "incoming",
                            "Outgoing": "outgoing",
                            "Options": "options",
                        }[mt.group(1)],
                        mt.group(3),
                    )
                elif "=" in line and not line.startswith("["):
                    k, _, v = line.partition("=")
                    p.header[k] = v
            self.presets[num] = p

    def write(self, preset: int) -> Path:
        """Rewrites ONE preset in place after a timestamped backup. Returns the backup."""
        refuse_if_running()
        m = re.search(r"^\[Preset\.%d\]$" % preset, self.text, re.M)
        if not m:
            raise LookupError(f"[Preset.{preset}] not found")
        end = section_end(self.text, m.end())
        # The backup name carries the PRESET, and an existing file is never overwritten.
        # A name with second resolution alone collides as soon as two presets are written
        # in the same second -- and each copy2 then overwrote the previous backup, so what
        # survived was a HALF-EDITED file wearing the name of the pre-edit state. That
        # defeats the one thing a backup is for. Observed on 2026-09-27 writing three
        # presets in one run.
        base = self.path.name + time.strftime(".bak-%Y%m%d-%H%M%S") + f"-p{preset}"
        backup, n = self.path.with_name(base), 1
        while backup.exists():
            backup, n = self.path.with_name(f"{base}.{n}"), n + 1
        shutil.copy2(self.path, backup)
        # Bome separates presets with a BLANK line, and `end` points at the newline that
        # begins the next header -- so a naive join eats that blank line and leaves three
        # cosmetic differences in a file that is otherwise byte-identical. Put back
        # exactly the separator that was there, no more: the point of this writer is that
        # a diff shows the translators you added and nothing else.
        separateur = "\n" if end > 0 and self.text[end - 1] == "\n" else ""
        fresh = (
            self.text[: m.start()]
            + self.presets[preset].render()
            + separateur
            + (self.text[end:] if end > 0 else "\n")
        )
        self.path.write_text(fresh, encoding="utf-8", errors="surrogateescape")
        self.text = fresh
        return backup

    @property
    def routes(self) -> list[Route]:
        """The MIDI Router's connections, in index order. Read-only.

        Strict on purpose: the section has been seen in one project only, so a key that
        is not In<N>/Out<N>, or an index missing its other half, raises instead of being
        skipped -- a reader that silently drops a route would report a router that is
        not the one Bome runs.
        """
        m = re.search(r"^\[MIDI\.routes\]$", self.text, re.M)
        if not m:
            return []
        end = section_end(self.text, m.end())
        body = self.text[m.end() : end if end > 0 else len(self.text)]
        ends: dict[int, dict[str, str]] = {}
        for line in body.split("\n"):
            if not line.strip() or line.startswith(";"):
                continue
            key = re.match(r"^(In|Out)(\d+)=(.*)$", line)
            if not key:
                raise ValueError(f"[MIDI.routes]: unknown line {line!r}")
            ends.setdefault(int(key.group(2)), {})[key.group(1)] = key.group(3)
        routes = []
        for i in sorted(ends):
            if set(ends[i]) != {"In", "Out"}:
                raise ValueError(f"[MIDI.routes]: route {i} has only {sorted(ends[i])}")
            routes.append(Route(i, ends[i]["In"], ends[i]["Out"]))
        return routes

    def is_signed(self) -> bool | None:
        """None when the file carries no signature. See rule 9 -- indicative only: only
        Bome can verify one, all we can observe is that a signature is PRESENT."""
        return None if "[Signatures]" not in self.text else True
