from pathlib import Path
import sys

content = Path("Pipfile").read_text(encoding="utf-8")
if "required-package" not in content:
    print("DIFFERENT_FAILURE", file=sys.stderr)
    raise SystemExit(2)

print("ORIGINAL_FAILURE", file=sys.stderr)
raise SystemExit(1)
