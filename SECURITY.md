# Security

The default host backend is not a sandbox. The `--command` value is executed by
the host shell inside a copied working directory, but the process retains the
current user's filesystem, environment, credentials, and network access.

Only run ReproMin on repositories and reproduction commands you trust. Review
scripts, Maven plugins, test fixtures, and build configuration before running
them. Use a disposable container or virtual machine for untrusted reports.

`repomin report replay` executes the unsigned command embedded in a report.
`--yes` acknowledges that execution but does not make the report trusted.
Replay uses disposable payload copies and omits raw command output and
environment values from its evidence, but a host command still retains the
invoking user's access. Review both the report and payload first; use a
disposable virtual machine for artifacts from an untrusted source.

The Docker backend narrows access with these defaults:

- no container network;
- no host environment or credentials forwarded by ReproMin;
- read-only container root plus a bounded `/tmp` filesystem;
- a default 512-process limit;
- all Linux capabilities dropped and privilege escalation disabled;
- a numeric host user and group instead of container root;
- only the disposable candidate repository mounted writable;
- no automatic image pulls.

This is defense in depth, not a guarantee against hostile code. The container
shares the host kernel and Docker daemon trust boundary, the project mount is
writable, and CPU, memory, and workspace limits are optional. The workspace
guard samples logical file size and is not a hard filesystem quota. Configure
`--docker-cpus`, `--docker-memory`, and `--docker-workspace-limit` for untrusted
builds, and use a dedicated virtual machine for repositories that may be
malicious. Treat the container image itself as trusted input.

`--docker-network bridge` permits outbound and container-network access.
`--docker-network host` removes network isolation and may expose host services.

Java source analysis always runs in the host JDK, including when reproduction
commands use `--backend docker`. Every `--java-classpath PATH` is therefore a
host-side input outside the container boundary: ReproMin recursively reads and
hashes directory entries and asks the host compiler to parse class metadata
from the supplied files. Annotation processing is disabled, and these entries
do not add or alter Docker mounts and are not added to the reproduction command.
An entry already below `SOURCE` can still appear in the container through the
ordinary candidate-repository mount. Classpath inputs should come from a trusted
source. Keep the host JDK patched, avoid unnecessarily broad classpath
directories, and do not assume Docker isolates the analyzer from malformed
dependency archives.

The default `--jobs 1` runs one reproduction command at a time. Higher values
use separate working directories or containers. Host jobs still share ports,
credentials, databases, and services. Docker jobs share any external services
made reachable by the selected network policy. Enable parallelism only when
concurrent executions cannot interfere with each other.

ReproMin starts POSIX commands behind a registration gate in a new process
group and starts Windows commands suspended before assigning them to a Job
Object. Timeout, resource failure, interruption, and parallel-worker failure
cancel all registered commands; ordinary background children in the managed
tree are also stopped when the command returns. Combined stdout and stderr are
bounded at 64 MiB and an overflow is rejected as resource exhaustion.

POSIX process groups are not a sandbox. Code can deliberately create a new
session or process group and escape host-backend cleanup. The bounded output
capture prevents such a process from blocking ReproMin indefinitely, but it may
continue changing host state. Run daemonizing or untrusted commands in a
disposable virtual machine; Docker is useful defense in depth but still shares
the host kernel and daemon trust boundary.

ReproMin does not intentionally include command output in its report. The
report does include the command string, optional failure regular expression,
learned process-termination signature when enabled, paths of accepted removals,
and aggregate file sizes. Review these fields before sharing a reduced
repository.

Once the project is hosted publicly, suspected vulnerabilities should be
reported through the repository's private security advisory feature rather
than a public issue.
