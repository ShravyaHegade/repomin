import json
import sys


with open("package.json", encoding="utf-8") as stream:
    package = json.load(stream)

required = package.get("dependencies", {}).get("required-sdk")
workspaces = package.get("workspaces", [])
if required != "1.0.0" or "packages/required" not in workspaces:
    print("DIFFERENT_FAILURE", file=sys.stderr)
    raise SystemExit(2)

print("ORIGINAL_FAILURE", file=sys.stderr)
raise SystemExit(1)
