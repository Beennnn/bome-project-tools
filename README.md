# bome-project-tools

**Read, audit and safely rewrite Bome MIDI Translator Pro project files.** The `.bmtp`
format is not documented anywhere; this repository is what we learned by editing a real
88-translator project, with every rule verified against Bome 1.9.2 by reopening the
file Bome produced.

> **Status: private, work in progress.** It does one real job today — it replaced 21
> translators with 3 in a live rig, verified 9 cases out of 9 against hardware — but the
> library is shaped by that one job and nothing else has used it yet.

---

## Why

A Bome project is a silent state machine. A translator that does not match does not
complain: it simply does nothing, for the whole show. There is no log, no error, no
"rule 14 never fired". The only verdict is to send a message and watch what comes out.

And a project grows by duplication, because a translator has **exactly one outgoing
action**. Seven triggers that must each reach three destinations is not seven
translators — it is twenty-one. Add a fourth destination and it is twenty-eight.

Two tools follow from that: one that edits the file without breaking it, and one that
proves the result on real MIDI.

## The format, in nine rules

Each one cost at least one mistake.

```
; ------- Bome MIDI Translator: signed project file
[Preset.6]
Active=1
Comments=…\n…                  newlines are escaped
Incoming0=MID3<Incoming …>     one translator per INDEX, spread over 4 keys
Incoming1=Prfm<Incoming …>
Name=Zones etat                the PRESET name, no index
Name0=CC112 -> ya P1 bas       translator 0's name
Options0=Actv01Stop00OutO00StMa00000001ya=uu
Outgoing0=None                 "None" = this translator emits nothing
PresetSwitchIgnore=0
[Signatures]
```

1. **A translator is spread over four indexed keys** — `Name`, `Incoming`, `Outgoing`,
   `Options` — never grouped. Touching one means recollecting all four.
2. **Lines are sorted alphabetically** by key name, then numerically by index. Writing
   them in any other order yields a file Bome accepts but rewrites wholesale on the
   first save, making every future diff unreadable.
3. **Numbering must be contiguous.** Deleting translator 11 of 39 means renumbering the
   27 that follow, across all four keys.
4. **`Outgoing<N>=None`** — the literal string — is a translator that emits nothing. It
   only sets variables through its rules. That is a free slot, and it is what makes
   some refactors cost nothing.
5. **The channel is 0-based.** `<Channel num="11"/>` is channel **12**. An adjacent
   `Type="Any"` means "any channel" and makes the number moot. This one produced a
   false regression diagnosis.
6. **`Options` is a fixed-length record followed by rules**: `Actv01` active ·
   `Stop00` does not stop processing · `OutO00` outgoing options · `StMa` + **eight hex
   digits giving the NUMBER of rules**, then the rules concatenated with no separator.
   Get the count wrong and the extra rules are silently ignored.
7. **The XML prefix names the action type**: `MID3` for MIDI, `Prfm` for a Perform. It
   is not decorative — Bome uses it to pick its parser.
8. **Bome rewrites the file from memory on save.** Editing while it is open means losing
   your work at the next ⌘S. `refuse_si_ouvert()` exists for that.
9. **Editing invalidates the signature.** The file still opens in MIDI Translator Pro,
   but BomeBox and MT Player refuse it. Only Bome can sign: the remedy is to open the
   project and save. Observed on a real rig — an invalid signature lived five days with
   no visible consequence on a machine that only runs MT Pro.

## The pattern that pays: one trigger, many actions

A translator has one outgoing action, so *N* triggers reaching *M* destinations costs
*N × M* translators. A **Perform** — a named event internal to Bome — turns the product
into a sum. Every translator listening for that name fires:

```
before               21 translators        7 triggers × 3 destinations
after                10                    7 emit "panic" + 3 execute
a 4th destination    +1  instead of +7
```

The manual recommends Perform over the older 0 ms one-shot timer for exactly this, and
guarantees a Perform is never retriggered — one emission, one event, no double firing.
Its parameters carry values, so one destination can filter on what the trigger sent
rather than needing its own copy.

Real result on the rig this came from: `Zones etat` went from 39 to 21 translators,
15 840 to 8 314 characters, with the ten other presets byte-identical.

## Testing: what can and cannot be reached

A translator is only testable if you can **write to the port it listens on**. On the rig
above, five of ten presets listen to hardware, and no script can inject into those:

| listens to | testable |
| --- | --- |
| a virtual port (`Rig Bus`) | **yes** |
| `P-Series`, `Breath Controller`, `Seaboard` | no — needs hands |

Which is an argument for routing hardware **through a virtual port** and having the
logic listen to that instead: it makes the logic testable, and adding a second keyboard
stops meaning duplicating every translator. Bome's built-in MIDI Router does port-to-port
without spending a translator at all.

`plans/rig-zones.py` shows the shape: cases that assert what comes out, and **cases that
assert silence**. The second kind is not optional — a plan that misses its target
produces "nothing" everywhere and reads like a pass. That is precisely how a 0-of-6 run
was mistaken for a regression when the configuration was fine and the test had the wrong
channel.

## Use

```bash
pip install mido python-rtmidi

python3 -c "
from pathlib import Path
from bome_project.project import Projet
p = Projet(Path('MyProject.bmtp'))
for n, pr in sorted(p.presets.items()):
    print(n, pr.nom, len(pr.translators), 'translators',
          '' if pr.actif else '(inactive)')
"

python3 plans/rig-zones.py          # drive real MIDI, print a verdict
```

Writing always takes a timestamped backup first, and refuses to run while Bome is open.

## Layout

```
bome_project/project.py   read/write a .bmtp — the nine rules live here
bome_project/probe.py     send MIDI, listen, compare; assert silence too
plans/rig-zones.py        one rig's test plan, as a worked example
```

## Not done yet

- The audit pass (duplicate translators, conflicting triggers, dead globals) exists as
  throwaway scripts, not as a tool.
- Nothing reads or writes the MIDI Router section of the settings file.
- One rig, one Bome version (1.9.2), macOS only. Everything here is verified, but
  verified once.

MIT.
