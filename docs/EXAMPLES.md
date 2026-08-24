# Examples

These examples are self-contained and use only Python. Run them from a scratch
directory after installing ReproMin in editable mode.

## Shrink a Python failure to its required files

Create a small failing project:

```sh
mkdir example && cd example
cat > reproduce.py <<'PY'
from pathlib import Path
import sys

if not Path("required.txt").exists():
    print("DIFFERENT_FAILURE", file=sys.stderr)
    raise SystemExit(2)
print("ORIGINAL_FAILURE", file=sys.stderr)
raise SystemExit(1)
PY
echo keep-me > required.txt
echo noise > unused-a.txt
echo more-noise > unused-b.txt
```

Before reduction the tree is:

```text
reproduce.py
required.txt
unused-a.txt
unused-b.txt
```

Run:

```sh
repomin . \
  --command 'python3 reproduce.py' \
  --match 'ORIGINAL_FAILURE' \
  --source-reducer none \
  --adapter none \
  --output ../example-minimal
```

The reduced tree keeps only the command entry point and the file the oracle
actually needs:

```text
reproduce.py
required.txt
```

The sibling `../example-minimal.repomin/report.json` records the attempts,
accepted mutations, and phase accounting.

## Shrink a Pipenv `Pipfile`

For a network-free reproduction that only needs one package declaration, run
the dedicated Pipenv adapter:

```sh
repomin . \
  --command 'python3 reproduce.py' \
  --match 'ORIGINAL_FAILURE' \
  --adapter pipenv \
  --source-reducer none \
  --output ../pipenv-minimal
```

Only direct entries in `[packages]`, `[dev-packages]`, and `[requires]` are
eligible. Pipenv source settings and `Pipfile.lock` are preserved.

## Shrink a data file's contents with `--text-file`

Add a data file whose oracle only needs one line:

```sh
cat > read_data.py <<'PY'
from pathlib import Path
import sys

if "NEEDLE" not in Path("data.txt").read_text():
    print("DIFFERENT_FAILURE", file=sys.stderr)
    raise SystemExit(2)
print("ORIGINAL_FAILURE", file=sys.stderr)
raise SystemExit(1)
PY
printf 'alpha\nbeta\nNEEDLE\ngamma\ndelta\n' > data.txt
```

Run with the text reducer:

```sh
repomin . \
  --command 'python3 read_data.py' \
  --match 'ORIGINAL_FAILURE' \
  --source-reducer none \
  --adapter none \
  --text-file data.txt \
  --output ../data-minimal
```

`data.txt` reduces to exactly `NEEDLE`, while the command still fails the same
way.

## Keep an unrelated file that the oracle does not need

Use `--keep` to preserve a file such as a license even though deleting it would
not change the failure:

```sh
repomin . \
  --command 'python3 reproduce.py' \
  --match 'ORIGINAL_FAILURE' \
  --keep LICENSE \
  --output ../example-kept
```
