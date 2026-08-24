# ReproMin 中文快速开始

ReproMin 会在每次候选修改后重新执行失败命令，只保留仍能复现原始失败的修改。它适合把一个过大的失败仓库缩减成便于提交 issue 或制作回归测试的最小复现目录。

## 安装

ReproMin 需要 Python 3.9 或更高版本，目前从 GitHub Release 安装，不依赖 PyPI：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install \
  https://github.com/fly1d/repomin/releases/download/v0.1.0.dev1/repomin-0.1.0.dev1-py3-none-any.whl
```

如果你正在开发 ReproMin，也可以在仓库根目录运行 `python3 -m pip install -e .`。

## 最小示例

下面的命令从一个干净的临时目录创建失败复现，然后只缩减 `input.txt`。整段示例可直接在 Bash 或 Zsh 中运行：

```sh
demo_dir="$(mktemp -d)"
cd "$demo_dir"

mkdir case
cat > case/reproduce.py <<'PY'
from pathlib import Path

text = Path("input.txt").read_text(encoding="utf-8")
if "keep-me" not in text:
    print("DIFFERENT_FAILURE")
    raise SystemExit(2)
print("ORIGINAL_FAILURE")
raise SystemExit(1)
PY

printf 'keep-me\nremove-me\n' > case/input.txt

repomin case \
  --command 'python3 reproduce.py' \
  --match 'ORIGINAL_FAILURE' \
  --adapter none \
  --source-reducer none \
  --text-file input.txt \
  --output "$demo_dir/reduced"
```

完成后，`reduced/input.txt` 仍包含 `keep-me`，无关的 `remove-me` 可以被删除。缩减后的仓库在 `reduced/`，证据报告在旁边的 `reduced.repomin/`：

- `report.json`：机器可读的运行统计、oracle 规则和环境信息；
- `REPOMIN.md`：面向人的缩减摘要。

可以用下面的命令查看结果：

```sh
grep -n 'keep-me' "$demo_dir/reduced/input.txt"
cat "$demo_dir/reduced.repomin/REPOMIN.md"
```

## Oracle 是什么

`--command` 是失败复现命令，`--match` 是必须继续出现在 stdout 或 stderr 中的正则表达式。上面的示例要求命令继续输出 `ORIGINAL_FAILURE` 并以非零状态退出。

匹配成功只说明“在记录的环境和抽样规则下，失败现象仍被复现”。它不是代码正确性证明，也不能证明这个正则表达式一定识别了唯一的根因。对于不同类型的失败，可以使用 `--exit-code`、`--process-failure` 或 Java/Python 异常签名选项。

## 安全边界

默认的 `host` backend 会直接在当前主机执行 `--command`，不是沙箱。只对你信任的复现命令使用默认设置；不要把包含恶意脚本或不可信依赖的仓库交给 host backend。Docker backend 可以减少访问范围，但也不是完整的安全边界，仍需由使用者配置镜像和资源限制。

## 下一步

- 查看 [英文 README](../README.md) 了解所有 CLI 参数和高级 reducer；
- 查看 [示例目录](EXAMPLES.md) 了解 Maven、Python、Node、MSBuild 等项目；
- 查看 [架构说明](ARCHITECTURE.md) 了解 oracle、checkpoint 和 reducer 的边界；
- 贡献代码前阅读 [贡献指南](../CONTRIBUTING.md)。
