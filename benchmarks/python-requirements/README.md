# Python requirements-chain benchmark

This network-free fixture models a common CI dependency-regression layout:
the top-level requirements file includes a runtime file, the runtime file
includes CI-only tools, and a constraints file pins compatible versions. The
oracle requires that complete chain and the hash-pinned runtime requirement to
remain present. Unrelated packages and package-index options are removable
noise.

Run it from the repository root:

```sh
out_parent="$(mktemp -d /tmp/repomin-python-requirements.XXXXXX)"
PYTHONPATH=src python3 -m repomin benchmarks/python-requirements \
  --command 'python3 reproduce.py' \
  --match 'ORIGINAL_FAILURE' \
  --adapter python \
  --source-reducer none \
  --output "$out_parent/result"
```

The independent command should exit with status `1` and print
`ORIGINAL_FAILURE`. The reduced payload must retain the two-level include
chain, `repomin-runtime==1.2.3`, `repomin-ci-runner==4.5.0`, and their
constraints, while removing the unused requirements and index options. The
sidecar report is written to the sibling `result.repomin` directory and can be
checked with `repomin report validate`.
