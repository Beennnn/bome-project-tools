#!/usr/bin/env python3
"""A test plan for a Bome project: send MIDI, check what comes out -- and what does not.

Copy this file next to your own project and edit the four settings below. The example
assumes a project where:

  - preset 0 listens on a VIRTUAL port ("Virtual Bus") -- the only kind of port a
    script can write to; a translator that listens to a hardware keyboard can only be
    tested with a hand on the keys;
  - in that preset, CC 20 is translated to CC 7 on channel 1.

Run it with MIDI Translator Pro OPEN and the project loaded. It needs `mido`:

    pip install mido python-rtmidi
    BMTP=MyProject.bmtp python3 examples/midi_test_plan.py

TWO RULES THIS PLAN FOLLOWS, and why:

1.  It READS channels and numbers from the project (`find_trigger`) instead of retyping
    them. The channel is 0-based in the file, and a plan that retypes numbers drifts
    away from the configuration it claims to test.
2.  It DECLARES the silences it expects (`expect_silence=True`). A plan that misses its
    target observes nothing everywhere; without declared silences, that reads as a pass.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mido

from bome_project.probe import Case, find_trigger, run, triggers
from bome_project.project import bome_running

# ── the four settings to adapt ──────────────────────────────────────────────────
CONFIG = Path(os.environ.get("BMTP", "MyProject.bmtp")).expanduser()
PORT = "Virtual Bus"  # a virtual port Bome listens on AND writes back to
PRESET = 0  # the preset under test
CC_IN, CC_OUT = 20, 7  # what the translator receives, what it must send


def cases(trigs: list) -> list[Case]:
    t = find_trigger(trigs, PRESET, CC_IN)  # channel read from the project
    return [
        Case(
            title=f"CC{CC_IN} ch{t.channel} -> CC{CC_OUT} ch1",
            send=mido.Message("control_change", channel=t.mido_channel, control=CC_IN, value=64),
            expected={(1, CC_OUT)},
        ),
        Case(
            title="an unmapped CC -> silence",
            send=mido.Message("control_change", channel=t.mido_channel, control=99, value=64),
            expect_silence=True,
        ),
    ]


def main() -> int:
    if not bome_running():
        # Without Bome nothing translates, and a run of pure silence cannot be told
        # apart from a broken configuration.
        print("MIDI Translator Pro is not running.", file=sys.stderr)
        return 2
    return 1 if run(PORT, cases(triggers(CONFIG))) else 0


if __name__ == "__main__":
    sys.exit(main())
