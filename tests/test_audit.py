"""Tests for bome_project.audit -- run with `python3 -m unittest` from the repo root.

stdlib only, on purpose: the toolkit has no dependency and its tests should not add one.

The fixture below is SYNTHETIC but every line follows a shape copied from a real
project saved by MT Pro 1.9.2 (SetVar incoming, ControlChange outgoing with a variable
in Value1, the Options header with its upper-case StMa count). A real project cannot be
committed -- it carries port names and a signature -- and a fixture whose lines Bome
would never write would test a parser for a format that does not exist.

Each test pins one behaviour that was either a bug once (`oo` filed as UNDOCUMENTED,
Perform parameters read as never written, heads o..x unknown) or is the reason the
audit exists (an early `execute` that fires before its controller number is assigned).
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from bome_project import audit

MID_IN = (
    'MID3<Incoming Action="MIDI"><Ports><Port>Keys</Port></Ports>'
    '<Simple Type="ControlChange"><Channel var="{ch}" Type="SetVar"/>'
    '<Value1 var="{v1}" Type="SetVar"/><Value2 var="{v2}" Type="SetVar"/>'
    "</Simple></Incoming>"
)
CC_OUT = (
    'MID3<Outgoing Action="MIDI"><Ports><Port>Synth</Port></Ports>'
    '<Simple Type="ControlChange"><Channel num="0"/><Value1 var="{v1}"/>'
    '<Value2 var="{v2}"/></Simple></Outgoing>'
)
PERFORM_IN = 'Prfm<Incoming Action="Perform"><Name>zone</Name><Params>qq,pp</Params></Incoming>'
PERFORM_OUT = 'Prfm<Outgoing Action="Perform"><Name>zone</Name><Params>ga,{p}</Params></Outgoing>'


def options(*rules):
    """The Options record as Bome writes it: fixed header, UPPER-case hex count, rules."""
    return "Actv01Stop00OutO00StMa%08X%s" % (len(rules), "".join(rules))


def preset(num, name, active, translators):
    lines = [f"[Preset.{num}]", f"Active={1 if active else 0}", f"Name={name}"]
    for i, (incoming, outgoing, opts) in enumerate(translators):
        lines += [
            f"Incoming{i}={incoming}",
            f"Name{i}=t{i}",
            f"Options{i}={opts}",
            f"Outgoing{i}={outgoing}",
        ]
    return "\n".join(lines)


FIXTURE = "\n\n".join(
    [
        "; ------- Bome MIDI Translator: synthetic test project",
        # Preset 0 and 1, both active, both write the global `ka` -> a collision.
        # Preset 0 also fires `execute` before `rr` is assigned: the silent-rig bug.
        preset(
            0,
            "Keys A",
            True,
            [
                (
                    MID_IN.format(ch="qq", v1="rr", v2="pp"),
                    CC_OUT.format(v1="rr", v2="pp"),
                    options("if(pp==0)execute", "rr=7", "ka=pp"),
                ),
            ],
        ),
        preset(
            1,
            "Keys B",
            True,
            [
                (
                    MID_IN.format(ch="qq", v1="oo", v2="pp"),
                    "None",
                    options("ka=pp", "sg=1", "if(pp<64)noexecute", "zz=cu+1"),
                ),
            ],
        ),
        # Preset 2 is INACTIVE and also writes `ka`: it must not count as a collision.
        # It reads `sg` without writing it -> a de-facto global.
        preset(
            2,
            "Spare",
            False,
            [
                (
                    MID_IN.format(ch="qq", v1="rr", v2="pp"),
                    CC_OUT.format(v1="ka", v2="sg"),
                    options("ka=1"),
                ),
            ],
        ),
        # Preset 3: a Perform receives its parameters (writes) and another sends them (reads).
        preset(
            3,
            "Perform",
            True,
            [
                (PERFORM_IN, "None", options("vv=qq+pp")),
                (MID_IN.format(ch="qq", v1="rr", v2="pp"), PERFORM_OUT.format(p="pp"), options()),
            ],
        ),
    ]
)


class Classify(unittest.TestCase):
    def test_the_ten_doubled_names_are_local(self):
        for v in ("oo", "pp", "qq", "rr", "ss", "tt", "uu", "vv", "ww", "xx"):
            self.assertEqual(audit.classify(v), "local", v)

    def test_oo_is_local__regression_2026_09_27(self):
        # It was missing from LOCALS, so a genuine local was reported as UNDOCUMENTED.
        self.assertEqual(audit.classify("oo"), "local")

    def test_heads_g_to_n_y_z_are_global_whatever_follows(self):
        for v in ("ga", "g0", "gg", "ka", "mz", "nn", "y1", "zz"):
            self.assertEqual(audit.classify(v), "global", v)

    def test_heads_o_to_x_with_another_character_are_global__regression_2026_10_11(self):
        # Steve Caldwell's second correction; until then these were UNDOCUMENTED.
        for v in ("oa", "o1", "pc", "q0", "sg", "tp", "v1", "xa"):
            self.assertEqual(audit.classify(v), "global", v)

    def test_heads_a_to_f_are_undocumented(self):
        for v in ("a1", "cu", "f5"):
            self.assertEqual(audit.classify(v), "UNDOCUMENTED", v)


class Payload(unittest.TestCase):
    def test_strips_the_fixed_header_and_the_rule_count(self):
        self.assertEqual(audit.payload("Options0=" + options("pp=1", "rr=2")), "pp=1rr=2")

    def test_an_options_record_without_rules_has_an_empty_payload(self):
        self.assertEqual(audit.payload("Options0=Actv01Stop00OutO00"), "")

    def test_a_rule_count_of_ten_or_more_is_upper_case_hex(self):
        rules = ["pp=%d" % i for i in range(12)]
        line = "Options0=" + options(*rules)
        self.assertIn("StMa0000000C", line)
        self.assertEqual(audit.payload(line), "".join(rules))


class Scan(unittest.TestCase):
    def scan(self, *lines):
        return audit.scan(list(lines))

    def test_incoming_setvar_is_a_write(self):
        w, r = self.scan("Incoming0=" + MID_IN.format(ch="qq", v1="rr", v2="pp"))
        self.assertEqual(w, {"qq", "rr", "pp"})
        self.assertEqual(r, set())

    def test_outgoing_var_is_a_read(self):
        w, r = self.scan("Outgoing0=" + CC_OUT.format(v1="rr", v2="ka"))
        self.assertEqual((w, r), (set(), {"rr", "ka"}))

    def test_rule_assignment_is_a_write_and_operand_a_read(self):
        w, r = self.scan("Options0=" + options("ss=pp+ka"))
        self.assertEqual(w, {"ss"})
        self.assertEqual(r, {"pp", "ka"})

    def test_a_comparison_is_not_an_assignment(self):
        w, r = self.scan("Options0=" + options("if(pp==0)noexecute"))
        self.assertEqual(w, set())
        self.assertEqual(r, {"pp"})

    def test_incoming_perform_params_are_writes__regression(self):
        # Reading them as reads reported every parameter as "read but never written".
        w, r = self.scan("Incoming0=" + PERFORM_IN)
        self.assertEqual((w, r), ({"qq", "pp"}, set()))

    def test_outgoing_perform_params_are_reads(self):
        w, r = self.scan("Outgoing0=" + PERFORM_OUT.format(p="pp"))
        self.assertEqual((w, r), (set(), {"ga", "pp"}))

    def test_name_and_header_lines_are_ignored(self):
        self.assertEqual(self.scan("Name0=pp=rr", "Comments=ka=1"), (set(), set()))


class Report(unittest.TestCase):
    presets: dict[str, dict]
    out: str

    """End to end: write the fixture to disk and read what `main` prints."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "fixture.bmtp"
            path.write_text(FIXTURE, encoding="utf-8")
            cls.presets = audit.parse(path)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                audit.main(path)
            cls.out = out.getvalue()

    def section(self, title):
        """The lines of one `== title ==` section, without its header."""
        body = self.out.split("== %s" % title, 1)[1]
        return body.split("\n== ", 1)[0]

    def test_parse_reads_presets_names_and_active_flags(self):
        self.assertEqual(list(self.presets), ["Preset.0", "Preset.1", "Preset.2", "Preset.3"])
        self.assertEqual(self.presets["Preset.1"]["name"], "Keys B")
        self.assertFalse(self.presets["Preset.2"]["active"])
        self.assertIn("4 presets, 3 active", self.out)

    def test_scope_lists_each_family(self):
        scope = self.section("scope")
        self.assertRegex(scope, r"UNDOCUMENTED\s+cu\n")
        self.assertRegex(scope, r"global\s+ga ka sg zz\n")
        self.assertRegex(scope, r"local\s+oo pp qq rr vv\n")

    def test_a_global_written_by_two_active_presets_is_a_collision(self):
        coll = self.section("collisions")
        self.assertIn("ka", coll)
        self.assertIn("Preset.0 «Keys A» · Preset.1 «Keys B»", coll)
        self.assertNotIn("Preset.2", coll)  # inactive: not a collision
        self.assertIn("-> 1", coll)

    def test_locals_are_never_collisions(self):
        # pp is written by every preset, which is exactly what locals are for.
        coll = self.section("collisions")
        self.assertNotRegex(coll, r"\n  pp ")

    def test_a_name_read_but_not_written_in_its_preset_is_a_de_facto_global(self):
        defacto = self.section("de-facto globals")
        self.assertRegex(defacto, r"Preset\.2 +off +Spare +reads sg\n")
        self.assertRegex(defacto, r"Preset\.1 +ACTIVE +Keys B +reads cu\n")
        self.assertNotIn("Preset.0", defacto)  # reads only what it writes

    def test_a_name_read_but_written_nowhere_is_reported(self):
        # cu in a rule, ga as a Perform parameter: neither is written by anyone.
        self.assertRegex(self.section("read but NEVER written"), r"\n  cu ga\n")

    def test_a_variable_as_controller_number_is_reported_with_its_scope(self):
        cc = self.section("outgoing actions with a VARIABLE")
        self.assertIn("controller = rr  [local]", cc)
        self.assertIn("controller = ka  [global]", cc)

    def test_an_early_execute_before_an_assignment_is_reported(self):
        early = self.section("early exits")
        self.assertIn("'if(pp==0)execute' fires before ka/rr is assigned", early)

    def test_noexecute_is_not_an_early_execute(self):
        self.assertNotIn("noexecute' fires", self.section("early exits"))


if __name__ == "__main__":
    unittest.main()
