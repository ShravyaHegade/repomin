from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

from repomin.model import ReductionResult, TREE_FINGERPRINT_POLICY
from repomin.session import _tree_digest
from repomin.signature import process_failure_name


REPORT_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ReportValidationError(ValueError):
    """The report is not a structurally valid ReproMin schema document."""


def validate_report_document(report: object) -> Dict[str, object]:
    """Validate the stable, machine-readable invariants of one report."""
    if not isinstance(report, dict):
        raise ReportValidationError("report root must be a JSON object")
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ReportValidationError(
            "unsupported report schema_version: %r" % report.get("schema_version")
        )
    _require_text(report, "command", non_empty=True)
    _require_optional_text(report, "failure_match")
    _require_int(report, "baseline_exit_code")
    _require_int(report, "final_exit_code")
    for name in ("source", "output"):
        section = _require_object(report, name)
        _require_nonnegative_int(section, "files", name)
        _require_nonnegative_int(section, "bytes", name)
    for name in ("attempts", "accepted_mutations", "cache_hits"):
        _require_nonnegative_int(report, name)

    execution = _require_object(report, "execution")
    backend = _require_text(execution, "backend", non_empty=True)
    if backend not in {"host", "docker"}:
        raise ReportValidationError("execution.backend must be host or docker")
    _require_positive_int(execution, "jobs", "execution")

    phases = _require_object(report, "phase_statistics")
    coverage = phases.get("coverage")
    if coverage not in {"complete", "partial"}:
        raise ReportValidationError(
            "phase_statistics.coverage must be complete or partial"
        )
    phase_items = phases.get("phases")
    if not isinstance(phase_items, list):
        raise ReportValidationError("phase_statistics.phases must be an array")
    if coverage == "complete":
        phase_attempts = 0
        phase_accepted = 0
        for index, phase in enumerate(phase_items):
            if not isinstance(phase, dict):
                raise ReportValidationError("phase %d must be an object" % index)
            attempts = _require_nonnegative_int(phase, "attempts", "phase %d" % index)
            accepted = _require_nonnegative_int(phase, "accepted", "phase %d" % index)
            no_op = _require_nonnegative_int(phase, "no_op", "phase %d" % index)
            rejected = _require_nonnegative_int(phase, "rejected", "phase %d" % index)
            superseded = _require_nonnegative_int(
                phase, "superseded", "phase %d" % index
            )
            aborted = _require_nonnegative_int(phase, "aborted", "phase %d" % index)
            if attempts != no_op + rejected + accepted + superseded + aborted:
                raise ReportValidationError(
                    "phase %d attempts accounting is inconsistent" % index
                )
            sample_uses = _require_nonnegative_int(
                phase, "oracle_sample_uses", "phase %d" % index
            )
            samples = _require_nonnegative_int(
                phase, "oracle_samples", "phase %d" % index
            )
            cache_hits = _require_nonnegative_int(
                phase, "cache_hits", "phase %d" % index
            )
            if sample_uses != samples + cache_hits:
                raise ReportValidationError(
                    "phase %d oracle accounting is inconsistent" % index
                )
            phase_attempts += attempts
            phase_accepted += accepted
        if phase_attempts != report["attempts"]:
            raise ReportValidationError(
                "phase attempts do not equal report attempts"
            )
        if phase_accepted != report["accepted_mutations"]:
            raise ReportValidationError(
                "phase accepted count does not equal report accepted_mutations"
            )

    events = report.get("events")
    if not isinstance(events, list):
        raise ReportValidationError("events must be an array")
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ReportValidationError("event %d must be an object" % index)
        context = "event %d" % index
        _require_text(event, "phase", non_empty=True)
        _require_text(event, "description", non_empty=True)
        _require_nonnegative_number(event, "duration_seconds", context)
        oracle_runs = _require_nonnegative_int(
            event, "oracle_runs", context
        )
        oracle_passes = _require_nonnegative_int(
            event, "oracle_passes", context
        )
        if oracle_passes > oracle_runs:
            raise ReportValidationError("%s oracle passes exceed runs" % context)
        oracle_rate = _require_optional_probability(event, "oracle_rate", context)
        if oracle_rate is not None:
            if oracle_runs == 0 or not math.isclose(
                oracle_rate, float(oracle_passes) / oracle_runs, rel_tol=1e-12
            ):
                raise ReportValidationError(
                    "%s oracle_rate does not match pass/run counts" % context
                )
        for name in ("oracle_lower_bound", "oracle_anytime_lower_bound"):
            _require_optional_probability(event, name, context)
        early_acceptance = event.get("oracle_early_acceptance")
        if "oracle_early_acceptance" in event and not isinstance(
            early_acceptance, bool
        ):
            raise ReportValidationError(
                "%s oracle_early_acceptance must be boolean" % context
            )
        family_index = _require_optional_nonnegative_int(
            event, "candidate_family_index", context
        )
        family_confidence = _require_optional_probability(
            event, "candidate_confidence", context
        )
        family_alpha = _require_optional_probability(event, "candidate_alpha", context)
        if family_index is None:
            if family_confidence is not None or family_alpha is not None:
                raise ReportValidationError(
                    "%s candidate family evidence is incomplete" % context
                )
        elif family_confidence is None or family_alpha is None:
            raise ReportValidationError(
                "%s candidate family evidence is incomplete" % context
            )

    holdout = _require_object(report, "holdout_certification")
    status = _require_text(holdout, "status", non_empty=True)
    allowed_statuses = {
        "not_requested",
        "not_started",
        "certified",
        "not_certified",
        "rejected",
        "interrupted",
        "aborted",
    }
    if status not in allowed_statuses:
        raise ReportValidationError("unknown holdout_certification.status: %s" % status)
    planned = _require_nonnegative_int(
        holdout, "planned_runs", "holdout_certification"
    )
    completed = _require_nonnegative_int(
        holdout, "completed_runs", "holdout_certification"
    )
    passes = _require_nonnegative_int(holdout, "passes", "holdout_certification")
    if completed > planned or passes > completed:
        raise ReportValidationError("holdout run counts are inconsistent")
    samples = holdout.get("samples")
    if not isinstance(samples, list) or len(samples) != completed:
        raise ReportValidationError(
            "holdout_certification.samples must match completed_runs"
        )
    sample_passes = 0
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise ReportValidationError(
                "holdout sample %d must be an object" % index
            )
        sample_index = _require_nonnegative_int(
            sample, "index", "holdout sample %d" % index
        )
        if sample_index != index:
            raise ReportValidationError("holdout sample indexes must be contiguous")
        accepted = sample.get("accepted")
        if not isinstance(accepted, bool):
            raise ReportValidationError(
                "holdout sample %d accepted must be boolean" % index
            )
        if accepted:
            sample_passes += 1
    if sample_passes != passes:
        raise ReportValidationError("holdout passes do not match samples")
    fingerprint = holdout.get("artifact_fingerprint")
    if fingerprint is not None and (
        not isinstance(fingerprint, str) or _SHA256.fullmatch(fingerprint) is None
    ):
        raise ReportValidationError("holdout artifact_fingerprint must be SHA-256")
    if status == "certified" and fingerprint is None:
        raise ReportValidationError(
            "certified holdout must include artifact_fingerprint"
        )
    return report


