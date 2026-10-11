"""Tests for bome_project.project -- run with `python3 -m unittest` from the repo root.

The fixture copies the LAYOUT of a file saved by MT Pro 1.9.2: presets whose keys are
sorted as text (Name before Name0, Incoming10 before Incoming2), a blank line between
sections, and the [Project] and [Signatures] sections after the last preset. Values are
synthetic -- a real project carries port names and a signature.

The promise this module makes is "written back byte-identical when unchanged", so most
tests here are round trips: read, render or write, compare bytes.
"""

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bome_project import project
from bome_project.project import (
    Preset,
    Project,
    Route,
    Translator,
    decode_ports,
    mid2,
    read_ports,
)

IN = (
    'MID3<Incoming Action="MIDI"><Ports><Port>Keys</Port></Ports>'
    '<Simple Type="ControlChange"><Channel num="{ch}"/><Value1 num="{n}"/>'
    '<Value2 var="pp" Type="SetVar"/></Simple></Incoming>'
)
OUT = (
    'MID3<Outgoing Action="MIDI"><Ports><Port>Synth</Port></Ports>'
    '<Simple Type="ControlChange"><Channel num="0"/><Value1 num="7"/>'
    '<Value2 var="pp"/></Simple></Outgoing>'
)


def bome_sorted(lines):
    """Bome's order: the KEY compared as text (rule 2)."""
    return sorted(lines, key=lambda line: line.split("=", 1)[0])


def preset_block(num, name, count, active=1):
    lines = [f"Active={active}", f"Name={name}", "PresetSwitchIgnore=0"]
    for i in range(count):
        lines += [
            f"Incoming{i}=" + IN.format(ch=i % 16, n=20 + i),
            f"Name{i}=tr {i}",
            f"Options{i}=Actv01Stop00OutO00StMa00000001pp=pp+{i}",
            f"Outgoing{i}=" + (OUT if i % 2 == 0 else "None"),
        ]
    return f"[Preset.{num}]\n" + "\n".join(bome_sorted(lines))


# Eleven translators in preset 1, so that Incoming10 has to sort between 1 and 2.
FIXTURE = (
    "\n\n".join(
        [
            "; ------- Bome MIDI Translator: signed project file",
            preset_block(0, "First", 2),
            preset_block(1, "Dense", 11),
            preset_block(2, "Last", 1, active=0),
            "[Project]\nDefaultInPorts=Keys\nVersion=1",
            "[Signatures]\nSigName-1=test\nSignature-1=AAAA\nSignedBy-1=test",
        ]
    )
    + "\n"
)


