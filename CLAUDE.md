# Notes for contributors and coding agents

This repository is **public**. It holds tools for Bome MIDI Translator Pro project files and
nothing else. The README is the user documentation; this file is about working on the code.

## Scope — what belongs here

- Code, tests and documentation about the **`.bmtp` format and the tools**.
- **Never** a real project file, a personal test plan, or anything that names a specific
  person's rig: port names, device names, preset names, file paths, songs. A real project
  carries its author's setup and a signature.
- Test fixtures and examples use **made-up** names (`Keys`, `Pads`, `Synth`, `Virtual Bus`,
  `MyProject.bmtp`). When a fixture copies a real shape, change every name in it.
- Commit messages are public too: describe the format and the fix, not whose project
  revealed it.

## Rules the code depends on

- **Standard library only** for the library and its tests. `mido` stays optional: `probe`
  imports it lazily and its tests use a fake port.
- **Python 3.9** is the oldest supported version (CI runs it). mypy checks as 3.10 because
  mypy 2.x cannot target 3.9.
- **Every format rule was observed, not documented.** Each one is stated in the
  `project.py` docstring with how it was found. When a reader meets a shape it has never
  seen, it **raises** rather than guesses (`Project.routes`, `mid2`) — a reader that skips
  what it does not understand describes a project that is not the one Bome runs.
- **A write must be byte-identical when nothing changed.** That is the module's promise and
  most of `tests/test_project.py` checks it. Keys are sorted as text (`Name10` before
  `Name2`), a preset ends at the next section of any kind, the blank line between sections
  is preserved.
- **Writes refuse to run while MIDI Translator Pro runs** (`refuse_if_running`), and take a
  backup that never overwrites another.
- `probe`: a `Case` passes on silence **only** when it declares `expect_silence=True`.

## Before pushing

```bash
python3 -m unittest
pip install -r requirements-dev.txt
ruff check . && ruff format --check . && mypy
```

Behaviour changes ship with a test; a bug fix ships with the test that would have caught
it. Versions are pinned (Actions by SHA, tools in `requirements-dev.txt`); Dependabot
proposes updates.

## Not done, on purpose

- The MIDI Router is read, not written (a few routes are quicker in Bome's editor).
- Nothing reads the `.bmts` settings file.
