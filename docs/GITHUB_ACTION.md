# GitHub Action

ReproMin can turn a failing CI job into a downloadable minimized repository.
The action runs the configured reproduction command, then uploads both the
payload and its sibling `.repomin` report directory as one artifact.

## Example

Add this step after the command that identifies the failure, or use it as the
job's failure-handling step:

```yaml
- name: Minimize failure
  if: ${{ failure() }}
  uses: fly1d/repomin@v0.1.0.dev4
  with:
    command: python -m pytest -q
    match: "FAILED tests/test_regression.py"
    adapter: python
    source-reducer: python
    output: .repomin-result
    artifact-name: minimized-reproduction
```

The checkout must happen before this step. The action installs ReproMin from
the selected ref and uses `GITHUB_WORKSPACE` as the repository boundary. Keep
the action ref pinned to a reviewed release or full commit SHA for production
CI; the version above is the current pre-release.

## Inputs

`command` is required. Set at least one of `match`, `exit-code`, or
`process-failure` to define the failure oracle. `match` is useful when the
failure text is stable; `exit-code` is safer when test output changes between
runs; `process-failure: true` learns and preserves the exact signal or process
termination signature. `exit-code` and `process-failure` are mutually
exclusive, while `match` may be combined with either to narrow the oracle.

`source`, `output`, `adapter`, `source-reducer`, `backend`, `docker-image`,
`docker-network`, `timeout`, and `jobs` map directly to the corresponding CLI
options. `python-version` selects the action runtime, and `artifact-name`
controls the uploaded artifact name.

`ignore` and `ignore-path` accept one exact entry per line. Use them for
secrets or private fixtures that must never enter the uploaded payload;
`gitignore: true` applies the repository's root `.gitignore`, and
`gitignore-recursive: true` also applies nested rule files. These exclusions are
passed as structured arguments, not evaluated as shell code. A missing rule
file or invalid path causes the action to fail before it publishes an artifact.

```yaml
    ignore: |
      .env
      credentials
    ignore-path: |
      test/private-fixtures
    gitignore: true
```

For a fresh final certification, set all three holdout inputs. The action keeps
holdout samples separate from ordinary candidate evidence and reports the
result through `holdout-status`:

```yaml
- name: Certify minimized failure
  if: ${{ failure() }}
  uses: fly1d/repomin@v0.1.0.dev4
  with:
    command: python -m pytest -q
    match: "FAILED tests/test_regression.py"
    holdout-runs: "5"
    min-holdout-rate: "0.8"
    holdout-confidence: "0.95"
```

For a command with a stable exit code but unstable output:

```yaml
- name: Minimize failure
  if: ${{ failure() }}
  uses: fly1d/repomin@v0.1.0.dev4
  with:
    command: python -m pytest -q
    exit-code: "1"
    adapter: python
```
The default backend is `host`; use Docker when the command should run inside an
existing local image:

```yaml
- name: Minimize Docker failure
  if: ${{ failure() }}
  uses: fly1d/repomin@v0.1.0.dev4
  with:
    command: python3 reproduce.py
    match: "ORIGINAL_FAILURE"
    backend: docker
    docker-image: my-reproduction-image:ci
    docker-network: none
```

## Outputs

Give the action an `id` when a later step needs to inspect the generated files.
The action exposes absolute workspace paths for the payload and report, the
artifact name, and scalar report facts useful for downstream gates:
`report-schema-version`, `source-files`, `output-files`, `attempts`,
`accepted-mutations`, and `holdout-status`.

```yaml
- name: Minimize failure
  if: ${{ failure() }}
  id: minimize
  uses: fly1d/repomin@v0.1.0.dev4
  with:
    command: python -m pytest -q
    match: "FAILED tests/test_regression.py"

- name: Validate minimized report
  if: ${{ always() && steps.minimize.conclusion == 'success' }}
  run: |
    repomin report validate \
      "${{ steps.minimize.outputs.report-path }}" \
      --payload "${{ steps.minimize.outputs.payload-path }}" --json
```

`payload-path` points to the minimized tree, `report-path` points to its
`report.json`, and `artifact-name` is the name passed to
`actions/upload-artifact`. The numeric outputs are copied from the generated
report and are strings, as required by the Actions output protocol.
`holdout-status` is `not_requested` when no holdout inputs are supplied.
The action validates the report and payload fingerprint before publishing its
outputs. Report validation returns exit code `2` for an invalid report or
payload fingerprint, so it can also be used as a later CI gate without
rerunning the original failure command.

`source` and `output` must be repository-relative paths and cannot escape the
workspace. The command is intentionally passed to the configured ReproMin
runner; do not use this action with untrusted workflow input. The host backend
is not a sandbox, and Docker is not a complete security boundary. Read
[SECURITY.md](../SECURITY.md) before minimizing an untrusted project.

## Artifact contract

The artifact contains the reduced payload at `output` and the sibling metadata
directory at `output.repomin`. The report records the command outcome,
reduction attempts, execution backend, and payload fingerprint. It is evidence
for the configured reproduction in the recorded environment, not a proof of
code correctness or production reliability. The action explicitly includes
hidden paths so the default `.repomin-result` payload and its `.repomin`
metadata directory are uploaded by GitHub Actions.

For local reproduction and report validation, see [EXAMPLES.md](EXAMPLES.md)
and [REPORT_SCHEMA.md](REPORT_SCHEMA.md).
