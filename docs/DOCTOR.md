# Doctor preflight

`repomin doctor` checks whether a repository is ready for a reduction without
changing the source tree or creating an output directory. It detects supported
manifest and source reducers, checks the selected toolchain, and validates the
output and sibling metadata paths that a normal run would use.

Start with a read-only project scan:

```sh
repomin doctor .
```

Without `--output`, Doctor checks the normal `SOURCE-minimal` default. An
existing payload, an existing `OUTPUT.repomin` sidecar, a symbolic link, or an
output inside the source is reported as a failure because a new reduction would
reject the same path. Pass the intended path when the command depends on its
working-directory basename:

```sh
repomin doctor . --output /tmp/project-repro
```

When a reproduction command is available, ask Doctor to run the configured
failure oracle twice in fresh copies before spending time on reduction:

```sh
repomin doctor . \
  --command 'python -m pytest -q' \
  --match 'FAILED tests/test_regression.py' \
  --adapter python \
  --source-reducer python \
  --output /tmp/project-repro
```

Use `--exit-code` when output is unstable, or `--process-failure` to learn an
exact process termination signature. `--baseline-runs N` changes the number
of fresh checks; it defaults to two. Doctor never treats a command's output as
an issue report and does not include stdout/stderr in its result.

For CI scripts, add `--json`. The result contains `ok`, detected adapter and
source-reducer details, the effective source size after exact ignore rules,
resolved output and metadata paths, per-check status, and (when requested)
baseline pass counts. Exit code `0` means all requested checks passed, `1`
means a check or baseline failed, and `2` means the Doctor invocation itself
was invalid. A passing baseline only says that the configured failure was
observed in the recorded environment; it is not a correctness claim.

The baseline runs in disposable copies and inherits the normal host or Docker
trust boundary. Do not run an untrusted command on the host backend, and review
the repository and environment settings before sharing Doctor output.
