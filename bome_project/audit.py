#!/usr/bin/env python3
"""Audit variable usage in a Bome MIDI Translator Pro project (.bmtp).

Read-only. Reports scope violations, cross-preset collisions, and the two
outgoing-action patterns that turn a naming slip into silence on stage.

    python3 -m bome_project.audit MyProject.bmtp

Came from the `bome-variable-naming` repository, merged here on 2026-09-27. The two
projects had grown a parser each for the same format, which is exactly the duplication
this toolkit exists to remove -- so the audit keeps its own reporting and reads the
structure through `project.Project`.

The convention this audit checks is in `docs/variable-convention.md`.
"""

import re
import sys
from pathlib import Path

from .project import Project, decode_ports

# TEN locals, not nine. `oo` was missing here until 2026-09-27, when Steve Caldwell of
# Bome Customer Care pointed it out on the forum -- so the audit was reporting a genuine
# local as UNDOCUMENTED, which is the one kind of false positive this tool must not have.
LOCALS = {"oo", "pp", "qq", "rr", "ss", "tt", "uu", "vv", "ww", "xx"}
GLOBAL_HEADS = set("ghijklmnyz")
# Heads o..x are global too, as long as the second character differs from the head:
# their doubled form (oo..xx) is the local. Also from Steve Caldwell, on the forum
# (2026-08-23) -- missing until 2026-10-11, so `sg`, `pc`, `oa` were filed under
# UNDOCUMENTED when the language does document them.
MIXED_HEADS = set("opqrstuvwx")
# Reserved Channel Mode messages — a stray controller number here silences a rig.
CHANNEL_MODE = {
    120: "All Sound Off",
    121: "Reset All Controllers",
    122: "Local Control",
    123: "All Notes Off",
    124: "Omni Off",
    125: "Omni On",
    126: "Mono On",
    127: "Poly On",
}
VAR = r"[a-z][a-z0-9]"
OPS = "=(<>+-*/"


def parse(path):
    """-> {preset_id: {'active':bool, 'name':str, 'lines':[...]}} in file order.

    Reads through `project.Project` rather than scanning the file again. The audit
    arrived here carrying its own parser for the same format, which was the one
    duplication this toolkit exists to remove -- the shape below is kept because the
    reporting code speaks in flat lines, not because the parsing is separate.
    """
    proj = Project(Path(path))
    out = {}
    for num in sorted(proj.presets):
        pr = proj.presets[num]
        lines = [f"{k}={v}" for k, v in pr.header.items()]
        for i in sorted(pr.translators):
            tr = pr.translators[i]
            for field, val in (
                ("Name", tr.name),
                ("Incoming", tr.incoming),
                ("Outgoing", tr.outgoing),
                ("Options", tr.options),
            ):
                if val:
                    lines.append(f"{field}{i}={val}")
        out[f"Preset.{num}"] = {
            "active": pr.active,
            "name": pr.name or f"Preset.{num}",
            "lines": lines,
        }
    return out


def payload(options_line):
    """Strip the Options header, leaving the concatenated rule text."""
    body = options_line.split("=", 1)[1]
    return re.sub(r"^Actv\d+Stop\d+OutO\d+(StMa[0-9A-F]{8})?", "", body)


def scan(lines):
    """-> (writes, reads) over one preset."""
    writes, reads = set(), set()
    for line in lines:
        if not re.match(r"(Options|Incoming|Outgoing)\d+=", line):
            continue
        if line.startswith("Incoming"):
            writes |= set(re.findall(r'var="(%s)" Type="SetVar"' % VAR, line))
            # An incoming Perform or Timer RECEIVES its parameters into variables --
            # <Params>qq</Params> is a write, not a read. Missing this reported every
            # parameter variable as "read but never written", the same false positive
            # class as the one that had `oo` filed under UNDOCUMENTED.
            for params in re.findall(r"<Params>([^<]*)</Params>", line):
                writes |= set(re.findall(r"\b(%s)\b" % VAR, params))
        elif line.startswith("Outgoing"):
            reads |= set(re.findall(r'var="(%s)"' % VAR, line))
            for params in re.findall(r"<Params>([^<]*)</Params>", line):
                reads |= set(re.findall(r"\b(%s)\b" % VAR, params))
        else:
            p = payload(line)
            writes |= set(re.findall(r"(%s)=(?!=)" % VAR, p))
            reads |= set(re.findall(r"[%s](%s)" % (re.escape(OPS), VAR), p))
    return writes, reads


def classify(v):
    if v in LOCALS:
        return "local"
    if v[0] in GLOBAL_HEADS:
        return "global"
    if v[0] in MIXED_HEADS and v[1] != v[0]:
        return "global"
    return "UNDOCUMENTED"


