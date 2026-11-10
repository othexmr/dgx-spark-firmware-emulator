#!/usr/bin/env python3
"""Type into a running emulated guest's serial console over QMP.

The boot runner hands the guest its only input through the emulated 16550, and a menuless installer or
a panic still has to be told what to do. This connects to the QMP socket a run left behind
(`boot_spark.py --qmp-alive PATH`) and sends keystrokes as key events, which is the same path a
physical keyboard takes.

`type` sends text through a small ASCII map; `keys` sends named keys. Both take `--delay` so a slow
TCG guest can consume each key before the next arrives — a serial FIFO that overruns is a lost
keystroke, not a diagnosis.

This drives a *guest*, never a unit: the socket belongs to a QEMU process started by the runner.
"""

import argparse
import json
import socket
import sys
import time
from pathlib import Path

# QEMU key names for the characters a shell command needs. Anything else is refused rather than
# guessed at, because a wrong key in a menu is worse than a refused one.
CHARS = {
    **{c: c for c in "abcdefghijklmnopqrstuvwxyz0123456789"},
    # A capital letter is shift plus the lower-case key. Mapping it to the bare key silently
    # types a different command.
    **{c.upper(): f"shift-{c}" for c in "abcdefghijklmnopqrstuvwxyz"},
    " ": "spc", "-": "minus", "/": "slash", "=": "equal", ".": "dot", ",": "comma",
    ":": "shift-semicolon", ";": "semicolon", "_": "shift-minus", "\n": "ret",
    "|": "shift-backslash", "'": "apostrophe", "\"": "shift-apostrophe", "*": "shift-8",
    "(": "shift-9", ")": "shift-0", ">": "shift-dot", "<": "shift-comma", "\\": "backslash",
    "?": "shift-slash", "!": "shift-1", "#": "shift-3", "$": "shift-4", "%": "shift-5",
    "&": "shift-7", "+": "shift-equal", "[": "bracket_left", "]": "bracket_right",
}


class Qmp:
    """A minimal QMP client: one greeting, then `send-key` commands."""

    def __init__(self, path: Path):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(10)
        self.socket.connect(str(path))
        self.file = self.socket.makefile("rwb")
        self._read()                     # greeting
        self.command("qmp_capabilities")

    def _read(self):
        while True:
            line = self.file.readline()
            if not line:
                raise SystemExit("the QMP socket closed")
            message = json.loads(line)
            if "event" in message:
                continue                 # STOP/RESET events are not part of the reply stream
            return message

    def command(self, name, arguments=None):
        payload = {"execute": name}
        if arguments is not None:
            payload["arguments"] = arguments
        self.file.write((json.dumps(payload) + "\n").encode())
        self.file.flush()
        reply = self._read()
        if "error" in reply:
            raise SystemExit(f"{name}: {reply['error'].get('desc')}")
        return reply.get("return")

    def key(self, names, hold_ms=30):
        """Send these key names together, as one chord."""
        keys = [{"type": "qcode", "data": name} for name in names]
        self.command("send-key", {"keys": keys, "hold-time": hold_ms})


def keys_for(character: str) -> list[str]:
    """The key names that produce this character, or a refusal."""
    name = CHARS.get(character)
    if name is None:
        raise SystemExit(f"no key for {character!r}; add it to CHARS rather than guessing")
    if name.startswith("shift-"):
        return ["shift", name[len("shift-"):]]
    return [name]


def type_line(qmp, line: str, delay: float = 0.0, sleep=time.sleep) -> int:
    """Type one line plus Enter through any object that speaks QMP's `request`.

    `qmp` is duck-typed on purpose: the standalone client here and the boot runner's own transport are
    different classes, and both must be able to drive a console.
    """
    sent = 0
    for character in line + "\n":
        keys = keys_for(character)
        qmp.request("send-key", {"keys": [{"type": "qcode", "data": name} for name in keys],
                                 "hold-time": 30})
        sent += 1
        if delay:
            sleep(delay)
    return sent


def send_text(qmp: Qmp, text: str, delay: float) -> int:
    for character in text:
        qmp.key(keys_for(character))
        time.sleep(delay)
    return len(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("type", "send text to the guest, one key at a time"),
                            ("keys", "send a space-separated list of QEMU key names")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("socket", type=Path, help="the QMP socket a run left at --qmp-alive")
        command.add_argument("text", nargs="+" if name == "type" else "+")
        command.add_argument("--delay", type=float, default=0.02,
                             help="seconds between keys; raise it on a slow guest")
        command.add_argument("--settle", type=float, default=0.0,
                             help="seconds to wait before the first key, for a boot that is still "
                                  "reaching its prompt")
    args = parser.parse_args()

    if not args.socket.exists():
        raise SystemExit(f"no QMP socket at {args.socket}; start the run with --qmp-alive")
    time.sleep(args.settle)
    qmp = Qmp(args.socket)
    if args.command == "type":
        count = send_text(qmp, " ".join(args.text), args.delay)
        print(f"sent {count} keys")
        return 0
    for name in args.text:
        qmp.key([name])
        time.sleep(args.delay)
    print(f"sent {len(args.text)} keys")
    return 0


if __name__ == "__main__":
    sys.exit(main())
