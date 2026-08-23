from pathlib import Path
import sys


if "NEEDLE" not in Path("data.txt").read_text():
    print("DIFFERENT_FAILURE", file=sys.stderr)
    raise SystemExit(2)

print("ORIGINAL_FAILURE", file=sys.stderr)
raise SystemExit(1)
