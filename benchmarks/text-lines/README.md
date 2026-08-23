# Text line reducer benchmark

This network-free fixture exercises the opt-in `--text-file` line reducer. The
oracle requires `NEEDLE` in `data.txt` while every surrounding line is noise.

Run from the repository root:

```sh
out_parent="$(mktemp -d /tmp/repomin-text-lines.XXXXXX)"
PYTHONPATH=src python3 -m repomin benchmarks/text-lines \
  --command 'python3 reproduce.py' \
  --match 'ORIGINAL_FAILURE' \
  --source-reducer none \
  --adapter none \
  --text-file data.txt \
  --output "$out_parent/result"
```

The exported `data.txt` must contain only `NEEDLE`, and the independent command
must still exit `1` with `ORIGINAL_FAILURE`.