def validate_report_file(
    report_path: Path,
    payload: Optional[Path] = None,
) -> Dict[str, object]:
    """Validate a report file and, when supplied, its exported payload tree."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReportValidationError(
            "report could not be read: %s" % report_path
        ) from exc
    validate_report_document(report)
    assert isinstance(report, dict)
    holdout = report["holdout_certification"]
    assert isinstance(holdout, dict)
    expected = holdout.get("artifact_fingerprint")
    if payload is not None and expected is not None:
        if not payload.is_dir():
            raise ReportValidationError("payload is not a directory: %s" % payload)
        actual = _tree_digest(payload, set())
        if actual != expected:
            raise ReportValidationError(
                "payload fingerprint differs from report: %s" % payload
            )
    return report


def _require_object(parent: Dict[str, object], name: str) -> Dict[str, object]:
    value = parent.get(name)
    if not isinstance(value, dict):
        raise ReportValidationError("%s must be an object" % name)
    return value


def _require_text(
    parent: Dict[str, object], name: str, *, non_empty: bool = False
) -> str:
    value = parent.get(name)
    if not isinstance(value, str) or (non_empty and not value):
        expected = "non-empty text" if non_empty else "text"
        raise ReportValidationError("%s must be %s" % (name, expected))
    return value


def _require_optional_text(parent: Dict[str, object], name: str) -> None:
    value = parent.get(name)
    if value is not None and not isinstance(value, str):
        raise ReportValidationError("%s must be text or null" % name)


def _require_int(parent: Dict[str, object], name: str) -> int:
    value = parent.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReportValidationError("%s must be an integer" % name)
    return value


def _require_nonnegative_int(
    parent: Dict[str, object], name: str, context: str = ""
) -> int:
    value = _require_int(parent, name)
    if value < 0:
        prefix = (context + ".") if context else ""
        raise ReportValidationError(
            "%s%s must be non-negative" % (prefix, name)
        )
    return value


def _require_optional_nonnegative_int(
    parent: Dict[str, object], name: str, context: str = ""
) -> Optional[int]:
    if name not in parent or parent[name] is None:
        return None
    return _require_nonnegative_int(parent, name, context)


def _require_positive_int(
    parent: Dict[str, object], name: str, context: str = ""
) -> int:
    value = _require_int(parent, name)
    if value <= 0:
        prefix = (context + ".") if context else ""
        raise ReportValidationError("%s%s must be positive" % (prefix, name))
    return value


def _require_nonnegative_number(
    parent: Dict[str, object], name: str, context: str = ""
) -> float:
    value = parent.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        prefix = (context + ".") if context else ""
        raise ReportValidationError("%s%s must be a number" % (prefix, name))
    if not math.isfinite(float(value)) or value < 0:
        prefix = (context + ".") if context else ""
        raise ReportValidationError(
            "%s%s must be finite and non-negative" % (prefix, name)
        )
    return float(value)


def _require_optional_probability(
    parent: Dict[str, object], name: str, context: str = ""
) -> Optional[float]:
    if name not in parent or parent[name] is None:
        return None
    value = _require_nonnegative_number(parent, name, context)
    if value > 1.0:
        prefix = (context + ".") if context else ""
        raise ReportValidationError("%s%s must be at most 1" % (prefix, name))
    return value


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
        + _shell_markdown(command)
        + _execution_markdown(result)
        + match_markdown
        + _java_signature_markdown(result)
        + _python_signature_markdown(result)
        + _process_signature_markdown(result)
        + _holdout_markdown(result)
        + "See `report.json` in this metadata directory for reduction statistics.\n"
    )


def _shell_markdown(command: str) -> str:
    """Wrap a shell command without allowing its backticks to close the fence."""
    fence = "```"
    while fence in command:
        fence += "`"
    return "%ssh\n%s\n%s\n\n" % (fence, command, fence)


def _execution_markdown(result: ReductionResult) -> str:
    """Describe the recorded execution boundary without exposing env values."""
    stats = result.stats
    lines = ["## Execution\n", "Backend: `%s`" % stats.backend]
    if stats.backend == "docker":
        if stats.container_image is not None:
            lines.append("Docker image reference: `%s`" % stats.container_image)
        if stats.container_image_id is not None:
            lines.append("Docker image ID: `%s`" % stats.container_image_id)
        if stats.container_network is not None:
            lines.append("Docker network policy: `%s`" % stats.container_network)
    if stats.environment_names:
        names = ", ".join("`%s`" % name for name in stats.environment_names)
        lines.append("Environment variable names: %s (values are not recorded)" % names)
    return "\n".join(lines) + "\n\n"


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
