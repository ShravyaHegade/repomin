# Cargo workspace benchmark

This local-only workspace has one required path dependency, one unused path
dependency, and two unrelated workspace members. It needs no crates.io access.

Run from the repository root:

```sh
out_parent="$(mktemp -d /tmp/repomin-cargo-workspace.XXXXXX)"
PYTHONPATH=src python3 -m repomin benchmarks/cargo-workspace \
  --command 'CARGO_NET_OFFLINE=true cargo run -q -p app' \
  --match 'ORIGINAL_FAILURE' \
  --adapter cargo \
  --source-reducer none \
  --output "$out_parent/result"
```

The independent command should still exit with Cargo's failed-test status and
contain `ORIGINAL_FAILURE`. The reduced workspace must keep the `app` package
and its `required-lib` source dependency, while the unused dependency and
unrelated members may be gone.
