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

2.  LINES ARE SORTED ALPHABETICALLY by key name, then numerically by index:
    Active, Comments, Incoming0..N, Name, Name0..N, Options0..N, Outgoing0..N,
    PresetSwitchIgnore. Any other order yields a file Bome accepts but rewrites
    wholesale on the first save, making every future diff unreadable.

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
    `StMa` + EIGHT hex digits giving the NUMBER of rules, then the rules concatenated
    with no separator. Get that count wrong and the extra rules are silently ignored.

7.  THE XML PREFIX NAMES THE ACTION TYPE: `MID3` for MIDI, `Prfm` for a Perform. It
    is not decorative -- Bome uses it to pick its parser.

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
EMPTY = "None"                     # how an absent outgoing action is written


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
    return subprocess.run(["pgrep", "-x", "MIDITranslatorPro"],
                          capture_output=True).returncode == 0


def refuse_if_running() -> None:
    """Call before any write. See rule 8."""
    if bome_running():
        raise SystemExit(
            "Bome MIDI Translator is running: it holds the project in memory and "
            "will rewrite the file on its next save.\n"
            "Closing the WINDOW is not enough -- the application has to quit.")


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

    def rules(self) -> list[str]:
        """The declared rules, as written. See rule 6."""
        m = re.search(r"StMa([0-9a-f]{8})(.*)$", self.options)
        return [] if not m else [m.group(2)]      # not split: there is no separator

    def set_rules(self, rules: list[str], prefix: str = "Actv01Stop00OutO00") -> None:
        """Rewrites Options with the right COUNT -- the classic rule-6 mistake."""
        self.options = prefix + ("StMa%08x%s" % (len(rules), "".join(rules))
                                 if rules else "")


@dataclass
class Preset:
    number: int
    header: dict[str, str] = field(default_factory=dict)   # Active, Comments, Name…
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
        self.translators = {n: t for n, t in enumerate(
            self.translators[i] for i in sorted(self.translators))}

    def add(self, t: Translator) -> int:
        i = max(self.translators, default=-1) + 1
        self.translators[i] = t
        return i

    def render(self) -> str:
        """The text block, lines sorted the way Bome sorts them. See rule 2."""
        lines = [f"{k}={v}" for k, v in self.header.items()]
        for i in sorted(self.translators):
            t = self.translators[i]
            for field, val in (("Name", t.name), ("Incoming", t.incoming),
                               ("Outgoing", t.outgoing), ("Options", t.options)):
                if val or field == "Outgoing":
                    lines.append(f"{field}{i}={val}")

        def rank(l: str):
            m = re.match(r"^([A-Za-z]+)(\d*)=", l)
            return (m.group(1), int(m.group(2)) if m.group(2) else -1)
        return f"[Preset.{self.number}]\n" + "\n".join(sorted(lines, key=rank))


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
            end = self.text.find("\n[Preset.", m.end())
            body = self.text[m.end():end if end > 0 else len(self.text)]
            p = Preset(num)
            for line in body.split("\n"):
                mt = re.match(r"^(%s)(\d+)=(.*)$" % "|".join(FIELDS), line)
                if mt:
                    t = p.translators.setdefault(int(mt.group(2)), Translator())
                    setattr(t, {"Name": "name", "Incoming": "incoming",
                                "Outgoing": "outgoing", "Options": "options"}[mt.group(1)],
                            mt.group(3))
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
        end = self.text.find("\n[Preset.", m.end())
        backup = self.path.with_name(
            self.path.name + time.strftime(".bak-%Y%m%d-%H%M%S"))
        shutil.copy2(self.path, backup)
        fresh = self.text[:m.start()] + self.presets[preset].render() + \
            (self.text[end:] if end > 0 else "\n")
        self.path.write_text(fresh, encoding="utf-8", errors="surrogateescape")
        self.text = fresh
        return backup

    def is_signed(self) -> bool | None:
        """None when the file carries no signature. See rule 9 -- indicative only: only
        Bome can verify one, all we can observe is that a signature is PRESENT."""
        return None if "[Signatures]" not in self.text else True
