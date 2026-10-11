"""Tests for bome_project.probe -- run with `python3 -m unittest` from the repo root.

Two halves. `triggers` / `find_*` read a project file and need nothing else. `Probe` /
`run` talk to MIDI through `mido`: here `mido` is replaced by a fake loopback port, so
the tests need neither the library nor a running MT Pro -- what they check is the
VERDICT logic, which is where the 2026-09-27 false regression came from.
"""

import contextlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from bome_project import probe
from bome_project.probe import Case


def incoming(kind, channel, value1, port="Keys"):
    return (
        'MID3<Incoming Action="MIDI"><Ports><Port>%s</Port></Ports>'
        '<Simple Type="%s">%s%s</Simple></Incoming>' % (port, kind, channel, value1)
    )


FIXTURE = """; ------- Bome MIDI Translator: synthetic test project
[Preset.0]
Active=1
Incoming0=%s
Incoming1=%s
Incoming2=%s
Incoming3=Prfm<Incoming Action="Perform"><Name>zone</Name></Incoming>
Name=Zones
Name0=CC on channel 12
Name1=hex note, any channel
Name2=any CC
Outgoing0=None

[Preset.1]
Active=0
Incoming0=%s
Name0=inactive

[Preset.2]
Incoming0=%s
Name0=no Active key

[Preset.3]
Incoming0=MID2MIDA00010004KeysB307pp
Incoming1=MID2ppqqrr
Incoming2=MID2F04110ppF7
""" % (
    incoming("ControlChange", '<Channel num="11"/>', '<Value1 num="20"/>'),
    incoming("NoteOn", '<Channel num="0" Type="Any"/>', '<Value1 num="0x3C"/>', port="Pads"),
    incoming("ControlChange", '<Channel num="2"/>', '<Value1 var="rr" Type="SetVar"/>'),
    incoming("ControlChange", '<Channel num="0"/>', '<Value1 num="99"/>'),
    incoming("ProgramChange", '<Channel num="15"/>', '<Value1 num="5"/>'),
)


class Triggers(unittest.TestCase):
    all: list[probe.Trigger]

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "fixture.bmtp"
            path.write_text(FIXTURE, encoding="utf-8")
            cls.all = probe.triggers(path)

    def test_reads_only_midi_incoming_of_active_presets(self):
        # Preset.1 is inactive; the Perform incoming is not MIDI; a preset without an
        # Active key counts as active (Bome's default). MID2 incoming counts as MIDI.
        self.assertEqual(
            [(t.preset, t.index) for t in self.all],
            [(0, 0), (0, 1), (0, 2), (2, 0), (3, 0), (3, 1), (3, 2)],
        )

    def test_the_channel_is_zero_based_in_the_file(self):
        self.assertEqual(self.all[0].channel, 12)  # num="11" is channel 12
        self.assertEqual(self.all[3].channel, 16)

    def test_type_any_means_any_channel(self):
        self.assertIsNone(self.all[1].channel)

    def test_number_hex_decimal_or_variable(self):
        self.assertEqual([t.number for t in self.all], [20, 0x3C, None, 5, 7, None, None])

    def test_a_mid2_trigger_with_a_literal_status_byte(self):
        t = self.all[4]  # B3 07 pp: ControlChange, channel 4, CC 7
        self.assertEqual((t.port, t.kind, t.channel, t.number), ("Keys", "ControlChange", 4, 7))
        self.assertIs(probe.find_trigger(self.all, 3, 7), t)

    def test_a_mid2_trigger_on_any_message_or_on_sysex(self):
        raw, sysex = self.all[5], self.all[6]
        self.assertEqual((raw.kind, raw.channel, raw.number), ("Raw", None, None))
        self.assertEqual((sysex.kind, sysex.channel, sysex.number), ("SysEx", None, None))

    def test_mido_channel_is_zero_based_and_refuses_any_channel(self):
        self.assertEqual(self.all[0].mido_channel, 11)  # channel 12
        with self.assertRaisesRegex(ValueError, "listens on any channel"):
            _ = self.all[1].mido_channel

    def test_name_port_and_kind(self):
        t = self.all[1]
        self.assertEqual((t.name, t.port, t.kind), ("hex note, any channel", "Pads", "NoteOn"))

    def test_find_trigger_by_number(self):
        self.assertEqual(probe.find_trigger(self.all, 0, 20).channel, 12)
        with self.assertRaises(LookupError):
            probe.find_trigger(self.all, 0, 99)  # only in the inactive preset

    def test_find_any_by_kind_when_the_number_is_a_variable(self):
        self.assertEqual(probe.find_any(self.all, 0, "ControlChange").index, 0)
        self.assertEqual(probe.find_any(self.all, 2, "ProgramChange").number, 5)
        with self.assertRaises(LookupError):
            probe.find_any(self.all, 0, "PitchBend")


# ── a fake mido: one virtual port that loops back, plus a scripted "Bome" ────────


