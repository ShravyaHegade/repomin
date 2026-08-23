from pathlib import Path
import sys


if not Path("required.txt").exists():
    print("DIFFERENT_FAILURE: required input is missing", file=sys.stderr)
    raise SystemExit(2)

print("ORIGINAL_FAILURE: docker backend fixture", file=sys.stderr)
raise SystemExit(1)
