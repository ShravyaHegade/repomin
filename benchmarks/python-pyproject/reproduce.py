from pathlib import Path
import sys


content = Path("pyproject.toml").read_text(encoding="utf-8")
required = (
    'name = "repomin-pyproject-fixture"',
    "repomin-required==1.0",
)
if not all(token in content for token in required):
    print("DIFFERENT_FAILURE", file=sys.stderr)
    raise SystemExit(2)

print("ORIGINAL_FAILURE", file=sys.stderr)
raise SystemExit(1)
