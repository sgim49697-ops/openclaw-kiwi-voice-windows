# voice_dry_run.py - preview Kiwi voice routing without executing actions.
from __future__ import annotations

import sys

from e2e_dry_run import main as e2e_main


def main(argv: list[str]) -> int:
    rewritten: list[str] = ["--mode", "voice"]
    iterator = iter(argv)
    for item in iterator:
        if item == "--utterance":
            rewritten.extend(["--intent", next(iterator, "")])
        else:
            rewritten.append(item)
    return e2e_main(rewritten)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
