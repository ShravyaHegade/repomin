# Node package manifest benchmark

This fixture exercises the strict `package.json` adapter without network
access or package installation. The oracle keeps `required-sdk` and
`packages/required`, rejecting a candidate that removes either; unrelated
dependencies, scripts, workspaces, and
overrides are removable noise.

Run from the repository root:

```sh
out_parent="$(mktemp -d /tmp/repomin-node-package.XXXXXX)"
PYTHONPATH=src python3 -m repomin benchmarks/node-package \
  --command 'python3 reproduce.py' \
  --match 'ORIGINAL_FAILURE' \
  --adapter node \
  --source-reducer none \
  --output "$out_parent/result"
```

The independent check should still return exit code `1` and print
`ORIGINAL_FAILURE`; the exported `package.json` must retain `required-sdk` and
`packages/required`, while omitting `unused-sdk`, `unused-test-tool`, the
unused script, and the unused workspace/override entries.
