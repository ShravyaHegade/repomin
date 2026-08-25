# Report Schema

`report.json` is the machine-readable evidence sidecar for one reduction. It is
written next to the exported payload at `OUTPUT.repomin/report.json`; keeping
it outside the payload means report writes cannot change a tree that already
passed the oracle.

The current top-level schema version is `1`. Consumers should reject an
unsupported `schema_version`, tolerate additional fields within a supported
version, and never infer code correctness from a passing oracle.

## Top-level fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer report format version. Current value: `1`. |
| `command` | Exact reproduction command passed to the runner. |
| `failure_match` | Configured output regular expression, or `null` for process/exit-code modes. |
| `baseline_exit_code` | Return code observed during baseline validation. |
| `final_exit_code` | Return code observed during final validation of the accepted tree. |
| `source` | File/byte counts for the copied source tree before reduction. |
| `output` | File/byte counts for the exported payload, excluding the sidecar. |
| `attempts` | Logical candidate attempts, including no-ops and cache uses. |
| `accepted_mutations` | Number of promoted candidate mutations. |
| `cache_hits` | Session-local content-cache uses. These are not oracle executions. |
| `execution` | Runner, sampling, ignore-rule, and resource configuration. |
| `phase_statistics` | Per-phase accounting and oracle sample usage. |
| `holdout_certification` | Optional fresh-sample certification of the exported artifact. |
| `events` | Ordered human-readable reduction events and their oracle evidence. |
| `java_exception_signature` | Present only with `--java-exception`. |
| `python_exception_signature` | Present only with `--python-exception`. |
| `process_failure_signature` | Present only with `--process-failure`. |

`source` and `output` contain `files` and `bytes`. Output counts deliberately
exclude `report.json` and `REPOMIN.md`.

## Execution

The `execution` object records the boundary in which commands were sampled.
Important fields include:

- `backend`: `host` or `docker`.
- `jobs`: maximum candidate concurrency.
- `cache_enabled`, `cache_hits`, and `resumed`.
- `baseline_runs`, `candidate_runs`, `final_runs`, and their pass counts.
- `confidence`, `min_baseline_rate`, `min_candidate_rate`, and the sampling
  policy identifiers.
- `reduction_strategy`: the reducer strategy identity used for this report and
  persistent-session compatibility.
- `ignored_names`, `ignored_paths`, `gitignore_files`, `keep_paths`, and
  `text_files`: input-selection controls applied before reduction.
- `environment_names` and `environment_sha256`: names and a digest of explicit
  environment values. Values are intentionally never recorded.

Docker reports additionally contain the image reference, resolved immutable
image ID, network policy, and configured resource limits when applicable.
These fields describe the execution boundary; they do not make Docker a
complete security sandbox.

## Phase accounting

`phase_statistics.phases` contains one object per reduction phase. Each phase
tracks attempts, no-ops, rejected/accepted/superseded/aborted candidates,
oracle sample uses, actual oracle samples, cache hits, and samples saved by
early stopping.

For complete reports, consumers can check both accounting identities:

```text
attempts = no_op + rejected + accepted + superseded + aborted
oracle_sample_uses = oracle_samples + cache_hits
```

`coverage` is `partial` when a legacy or interrupted session cannot provide a
complete phase history. Missing historical data must not be reconstructed from
the aggregate counters.

## Holdout certification

`holdout_certification.status` is `not_requested`, `certified`, `rejected`, or
an interrupted/aborted status. When certification is enabled, its samples are
fresh fixed-size runs against the frozen exported payload. They are separate
from baseline, candidate, and ordinary final-validation samples.

The report records the planned/completed sample counts, passes, exact lower
bound, exact p-value, resource/timeout veto counts, artifact fingerprint, and
the holdout policy identifier. A certified lower bound is a statistical claim
about oracle pass probability under fresh iid samples in the recorded
environment. It is not a proof of correctness, compatibility, or production
reliability.

## Events and signatures

Each `events` entry records the phase, description, duration, oracle pass/runs,
rate and lower-bound evidence, and (when applicable) candidate family
confidence and early-acceptance state. Event order is significant for audit
and resume diagnostics.

Signature objects preserve identity beyond a broad output match. Java and
Python signatures include exception class, message, and normalized frames.
Process signatures distinguish POSIX signals, Windows statuses, and ordinary
exit codes. Timeout and resource-exhaustion outcomes are never treated as a
matching failure signature.

## Consumer guidance

1. Verify `schema_version` and the payload fingerprint reported by holdout
   certification before trusting an artifact.
2. Check `execution.backend`, Docker identity/policy, environment names, and
   the reproduction command before sharing the sidecar.
3. Treat `failure_match` and signatures as the configured oracle contract, not
   as an explanation of every possible failure mode.
4. Keep `report.json` and `REPOMIN.md` beside the payload; do not copy either
   file into the tree when independently rerunning the command.

The architecture document explains the statistical contracts and reducer
invariants behind these fields. See [ARCHITECTURE.md](ARCHITECTURE.md) and
[SECURITY.md](../SECURITY.md) before processing untrusted repositories.