class TempProject(unittest.TestCase):
    """Each test gets its own copy on disk, and Bome is never 'running'."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "fixture.bmtp"
        self.path.write_text(FIXTURE, encoding="utf-8")
        patcher = mock.patch.object(project, "bome_running", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def backups(self):
        return sorted(p.name for p in Path(self.dir.name).iterdir() if ".bak-" in p.name)


class Parsing(TempProject):
    def test_reads_every_preset_and_its_translators(self):
        proj = Project(self.path)
        self.assertEqual(sorted(proj.presets), [0, 1, 2])
        self.assertEqual(len(proj.presets[1].translators), 11)
        self.assertEqual(proj.presets[1].name, "Dense")

    def test_recollects_the_four_keys_of_an_index(self):
        t = Project(self.path).presets[1].translators[10]
        self.assertEqual(t.name, "tr 10")
        self.assertIn('<Value1 num="30"/>', t.incoming)
        self.assertEqual(t.outgoing, OUT)
        self.assertTrue(t.options.endswith("pp=pp+10"))

    def test_active_flag(self):
        proj = Project(self.path)
        self.assertTrue(proj.presets[0].active)
        self.assertFalse(proj.presets[2].active)

    def test_the_last_preset_does_not_swallow_project_and_signatures(self):
        # The sections after the last preset are NOT preset keys. Reading them as such
        # makes write() pull them into the preset and drop their headers.
        header = Project(self.path).presets[2].header
        for key in ("DefaultInPorts", "Version", "SigName-1", "Signature-1", "SignedBy-1"):
            self.assertNotIn(key, header)

    def test_is_signed(self):
        self.assertTrue(Project(self.path).is_signed())
        self.path.write_text(FIXTURE.split("[Signatures]")[0], encoding="utf-8")
        self.assertIsNone(Project(self.path).is_signed())


class Render(TempProject):
    def block(self, num):
        m = re.search(r"^\[Preset\.%d\]\n.*?(?=\n\n|\Z)" % num, FIXTURE, re.M | re.S)
        assert m is not None, num
        return m.group(0)

    def test_an_unchanged_preset_renders_byte_identical(self):
        proj = Project(self.path)
        for num in (0, 1, 2):
            self.assertEqual(proj.presets[num].render(), self.block(num), num)

    def test_keys_sort_as_text_not_as_numbers(self):
        rendered = Project(self.path).presets[1].render().split("\n")[1:]
        keys = [line.split("=", 1)[0] for line in rendered]
        names = [k for k in keys if k.startswith("Name")]
        self.assertEqual(names[:4], ["Name", "Name0", "Name1", "Name10"])
        self.assertEqual(names[4], "Name2")

    def test_an_empty_outgoing_is_written_as_None(self):
        p = Preset(5, {"Active": "1"})
        p.add(Translator())
        self.assertIn("Outgoing0=None", p.render())


class Write(TempProject):
    def test_writing_an_unchanged_preset_leaves_the_file_byte_identical(self):
        for num in (0, 1, 2):
            Project(self.path).write(num)
            self.assertEqual(self.path.read_text(encoding="utf-8"), FIXTURE, num)

    def test_writing_the_last_preset_keeps_project_and_signatures(self):
        proj = Project(self.path)
        proj.presets[2].translators[0].name = "renamed"
        proj.write(2)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("Name0=renamed", text)
        self.assertTrue(
            text.endswith(
                "[Project]\nDefaultInPorts=Keys\nVersion=1\n\n"
                "[Signatures]\nSigName-1=test\nSignature-1=AAAA\nSignedBy-1=test\n"
            )
        )

    def test_an_edit_changes_only_its_own_line(self):
        proj = Project(self.path)
        proj.presets[1].translators[3].name = "edited"
        proj.write(1)
        before, after = FIXTURE.split("\n"), self.path.read_text(encoding="utf-8").split("\n")
        diff = [(a, b) for a, b in zip(before, after) if a != b]
        self.assertEqual(diff, [("Name3=tr 3", "Name3=edited")])

    def test_a_backup_is_taken_before_writing_and_holds_the_old_text(self):
        proj = Project(self.path)
        proj.presets[0].translators[0].name = "edited"
        backup = proj.write(0)
        self.assertEqual(backup.read_text(encoding="utf-8"), FIXTURE)
        self.assertRegex(backup.name, r"\.bak-\d{8}-\d{6}-p0$")

    def test_two_writes_in_the_same_second_never_overwrite_a_backup(self):
        proj = Project(self.path)
        with mock.patch.object(project.time, "strftime", return_value=".bak-20260101-000000"):
            first = proj.write(0)
            second = proj.write(0)
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.backups()), 2)

    def test_an_unknown_preset_raises(self):
        with self.assertRaises(LookupError):
            Project(self.path).write(42)

    def test_refuses_to_write_while_bome_runs(self):
        proj = Project(self.path)
        proj.presets[0].translators[0].name = "edited"
        with (
            mock.patch.object(project, "bome_running", return_value=True),
            self.assertRaises(SystemExit),
        ):
            proj.write(0)
        self.assertEqual(self.path.read_text(encoding="utf-8"), FIXTURE)
        self.assertEqual(self.backups(), [])


# The router section as a real project carries it: before the first preset.
ROUTES = (
    "[MIDI.routes]\nIn0=Keyboard In\nOut0=Ableton\nIn1=Keys\nOut1=Ableton\nIn2=Keys\nOut2=Synth"
)
WITH_ROUTES = FIXTURE.replace("\n\n[Preset.0]", "\n\n" + ROUTES + "\n\n[Preset.0]", 1)


class Routes(TempProject):
    def load(self, text):
        self.path.write_text(text, encoding="utf-8")
        return Project(self.path)

    def test_reads_each_in_out_pair_in_index_order(self):
        self.assertEqual(
            self.load(WITH_ROUTES).routes,
            [
                Route(0, "Keyboard In", "Ableton"),
                Route(1, "Keys", "Ableton"),
                Route(2, "Keys", "Synth"),  # one port, two destinations: two routes
            ],
        )

    def test_a_project_without_a_router_has_no_routes(self):
        self.assertEqual(Project(self.path).routes, [])

    def test_the_router_does_not_disturb_the_presets(self):
        proj = self.load(WITH_ROUTES)
        self.assertEqual(sorted(proj.presets), [0, 1, 2])
        proj.write(0)
        self.assertEqual(self.path.read_text(encoding="utf-8"), WITH_ROUTES)

    def test_comments_and_blank_lines_are_skipped(self):
        text = WITH_ROUTES.replace("In1=Keys", "; a comment\nIn1=Keys")
        self.assertEqual(len(self.load(text).routes), 3)

    def test_an_unknown_key_raises_rather_than_being_dropped(self):
        with self.assertRaisesRegex(ValueError, "unknown line 'Options0=Actv01'"):
            _ = self.load(WITH_ROUTES.replace("Out2=Synth", "Out2=Synth\nOptions0=Actv01")).routes

    def test_a_route_missing_its_destination_raises(self):
        with self.assertRaisesRegex(ValueError, r"route 2 has only \['In'\]"):
            _ = self.load(WITH_ROUTES.replace("\nOut2=Synth", "")).routes


class PortLists(unittest.TestCase):
    # The shape of a real [Project] DefaultInPorts value, port names changed: the
    # lengths are hex, 0x13 = 19.
    REAL = "MIDA00030013Keyboard Controller0008DrumPads0007Ableton"

    def test_decodes_count_then_length_prefixed_names(self):
        self.assertEqual(decode_ports(self.REAL), ["Keyboard Controller", "DrumPads", "Ableton"])

    def test_mida0000_is_no_port(self):
        self.assertEqual(decode_ports("MIDA0000"), [])

    def test_refuses_what_is_not_a_port_list(self):
        with self.assertRaisesRegex(ValueError, "not a port list"):
            decode_ports("Ableton")

    def test_refuses_a_list_longer_than_its_count(self):
        with self.assertRaisesRegex(ValueError, "does not end where its count says"):
            decode_ports(self.REAL + "0003xyz")

    def test_read_ports_says_where_the_list_stops(self):
        names, end = read_ports("MID2MIDA00010007Abletonppqqrr", 4)
        self.assertEqual((names, "MID2MIDA00010007Abletonppqqrr"[end:]), (["Ableton"], "ppqqrr"))


class Mid2(unittest.TestCase):
    """The two MID2 shapes seen on real projects, and the refusal of any other."""

    def test_a_raw_pattern_with_a_port_list(self):
        self.assertEqual(mid2("MID2MIDA00010007Abletonppqqrr"), (["Ableton"], ["pp", "qq", "rr"]))

    def test_hex_literals_and_variables_mix_byte_by_byte(self):
        self.assertEqual(mid2("MID2F0411000ppF7"), ([], ["F0", "41", "10", "00", "pp", "F7"]))

    def test_other_actions_are_not_mid2(self):
        self.assertIsNone(mid2(OUT))
        self.assertIsNone(mid2("None"))

    def test_an_unknown_pattern_raises(self):
        for bad in ("MID2F", "MID2ABC", "MID2<xml/>"):
            with self.assertRaisesRegex(ValueError, "unexpected MID2 pattern"):
                mid2(bad)


class InPorts(unittest.TestCase):
    def t(self, incoming):
        t = Translator()
        t.incoming = incoming
        return t

    def test_mid3_names_ports_in_port_elements(self):
        self.assertEqual(self.t(IN.format(ch=0, n=1)).in_ports, ["Keys"])

    def test_mid2_carries_a_port_list_after_its_prefix(self):
        self.assertEqual(self.t("MID2MIDA00010007Abletonppqqrr").in_ports, ["Ableton"])

    def test_no_port_means_the_defaults(self):
        self.assertEqual(self.t("MID2ppqqrr").in_ports, [])
        self.assertEqual(self.t('MID3<Incoming Action="MIDI"><Simple/></Incoming>').in_ports, [])


class BomeRunning(unittest.TestCase):
    def test_matches_the_exact_process_name(self):
        # pgrep -f would match the invoking shell too (see the docstring).
        with mock.patch.object(project.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertTrue(project.bome_running())
            self.assertEqual(run.call_args.args[0], ["pgrep", "-x", "MIDITranslatorPro"])
            run.return_value.returncode = 1
            self.assertFalse(project.bome_running())


class TranslatorFields(unittest.TestCase):
    def t(self, incoming="", outgoing="None", options=""):
        t = Translator()
        t.incoming, t.outgoing, t.options = incoming, outgoing, options
        return t

    def test_emits(self):
        self.assertTrue(self.t(outgoing=OUT).emits)
        self.assertFalse(self.t(outgoing="None").emits)
        self.assertFalse(self.t(outgoing="").emits)

    def test_the_channel_is_zero_based_in_the_file(self):
        self.assertEqual(self.t(IN.format(ch=11, n=1)).channel, 12)
        self.assertEqual(self.t(IN.format(ch=0, n=1)).channel, 1)

    def test_type_any_means_any_channel(self):
        self.assertIsNone(self.t('<Channel num="3" Type="Any"/>').channel)
        self.assertIsNone(self.t("").channel)

    def test_number_decimal_hex_or_variable(self):
        self.assertEqual(self.t('<Value1 num="64"/>').number, 64)
        self.assertEqual(self.t('<Value1 num="0x40"/>').number, 64)
        self.assertIsNone(self.t('<Value1 var="rr" Type="SetVar"/>').number)

    def test_rules_read_through_an_upper_case_count(self):
        self.assertEqual(self.t(options="Actv01Stop00OutO00StMa0000000Cpp=1").rules(), ["pp=1"])
        self.assertEqual(self.t(options="Actv01Stop00OutO00").rules(), [])

    def test_set_rules_writes_the_count_in_upper_case_hex(self):
        t = self.t()
        t.set_rules(["pp=%d" % i for i in range(12)])
        self.assertTrue(t.options.startswith("Actv01Stop00OutO00StMa0000000Cpp=0pp=1"))
        t.set_rules([])
        self.assertEqual(t.options, "Actv01Stop00OutO00")


class PresetEdits(unittest.TestCase):
    def preset(self, n):
        p = Preset(0, {"Active": "1"})
        for i in range(n):
            t = Translator()
            t.name = f"t{i}"
            p.add(t)
        return p

    def test_add_appends_at_the_next_index(self):
        p = self.preset(3)
        self.assertEqual(p.add(Translator()), 3)
        self.assertEqual(Preset(0).add(Translator()), 0)

    def test_remove_renumbers_contiguously(self):
        p = self.preset(5)
        p.remove([1, 3])
        self.assertEqual(sorted(p.translators), [0, 1, 2])
        self.assertEqual([p.translators[i].name for i in range(3)], ["t0", "t2", "t4"])

    def test_active_defaults_to_true_and_name_to_empty(self):
        self.assertTrue(Preset(0).active)
        self.assertEqual(Preset(0).name, "")


if __name__ == "__main__":
    unittest.main()
