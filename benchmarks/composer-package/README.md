# Composer manifest benchmark

This network-free fixture exercises the strict `composer.json` adapter. The
offline Python oracle keeps one required package and the autoload map while
allowing unrelated requirements, scripts, repository entries, and replacement
metadata to disappear. It does not require PHP or Composer to be installed.

Run from the repository root:

```sh
out_parent="$(mktemp -d /tmp/repomin-composer-package.XXXXXX)"
PYTHONPATH=src python3 -m repomin benchmarks/composer-package \
  --command 'python3 reproduce.py' \
  --match 'ORIGINAL_FAILURE' \
  --adapter composer \
  --source-reducer none \
  --output "$out_parent/result"
```

The exported manifest must retain `repomin/required` and `autoload.psr-4`, while
unrelated entries are removed and the independent command still exits `1` with
`ORIGINAL_FAILURE`.
