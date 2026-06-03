# voice_dry_run.py - preview Kiwi voice transcript routing without executing actions.
from __future__ import annotations

import sys

from e2e_dry_run import main as e2e_main
from voice_planner import main as planner_main


def main(argv: list[str]) -> int:
    use_legacy = "--legacy-classifier" in argv
    filtered = [item for item in argv if item != "--legacy-classifier"]
    if use_legacy:
        rewritten: list[str] = ["--mode", "voice"]
        iterator = iter(filtered)
        for item in iterator:
            if item == "--utterance":
                rewritten.extend(["--intent", next(iterator, "")])
            else:
                rewritten.append(item)
        return e2e_main(rewritten)

    rewritten = []
    iterator = iter(argv)
    for item in iterator:
        if item == "--utterance":
            rewritten.extend(["--transcript", next(iterator, "")])
        else:
            rewritten.append(item)
    return planner_main(rewritten)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
