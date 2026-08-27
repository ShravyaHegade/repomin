# Real failure pilot

Use this guide to share a sanitized ReproMin run from a real CI or dependency
failure. The goal is to learn which defaults, adapters, and report fields help
in practice; a complete private repository is not required.

## Before sharing

- Remove credentials, tokens, private URLs, customer data, and proprietary
  source from the example.
- Replace package names, paths, and service names when they are confidential.
- Check that the reproduction command does not publish environment values.
- Prefer a public fixture or a small synthetic copy when the original project
  cannot be shared.
- Read the [security policy](../SECURITY.md). The host backend runs commands
  directly, and Docker is not a complete security boundary.

## Capture a run

Run ReproMin against the sanitized checkout and use the failure signal that is
stable for the project:

```sh
repomin /path/to/sanitized-project \
  --command './run-failing-test.sh' \
  --match 'STABLE_FAILURE_MARKER' \
  --adapter auto \
  --output /tmp/repomin-pilot-result
```

When output text is unstable, use an exact exit code instead:

```sh
repomin /path/to/sanitized-project \
  --command './run-failing-test.sh' \
  --exit-code 1 \
  --adapter auto \
  --output /tmp/repomin-pilot-result
```

Validate the payload and its sidecar before inspecting or sharing them:

```sh
repomin report validate \
  /tmp/repomin-pilot-result.repomin/report.json \
  --payload /tmp/repomin-pilot-result \
  --json
```

The sidecar contains `report.json` and a human-readable `REPOMIN.md`. Review
both files and the minimized payload for secrets before posting anything.

## Share the result

Open [the real CI pilot issue](https://github.com/fly1d/repomin/issues/11) and
include only this summary:

```text
Language and build/test system:
ReproMin version:
Backend and adapter:
Source/text reducer options:
Failure signal shape (redacted):
Payload retained:
Payload removed:
Approximate duration:
What was useful or confusing:
Known limitations:
```

Do not attach the original checkout or unredacted logs. A maintainer may ask
for a public fixture only after the workflow and licensing are clear. Repeatable
workflows can become a benchmark, an examples entry, or a documented
compatibility boundary.
