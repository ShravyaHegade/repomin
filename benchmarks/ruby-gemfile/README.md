# Ruby Gemfile benchmark

This network-free fixture exercises the Bundler `Gemfile` adapter with the
Ruby runtime. The oracle requires one gem declaration while allowing unrelated
single-line declarations and declarations inside a group to disappear. A
multiline call and `Gemfile.lock` are outside the adapter's mutation set.

Run from the repository root:

```sh
out_parent="$(mktemp -d /tmp/repomin-ruby-gemfile.XXXXXX)"
PYTHONPATH=src python3 -m repomin benchmarks/ruby-gemfile \
  --command 'ruby reproduce.rb' \
  --match 'ORIGINAL_FAILURE' \
  --adapter ruby \
  --source-reducer none \
  --output "$out_parent/result"
```

The independent Ruby command must still exit `1` with `ORIGINAL_FAILURE`, and
the exported Gemfile must retain `repomin-required`.
