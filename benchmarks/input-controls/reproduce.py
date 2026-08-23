import pathlib
import sys


if not pathlib.Path("required.txt").exists():
    print("missing required.txt", file=sys.stderr)
    raise SystemExit(3)

if not pathlib.Path("exit-sentinel.txt").exists():
    print("missing exit-sentinel.txt", file=sys.stderr)
    raise SystemExit(1)

print("INPUT_CONTROLS_FAILURE", file=sys.stderr)
raise SystemExit(7)
