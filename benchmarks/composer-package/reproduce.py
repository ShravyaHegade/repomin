import json
from pathlib import Path

manifest = json.loads(Path("composer.json").read_text(encoding="utf-8"))
required = manifest.get("require", {})
autoload = manifest.get("autoload", {})
if required.get("repomin/required") != "1.0.0":
    print("DIFFERENT_FAILURE")
    raise SystemExit(2)
if "psr-4" not in autoload:
    print("DIFFERENT_FAILURE")
    raise SystemExit(3)
print("ORIGINAL_FAILURE")
raise SystemExit(1)
