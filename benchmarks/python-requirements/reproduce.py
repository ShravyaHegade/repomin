from pathlib import Path
import sys


root = Path("requirements.txt").read_text(encoding="utf-8")
runtime = Path("requirements/runtime.txt").read_text(encoding="utf-8")
ci = Path("requirements/ci.txt").read_text(encoding="utf-8")
constraints = Path("constraints.txt").read_text(encoding="utf-8")

required = (
    (root, "-r requirements/runtime.txt"),
    (root, "-c constraints.txt"),
    (runtime, "repomin-runtime==1.2.3"),
    (runtime, "--hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
    (runtime, "-r ci.txt"),
    (ci, "repomin-ci-runner==4.5.0"),
    (constraints, "repomin-runtime<2"),
    (constraints, "repomin-ci-runner<5"),
)
if not all(token in content for content, token in required):
    print("DIFFERENT_FAILURE", file=sys.stderr)
    raise SystemExit(2)

print("ORIGINAL_FAILURE: dependency-regression", file=sys.stderr)
raise SystemExit(1)
