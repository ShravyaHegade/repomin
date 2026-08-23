# Go module benchmark

This network-free module has one required local module, one unused local
module, and extra `exclude`/`retract` directives. The oracle is `go run` with
the module proxy disabled.

```sh
out_parent="$(mktemp -d /tmp/repomin-go-module.XXXXXX)"
PYTHONPATH=src python3 -m repomin benchmarks/go-module \
  --command 'GOPROXY=off go run .' \
  --match 'ORIGINAL_FAILURE' \
  --adapter go \
  --source-reducer none \
  --output "$out_parent/result"
```

The independent command should exit with Go's panic status and contain
`ORIGINAL_FAILURE`. The reduced tree must retain `example.com/required` and
its replacement while allowing the unused module and unrelated directives to
disappear.
