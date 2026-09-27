#!/usr/bin/env python3
"""Test plan for a zoned keyboard rig: what can be checked without touching the keys.

WHAT THIS COVERS, and why it is bounded. A translator can only be tested by sending its
incoming action and watching what comes out — which means being able to WRITE to the port
it listens on. Five presets of this rig listen to hardware, and no script can inject into
those:

    preset                          listens to                testable
    Touche CC & Bend  (inactive)    TOUCHE Main Port          no
    Breath Control                  Breath Controller 5.2     no
    Roli Keyb Notes   (inactive)    Seaboard RISE 49          no
    Degraded mode                   P-Series + Rig Bus        partly
    Clavier                         P-Series                  no
    Zones notes                     P-Series                  no   <- the heart of the rig
    Zones etat                      Rig Bus                   YES
    Zones controleurs               Rig Bus                   YES
    Ableton to Stage Traxx          Rig Bus                   YES
    Clock beat                      any                       YES

`Zones notes` is the most important and the least reachable: it is what sends a note to
the right zone according to its pitch. It needs a hand on the keyboard — the manual
section printed at the end of a run.

THE TRAP NOT TO REPEAT. The channel is 0-BASED in the configuration file:
`<Channel num="11"/>` is channel 12. A plan that retypes numbers drifts away from the
configuration; this one READS them, through find_trigger.

BOME MUST BE RUNNING for any of this to mean anything — it is Bome that translates. An
entire run of "nothing" with Bome closed would be indistinguishable from a broken config,
which is why the run refuses to start without it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mido                                                        # noqa: E402
from bome_project.probe import Case, find_any, find_trigger, run, triggers  # noqa: E402
from bome_project.project import bome_running                      # noqa: E402

# The project this plan describes. Point BMTP at your own to adapt it -- everything
# below reads channels and numbers FROM that file, so only the preset indices and the
# PANIC contract are specific to one rig.
CONFIG = Path(os.environ.get("BMTP", Path.home() / "MyProject.bmtp")).expanduser()
PORT = "Rig Bus"
ZONES_STATE, ZONES_CTRL = 6, 8

# Which zones each trigger must silence. This is the CONTRACT, written out in full,
# because a test that derives its expectation from the configuration tests nothing.
PANIC = {112: {1}, 113: {1},           # split points belonging to one zone
         116: {2}, 122: {2},
         120: {3}, 121: {3},
         114: {1, 2},                  # SHARED split points: they separate two zones,
         115: {2, 3},                  #   so moving one orphans held notes on both sides
         102: {1, 2, 3}}               # global tone: transposes everything, cuts everything

ALL_NOTES_OFF = lambda m: m.type == "control_change" and m.control == 123   # noqa: E731


def panic_cases(trigs: list) -> list[Case]:
    """A · panic: a split point moves, the zones concerned go quiet."""
    out = []
    for cc, zones in PANIC.items():
        t = find_trigger(trigs, ZONES_STATE, cc)
        out.append(Case(
            title=f"CC{cc} ch{t.channel} -> panic {sorted(zones)}",
            send=mido.Message("control_change", channel=t.channel - 1, control=cc, value=60),
            expected={(z, 123) for z in zones},
            keep=ALL_NOTES_OFF))
    return out


def controller_cases(trigs: list) -> list[Case]:
    """B · controllers: one CC from the master keyboard reaches all three zones.

    This fan-out is NOT factored through a Perform, and that is correct: here the three
    destinations always receive, unconditionally. A Perform would save nothing — it only
    collapses the crossings of a product, and there is no product.
    """
    # This preset captures the controller number into a variable -- it forwards ANY
    # CC -- so there is no number to look up, only a kind and a channel.
    t = find_any(trigs, ZONES_CTRL, "ControlChange")
    return [Case(
        title=f"CC1 ch{t.channel} modulation -> 3 zones",
        send=mido.Message("control_change", channel=t.channel - 1, control=1, value=64),
        expected={(1, 1), (2, 1), (3, 1)},
        keep=lambda m: m.type == "control_change" and m.control == 1)]


def guard_cases(trigs: list) -> list[Case]:
    """C · guards: what must produce NOTHING.

    Without these, a plan that misses its target returns "nothing" everywhere and reads
    like a pass. That is exactly what happened on 2026-09-27 with a wrong channel, and
    it is the reason `expect_silence` has to be declared rather than inferred.
    """
    t = find_trigger(trigs, ZONES_STATE, 112)
    return [
        Case(title=f"CC112 on the WRONG channel {t.channel - 1} -> silence",
             send=mido.Message("control_change", channel=t.channel - 2, control=112, value=60),
             expect_silence=True, keep=ALL_NOTES_OFF),
        Case(title="CC 99, unmapped -> silence",
             send=mido.Message("control_change", channel=t.channel - 1, control=99, value=60),
             expect_silence=True, keep=ALL_NOTES_OFF),
        Case(title="NoteOn E3 -> silence, the test preset is gone",
             send=mido.Message("note_on", channel=0, note=0x40, velocity=100),
             expect_silence=True, keep=ALL_NOTES_OFF),
    ]


MANUAL = """
NOT COVERED, because a script cannot write to a hardware port — walk these by hand:
  1. Zones notes · play a note in each keyboard zone, check it leaves on channel 1,
     then 2, then 3 according to pitch.
  2. Breath Control · breath, bite, nod and tilt on the TEControl.
  3. Clavier · the CCs of the P-Series.
  4. Degraded mode · unplug Ableton and check the keyboard still sounds.
  5. Repli brut · tick its Active box, play, check the piano sounds UNTRANSPOSED,
     then untick it — left on, the piano doubles Ableton.
"""


def main() -> int:
    if not bome_running():
        print("Bome MIDI Translator is not running: nothing would translate, and a run\n"
              "of pure silence cannot be told apart from a broken configuration.",
              file=sys.stderr)
        return 2
    trigs = triggers(CONFIG)
    print(f"{CONFIG.name} · {len(trigs)} MIDI incoming actions in active presets\n")
    total = 0
    for title, build in (("A · PANIC", panic_cases),
                         ("B · CONTROLLERS", controller_cases),
                         ("C · GUARDS — expected silence", guard_cases)):
        print(f"╔═ {title} " + "═" * max(0, 60 - len(title)))
        try:
            cases = build(trigs)
        except LookupError as e:
            # A section that cannot even be BUILT must not take the others down with
            # it: the first run of this plan died before case one because a later
            # section raised while the tuple was being evaluated.
            print(f"  section skipped — {e}")
            total += 1
        else:
            total += run(PORT, cases)
        print()
    print("=" * 82)
    print("VERDICT:", "all conforming" if not total else f"{total} mismatch(es) to inspect")
    print(MANUAL)
    return total


if __name__ == "__main__":
    raise SystemExit(main())
