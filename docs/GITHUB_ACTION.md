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
  uses: fly1d/repomin@main
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
the action ref pinned to a reviewed release or commit for production CI.

## Inputs

`command` and `match` are required. `source`, `output`, `adapter`,
`source-reducer`, `backend`, `docker-image`, `docker-network`, `timeout`, and
`jobs` map directly to the corresponding CLI options. `python-version` selects
the action runtime, and `artifact-name` controls the uploaded artifact name.
The default backend is `host`; use Docker when the command should run inside an
existing local image:

```yaml
- name: Minimize Docker failure
  if: ${{ failure() }}
  uses: fly1d/repomin@main
  with:
    command: python3 reproduce.py
    match: "ORIGINAL_FAILURE"
    backend: docker
    docker-image: my-reproduction-image:ci
    docker-network: none
```

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
code correctness or production reliability.

For local reproduction and report validation, see [EXAMPLES.md](EXAMPLES.md)
and [REPORT_SCHEMA.md](REPORT_SCHEMA.md).
