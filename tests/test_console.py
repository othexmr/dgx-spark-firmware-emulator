"""The console driver's two contracts: it can spell a shell command, and it refuses what it cannot.

The keys it sends are QEMU qcodes, so a wrong entry is a wrong keystroke on a guest whose console is
the only channel — worth a test even though it is a lookup table, because the failure is silent.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.console import CHARS, send_text  # noqa: E402


class Recorder:
    """Stands in for the QMP client and records the key names sent."""

    def __init__(self):
        self.sent = []

    def key(self, names, hold_ms=30):
        self.sent.extend(names)


class ConsoleKeyTests(unittest.TestCase):
    def test_a_representative_command_is_spellable(self):
        command = ("mount -t proc proc /proc; mount -t sysfs sys /sys\n"
                   "cat /sys/kernel/debug/devices_deferred\n"
                   "dmesg | tail -n 40\n"
                   "ls -l /sys/bus/pci/devices/000f:00:02.0/driver\n")
        missing = sorted({c for c in command if c not in CHARS})
        self.assertEqual(missing, [], f"no key for {missing}")

    def test_shifted_characters_send_shift_and_the_base_key(self):
        recorder = Recorder()
        send_text(recorder, "A:1", 0)
        self.assertEqual(recorder.sent, ["shift", "a", "shift", "semicolon", "1"])

    def test_an_unknown_character_is_refused_rather_than_guessed(self):
        with self.assertRaises(SystemExit):
            send_text(Recorder(), "\u00e9", 0)

    def test_the_count_matches_what_was_sent(self):
        recorder = Recorder()
        self.assertEqual(send_text(recorder, "abc", 0), 3)
        self.assertEqual(len(recorder.sent), 3)


if __name__ == "__main__":
    unittest.main()