def main(path):
    presets = parse(path)
    active = {k: v for k, v in presets.items() if v["active"]}
    print("%s — %d presets, %d active\n" % (path, len(presets), len(active)))

    allvars: set[str] = set()
    writers: dict[str, set[str]] = {}  # variable -> presets that write it
    readers: dict[str, set[str]] = {}
    for pid, p in presets.items():
        w, r = scan(p["lines"])
        allvars |= w | r
        for v in w:
            writers.setdefault(v, set()).add(pid)
        for v in r:
            readers.setdefault(v, set()).add(pid)

    print("== scope ==")
    for kind in ("UNDOCUMENTED", "global", "local"):
        names = sorted(v for v in allvars if classify(v) == kind)
        if names:
            print("  %-13s %s" % (kind, " ".join(names)))
    print(
        "  (UNDOCUMENTED = neither one of the ten locals nor a global:"
        " head g/h/i/j/k/l/m/n/y/z, or head o..x with a different second character)"
    )

    print("\n== collisions: written by more than one ACTIVE preset ==")
    print("  (locals are excluded: oo..xx cannot leak between translators)")
    hits = 0
    for v in sorted(allvars):
        if v in LOCALS:
            continue
        ps = sorted(p for p in writers.get(v, ()) if p in active)
        if len(ps) > 1:
            hits += 1
            print("  %-4s %s" % (v, " · ".join("%s «%s»" % (p, presets[p]["name"]) for p in ps)))
    print("  none" if not hits else "  -> %d" % hits)

    print("\n== de-facto globals: read in a preset that never writes them ==")
    for pid, p in sorted(presets.items()):
        w, r = scan(p["lines"])
        ext = sorted(r - w)
        if ext:
            print(
                "  %-11s %-6s %-28s reads %s"
                % (pid, "ACTIVE" if p["active"] else "off", p["name"][:28], " ".join(ext))
            )

    dead = sorted(v for v in allvars if v in readers and v not in writers)
    if dead:
        print("\n== read but NEVER written anywhere (always 0) ==\n  " + " ".join(dead))

    print("\n== outgoing actions with a VARIABLE as controller number ==")
    for pid, p in sorted(presets.items()):
        for line in p["lines"]:
            if not line.startswith("Outgoing"):
                continue
            m = re.search(r'Type="ControlChange".*?<Value1 var="(%s)"' % VAR, line)
            if m:
                print(
                    "  %-11s %-6s %-12s controller = %s  [%s]"
                    % (
                        pid,
                        "ACTIVE" if p["active"] else "off",
                        line.split("=")[0],
                        m.group(1),
                        classify(m.group(1)),
                    )
                )

    print("\n== early exits that fire the outgoing action ==")
    for pid, p in sorted(presets.items()):
        for line in p["lines"]:
            if not line.startswith("Options"):
                continue
            body = payload(line)
            for m in re.finditer(r"if\([^)]*\)execute", body):
                tail = body[m.end() :]
                assigned = set(re.findall(r"(%s)=(?!=)" % VAR, tail))
                if assigned:
                    print(
                        "  %-11s %-6s %-10s '%s' fires before %s is assigned"
                        % (
                            pid,
                            "ACTIVE" if p["active"] else "off",
                            line.split("=")[0],
                            m.group(0),
                            "/".join(sorted(assigned)),
                        )
                    )
    print(
        "\n  ('noexecute' leaves the rules WITHOUT firing — use it when the branch means"
        "\n   'nothing to send'. Reserved Channel Mode targets: %s)"
        % ", ".join("CC%d=%s" % (k, v) for k, v in sorted(CHANNEL_MODE.items()))
    )

    proj = Project(Path(path))
    print("\n== MIDI router: port-to-port thru, no translator involved ==")
    for r in proj.routes:
        print("  %-30s -> %s" % (r.source, r.destination))
    if not proj.routes:
        print("  none")
    else:
        router_leaks(proj)


def input_ports(proj, preset, translator):
    """The ports a translator listens on: its own, else its preset's, else the project's.

    That order is the README's observation that a port named on the translator
    OVERRIDES the preset default. An empty list means none is declared anywhere.
    """
    if translator.in_ports:
        return translator.in_ports
    for value in (preset.header.get("DefaultInPorts"), project_default_in(proj)):
        if value and decode_ports(value):
            return decode_ports(value)
    return []


def project_default_in(proj):
    m = re.search(r"^\[Project\]$(.*?)(?:^\[|\Z)", proj.text, re.M | re.S)
    found = m and re.search(r"^DefaultInPorts=(.*)$", m.group(1), re.M)
    return found.group(1) if found else None


def router_leaks(proj):
    """Translators on a ROUTED input port that can skip their outgoing action.

    Steve Caldwell (Bome, forum topic 7925): when the rules exit without executing the
    outgoing action, the incoming message "will pass through untouched based on how you
    have your MIDI Router set". So on a routed port, every `noexecute` branch sends the
    RAW message on to the route's destination. Often intended -- a translator that
    handles a few values and lets the rest through -- but invisible in the translator
    list, which is why it is reported.
    """
    routed: dict[str, list[str]] = {}  # source port -> destinations
    for r in proj.routes:
        routed.setdefault(r.source, []).append(r.destination)
    print("\n== routed ports: where the RAW message still goes on through the router ==")
    hits = 0
    for num in sorted(proj.presets):
        pr = proj.presets[num]
        for i in sorted(pr.translators):
            tr = pr.translators[i]
            if "noexecute" not in tr.options:
                continue
            for port in input_ports(proj, pr, tr):
                if port in routed:
                    hits += 1
                    print(
                        "  Preset.%-3d %-6s %-28s Incoming%-2d %s -> %s on its noexecute branch"
                        % (
                            num,
                            "ACTIVE" if pr.active else "off",
                            (pr.name or "")[:28],
                            i,
                            port,
                            ", ".join(routed[port]),
                        )
                    )
    print(
        "  none"
        if not hits
        else "  -> %d (intended when a translator lets the rest through)" % hits
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
