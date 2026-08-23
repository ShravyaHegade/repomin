# Good first issues

These are starter tasks that do not require deep knowledge of the reduction
engine. Each should include tests or documentation, following
[CONTRIBUTING.md](../CONTRIBUTING.md).

## Tooling and ergonomics

- Improve `--help` copy for the most common options.
- Add shell completion for common shells.
- Add a coverage step to the `quality` CI job and keep it informative.
- Improve error messages for common misconfigurations such as a missing
  `--match`, an invalid Docker limit, or a non-existent `--keep` path.

## Documentation

- Record an asciinema or GIF for a real before/after reduction and link it from
  the README.
- Translate the README or add a language-specific quick start.
- Add a worked example for a new ecosystem to `docs/EXAMPLES.md`.

## New adapters

- Add a structured manifest adapter for a build system not yet supported (for
  example, Bazel `MODULE.bazel`, Pipenv `Pipfile`, or a `.NET`
  `Directory.Build.props` file). Follow the hashed-text-range rules in
  [ARCHITECTURE.md](ARCHITECTURE.md) and add a network-free benchmark.

## Benchmarks

- Add a new fixture under `benchmarks/` and register it in
  `benchmarks/run_offline.py`.
- Tighten an existing benchmark's acceptance gate with deterministic file
  hashes and report assertions.
