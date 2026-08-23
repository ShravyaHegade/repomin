from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Tuple

from repomin.model import ReductionResult, TREE_FINGERPRINT_POLICY
from repomin.signature import process_failure_name


def measure_tree(root: Path) -> Tuple[int, int]:
    files = 0
    size = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            files += 1
            size += path.stat().st_size
    return files, size


def write_report(
    result: ReductionResult,
    command: str,
    match: Optional[str],
    metadata: Path,
) -> None:
    metadata.mkdir()
    report = _build_report(result, command, match)
    (metadata / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (metadata / "REPOMIN.md").write_text(
        _reproduction_markdown(result, command, match),
        encoding="utf-8",
    )


def verify_existing_report(
    result: ReductionResult,
    command: str,
    match: Optional[str],
    metadata: Path,
) -> None:
    """Verify a sidecar left by a crash without overwriting user-visible files."""
    if not metadata.is_dir():
        raise ValueError("metadata output is not a directory: %s" % metadata)
    expected_names = {"report.json", "REPOMIN.md"}
    actual_names = {path.name for path in metadata.iterdir()}
    if actual_names != expected_names or not all(
        (metadata / name).is_file() for name in expected_names
    ):
        raise ValueError(
            "metadata output is incomplete or has unexpected entries: %s" % metadata
        )
    try:
        actual_report = json.loads(
            (metadata / "report.json").read_text(encoding="utf-8")
        )
        actual_markdown = (metadata / "REPOMIN.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("metadata output could not be verified: %s" % metadata) from exc

    expected_report = _build_report(result, command, match)
    # A report may have been fully written immediately before the process
    # crashed. Restoration itself changes only these provenance booleans.
    for report in (actual_report, expected_report):
        if isinstance(report, dict):
            execution = report.get("execution")
            if isinstance(execution, dict):
                execution.pop("resumed", None)
            certification = report.get("holdout_certification")
            if isinstance(certification, dict):
                certification.pop("resumed", None)
    if actual_report != expected_report:
        raise ValueError(
            "metadata output does not match the certified payload: %s" % metadata
        )
    if actual_markdown != _reproduction_markdown(result, command, match):
        raise ValueError(
            "metadata reproduction instructions do not match the certified payload: %s"
            % metadata
        )


def _build_report(
    result: ReductionResult,
    command: str,
    match: Optional[str],
) -> Dict[str, object]:
    stats = result.stats
    holdout = result.holdout_certification
    report: Dict[str, object] = {
        "schema_version": 1,
        "command": command,
        "failure_match": match,
        "baseline_exit_code": result.baseline.returncode,
        "final_exit_code": result.final_run.returncode,
        "source": {
            "files": stats.source_files,
            "bytes": stats.source_bytes,
        },
        "output": {
            "files": stats.output_files,
            "bytes": stats.output_bytes,
        },
        "attempts": stats.attempts,
        "accepted_mutations": stats.accepted,
        "cache_hits": stats.cache_hits,
        "execution": {
            "jobs": stats.jobs,
            "cache_enabled": stats.cache_enabled,
            "backend": stats.backend,
            "ignored_names": list(stats.ignored_names),
            "ignored_paths": list(stats.ignored_paths),
            "gitignore_files": list(stats.gitignore_files),
            "gitignore_sha256": stats.gitignore_sha256,
            "gitignore_recursive": stats.gitignore_recursive,
            "keep_paths": list(stats.keep_paths),
            "text_files": list(stats.text_files),
            "max_attempts": stats.max_attempts,
            "budget_exhausted": stats.budget_exhausted,
            "max_duration_seconds": stats.max_duration_seconds,
            "semantic_reducer": stats.semantic_reducer,
            "semantic_model": stats.semantic_model,
            "semantic_endpoint": stats.semantic_endpoint,
            "semantic_calls": stats.semantic_calls,
            "semantic_accepted": stats.semantic_accepted,
            "environment_names": list(stats.environment_names),
            "environment_sha256": stats.environment_sha256,
            "working_directory_policy": stats.working_directory_policy,
            "working_directory_basename": stats.working_directory_basename,
            "resumed": stats.resumed,
            "baseline_runs": stats.baseline_runs,
            "baseline_passes": stats.baseline_passes,
            "min_baseline_rate": stats.min_baseline_rate,
            "min_candidate_rate": stats.min_candidate_rate,
            "confidence": stats.confidence,
            "candidate_sampling_policy": stats.candidate_sampling_policy,
            "run_confidence": stats.run_confidence,
            "candidate_family_control_policy": (
                stats.candidate_family_control_policy
            ),
            "candidate_family_count": stats.candidate_family_count,
            "candidate_family_alpha_upper_bound": (
                stats.candidate_family_alpha_upper_bound
            ),
            "reduction_strategy": stats.reduction_strategy,
            "baseline_rate": stats.baseline_rate,
            "baseline_lower_bound": stats.baseline_lower_bound,
            "baseline_rate_evidence_runs": stats.baseline_rate_evidence_runs,
            "baseline_rate_evidence_passes": stats.baseline_rate_evidence_passes,
            "baseline_exact_lower_bound": stats.baseline_exact_lower_bound,
            "baseline_exact_p_value": stats.baseline_exact_p_value,
            "baseline_exact_rate_gate_passed": (
                stats.baseline_exact_rate_gate_passed
            ),
            "candidate_runs": stats.candidate_runs,
            "candidate_min_passes": stats.candidate_min_passes,
            "candidate_samples": stats.candidate_samples,
            "candidate_passes": stats.candidate_passes,
            "candidate_early_rejections": stats.candidate_early_rejections,
            "candidate_early_acceptances": stats.candidate_early_acceptances,
            "candidate_samples_saved": stats.candidate_samples_saved,
            "final_runs": stats.final_runs,
            "final_passes": stats.final_passes,
            "final_rate": stats.final_rate,
            "final_lower_bound": stats.final_lower_bound,
        },
        "phase_statistics": {
            "schema_version": 1,
            "coverage": (
                "complete" if stats.phase_statistics_complete else "partial"
            ),
            "byte_accounting": "net-regular-file-bytes-v1",
            "oracle_time_accounting": "sum-run-result-duration-v1",
            "phases": [
                {
                    "phase": phase.phase,
                    "passes": phase.passes,
                    "completed_passes": phase.completed_passes,
                    "aborted_passes": phase.aborted_passes,
                    "wall_seconds": round(phase.wall_seconds, 4),
                    "bytes_removed": phase.bytes_removed,
                    "bytes_added": phase.bytes_added,
                    "attempts": phase.attempts,
                    "no_op": phase.no_op,
                    "rejected": phase.rejected,
                    "accepted": phase.accepted,
                    "superseded": phase.superseded,
                    "aborted": phase.aborted,
                    "oracle_sample_uses": phase.oracle_sample_uses,
                    "oracle_samples": phase.oracle_samples,
                    "oracle_passing_sample_uses": (
                        phase.oracle_passing_sample_uses
                    ),
                    "oracle_seconds": round(phase.oracle_seconds, 4),
                    "cache_hits": phase.cache_hits,
                    "samples_saved": phase.samples_saved,
                }
                for phase in stats.phase_stats.values()
            ],
        },
        "holdout_certification": {
            "schema_version": 1,
            "status": holdout.status,
            "policy": holdout.policy,
            "attempt_id": holdout.attempt_id,
            "planned_runs": holdout.planned_runs,
            "completed_runs": holdout.completed_runs,
            "passes": holdout.passes,
            "ordinary_failures": sum(
                sample.outcome == "failed" for sample in holdout.samples
            ),
            "minimum_rate": holdout.minimum_rate,
            "confidence": holdout.confidence,
            "alpha": (
                None if holdout.confidence is None else 1.0 - holdout.confidence
            ),
            "required_passes": holdout.required_passes,
            "observed_rate": holdout.observed_rate,
            "exact_lower_bound": holdout.exact_lower_bound,
            "exact_p_value": holdout.exact_p_value,
            "exact_rate_gate_passed": holdout.exact_rate_gate_passed,
            "timed_out_runs": holdout.timed_out_runs,
            "resource_exhausted_runs": holdout.resource_exhausted_runs,
            "interrupted_runs": holdout.interrupted_runs,
            "artifact_fingerprint": holdout.artifact_fingerprint,
            "artifact_fingerprint_policy": TREE_FINGERPRINT_POLICY,
            "artifact_scope": "exported-payload-tree-v1",
            "oracle_identity_sha256": holdout.oracle_identity_sha256,
            "fresh_repository_copy_per_run": (
                holdout.fresh_repository_copy_per_run
            ),
            "cache_used": holdout.cache_used,
            "early_stopping": holdout.early_stopping,
            "resumed": holdout.resumed,
            "iid_assumption": "required-not-verified",
            "samples": [
                {
                    "index": sample.index,
                    "outcome": sample.outcome,
                    "accepted": sample.accepted,
                    "returncode": sample.returncode,
                    "duration_seconds": (
                        None
                        if sample.duration_seconds is None
                        else round(sample.duration_seconds, 4)
                    ),
                    "timed_out": sample.timed_out,
                    "resource_exhausted": sample.resource_exhausted,
                    "resource_reason": sample.resource_reason,
                    "output_sha256": sample.output_sha256,
                }
                for sample in holdout.samples
            ],
        },
        "events": [
            {
                "phase": event.phase,
                "description": event.description,
                "duration_seconds": round(event.duration_seconds, 4),
                "oracle_runs": event.oracle_runs,
                "oracle_passes": event.oracle_passes,
                "oracle_rate": event.oracle_rate,
                "oracle_lower_bound": event.oracle_lower_bound,
                "oracle_anytime_lower_bound": event.oracle_anytime_lower_bound,
                "oracle_early_acceptance": event.oracle_early_acceptance,
                "candidate_family_index": event.candidate_family_index,
                "candidate_confidence": event.candidate_confidence,
                "candidate_alpha": event.candidate_alpha,
            }
            for event in stats.events
        ],
    }
    execution = report["execution"]
    assert isinstance(execution, dict)
    if stats.container_image is not None:
        execution["image"] = stats.container_image
    if stats.container_image_id is not None:
        execution["image_id"] = stats.container_image_id
    if stats.session_path is not None:
        execution["session_path"] = stats.session_path
    if stats.container_network is not None:
        execution["network"] = stats.container_network
    limits = {}
    if stats.container_cpus is not None:
        limits["cpus"] = stats.container_cpus
    if stats.container_memory_bytes is not None:
        limits["memory_bytes"] = stats.container_memory_bytes
    if stats.container_pids_limit is not None:
        limits["pids"] = stats.container_pids_limit
    if stats.container_tmpfs_bytes is not None:
        limits["tmpfs_bytes"] = stats.container_tmpfs_bytes
    if stats.container_workspace_limit_bytes is not None:
        limits["workspace_bytes"] = stats.container_workspace_limit_bytes
    if limits:
        execution["limits"] = limits
    if result.java_exception_signature is not None:
        signature = result.java_exception_signature
        report["java_exception_signature"] = {
            "class": signature.class_name,
            "message": signature.message,
            "frames": list(signature.frames),
        }
    if result.python_exception_signature is not None:
        signature = result.python_exception_signature
        report["python_exception_signature"] = {
            "class": signature.class_name,
            "message": signature.message,
            "frames": list(signature.frames),
        }
    if result.process_failure_signature is not None:
        signature = result.process_failure_signature
        process_signature: Dict[str, object] = {
            "kind": signature.kind,
            "code": signature.code,
        }
        name = process_failure_name(signature)
        if name is not None:
            process_signature["name"] = name
        report["process_failure_signature"] = process_signature
    return report


def _reproduction_markdown(
    result: ReductionResult,
    command: str,
    match: Optional[str],
) -> str:
    match_markdown = ""
    if match is not None:
        match_markdown = "Expected output match: `%s`\n\n" % match.replace(
            "`", "\\`"
        )
    return (
        "# Minimal reproduction\n\n"
        "This repository was reduced by ReproMin while preserving the configured "
        "failure signature.\n\n"
        "## Reproduce\n\n"
        "```sh\n%s\n```\n\n" % command
        + match_markdown
        + _java_signature_markdown(result)
        + _python_signature_markdown(result)
        + _process_signature_markdown(result)
        + _holdout_markdown(result)
        + "See `report.json` in this metadata directory for reduction statistics.\n"
    )


def _java_signature_markdown(result: ReductionResult) -> str:
    signature = result.java_exception_signature
    if signature is None:
        return ""
    location = signature.frames[0] if signature.frames else "<no frame>"
    return (
        "Expected Java exception: `%s: %s` at `%s`\n\n"
        % (
            signature.class_name.replace("`", "\\`"),
            signature.message.replace("`", "\\`"),
            location.replace("`", "\\`"),
        )
    )


def _python_signature_markdown(result: ReductionResult) -> str:
    signature = result.python_exception_signature
    if signature is None:
        return ""
    location = signature.frames[0] if signature.frames else "<no frame>"
    return (
        "Expected Python exception: `%s: %s` at `%s`\n\n"
        % (
            signature.class_name.replace("`", "\\`"),
            signature.message.replace("`", "\\`"),
            location.replace("`", "\\`"),
        )
    )


def _process_signature_markdown(result: ReductionResult) -> str:
    signature = result.process_failure_signature
    if signature is None:
        return ""
    name = process_failure_name(signature)
    if signature.kind == "posix_signal":
        detail = "POSIX signal `%s` (`%d`)" % (name or "unknown", signature.code)
    elif signature.kind == "windows_status":
        detail = "Windows status `0x%08X`" % signature.code
        if name is not None:
            detail += " (`%s`)" % name
    else:
        detail = "exit code `%d`" % signature.code
    return "Expected process failure: %s\n\n" % detail


def _holdout_markdown(result: ReductionResult) -> str:
    certification = result.holdout_certification
    if certification.status != "certified":
        return ""
    return (
        "Holdout certification: `%d/%d` fresh samples passed; the %.1f%% "
        "one-sided exact lower bound is `%.4f`.\n\n"
        % (
            certification.passes,
            certification.planned_runs,
            100.0 * (certification.confidence or 0.0),
            certification.exact_lower_bound or 0.0,
        )
    )