def cc(channel, control, value=64):
    """A mido-like message. `channel` is 0-based, as in mido."""
    return SimpleNamespace(type="control_change", channel=channel, control=control, value=value)


def note(channel, n):
    return SimpleNamespace(type="note_on", channel=channel, note=n, velocity=100)


class FakeMido:
    """`open_output` / `open_input` on a shared queue.

    Like a real virtual port, whatever is sent comes back -- which is why Probe has to
    discard its own message. `responder(msg)` returns what the configuration would emit.
    """

    def __init__(self, responder=lambda msg: []):
        self.responder = responder
        self.queue, self.lock = [], threading.Lock()

    def open_output(self, port):
        fake = self

        class Out:
            def send(self, msg):
                with fake.lock:
                    fake.queue.append(msg)
                    fake.queue.extend(fake.responder(msg))

            def close(self):
                pass

        return Out()

    def open_input(self, port):
        fake = self

        class In:
            def iter_pending(self):
                with fake.lock:
                    pending, fake.queue[:] = list(fake.queue), []
                return iter(pending)

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

        return In()


class ProbeVerdicts(unittest.TestCase):
    """What counts as a pass. One Probe per test: ~0.5 s each, for the port settle."""

    def play(self, responder, case):
        with mock.patch.object(probe, "mido", FakeMido(responder)), probe.Probe("virtual") as p:
            return p.play(case)

    def test_the_expected_messages_pass(self):
        ok, seen = self.play(
            lambda m: [cc(0, 7), cc(1, 8)],
            Case("two out", cc(11, 20), expected={(1, 7), (2, 8)}, wait_s=0.05),
        )
        self.assertTrue(ok)
        self.assertEqual(seen, {(1, 7), (2, 8)})

    def test_the_message_sent_is_not_mistaken_for_an_answer(self):
        ok, seen = self.play(
            lambda m: [], Case("echo only", cc(11, 20), expect_silence=True, wait_s=0.05)
        )
        self.assertTrue(ok)
        self.assertEqual(seen, set())

    def test_a_wrong_answer_fails(self):
        ok, _ = self.play(
            lambda m: [cc(0, 9)], Case("wrong cc", cc(11, 20), expected={(1, 7)}, wait_s=0.05)
        )
        self.assertFalse(ok)

    def test_silence_fails_unless_it_was_declared(self):
        ok, _ = self.play(lambda m: [], Case("mute", cc(11, 20), expected={(1, 7)}, wait_s=0.05))
        self.assertFalse(ok)

    def test_a_case_that_declares_nothing_cannot_pass_on_silence(self):
        # THE guard of this module: a case aimed at the wrong channel observes nothing.
        # If it declared no expectation and no silence, nothing must not read as a pass.
        ok, _ = self.play(lambda m: [], Case("forgot to declare", cc(11, 20), wait_s=0.05))
        self.assertFalse(ok)

    def test_declared_silence_fails_when_something_comes_out(self):
        ok, seen = self.play(
            lambda m: [cc(0, 7)], Case("leak", cc(11, 20), expect_silence=True, wait_s=0.05)
        )
        self.assertFalse(ok)
        self.assertEqual(seen, {(1, 7)})

    def test_keep_selects_which_messages_count(self):
        ok, seen = self.play(
            lambda m: [note(0, 60), cc(0, 7)],
            Case(
                "notes only",
                cc(11, 20),
                expected={(1, 60)},
                keep=lambda m: m.type == "note_on",
                wait_s=0.05,
            ),
        )
        self.assertTrue(ok)
        self.assertEqual(seen, {(1, 60)})

    def test_without_mido_the_probe_says_what_to_install(self):
        with mock.patch.object(probe, "mido", None), self.assertRaises(SystemExit) as e:
            probe.Probe("virtual")
        self.assertIn("pip install mido", str(e.exception))


class RunReport(unittest.TestCase):
    def test_counts_mismatches_and_prints_the_table(self):
        def bome(msg):
            return [cc(0, 7)] if msg.control == 20 else []

        cases = [
            Case("answers", cc(11, 20), expected={(1, 7)}, wait_s=0.05),
            Case("mute", cc(11, 21), expected={(1, 7)}, wait_s=0.05),
            Case("silent on purpose", cc(11, 22), expect_silence=True, wait_s=0.05),
        ]
        out = io.StringIO()
        with mock.patch.object(probe, "mido", FakeMido(bome)), contextlib.redirect_stdout(out):
            failures = probe.run("virtual", cases)
        self.assertEqual(failures, 1)
        lines = out.getvalue().splitlines()
        self.assertRegex(lines[2], r"^answers .*\[\(1, 7\)\] +OK$")
        self.assertRegex(lines[3], r"^mute .* nothing +!! MISMATCH$")
        self.assertRegex(lines[4], r"^silent on purpose +silence +nothing +OK$")
        self.assertEqual(lines[-1], "3 case(s) · 1 mismatch(es)")


if __name__ == "__main__":
    unittest.main()
