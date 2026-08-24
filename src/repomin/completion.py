"""Shell completion scripts for the public ``repomin`` command."""

from __future__ import annotations

from typing import Final


SUPPORTED_SHELLS: Final = ("bash", "zsh", "fish")


_BASH = r'''# Bash completion for repomin.
_repomin() {
    local cur prev options value_options
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    options="--version --help --command --match --exit-code --output --session --resume --timeout --backend --docker-image --docker-network --docker-cpus --docker-memory --docker-pids-limit --docker-tmpfs-size --docker-workspace-limit --jobs --no-cache --max-attempts --max-duration --ignore --ignore-path --gitignore --gitignore-file --gitignore-recursive --keep --env --java-exception --python-exception --process-failure --baseline-runs --min-baseline-passes --candidate-runs --min-candidate-passes --min-baseline-rate --min-candidate-rate --confidence --run-confidence --holdout-runs --min-holdout-rate --holdout-confidence --adapter --source-reducer --text-file --semantic-reducer --semantic-endpoint --semantic-model --semantic-timeout --java-classpath --verbose"
    value_options="--command --match --exit-code --output --session --timeout --backend --docker-image --docker-network --docker-cpus --docker-memory --docker-pids-limit --docker-tmpfs-size --docker-workspace-limit --jobs --max-attempts --max-duration --ignore --ignore-path --gitignore-file --keep --env --baseline-runs --min-baseline-passes --candidate-runs --min-candidate-passes --min-baseline-rate --min-candidate-rate --confidence --run-confidence --holdout-runs --min-holdout-rate --holdout-confidence --adapter --source-reducer --text-file --semantic-reducer --semantic-endpoint --semantic-model --semantic-timeout --java-classpath"
    case "$prev" in
        --backend) COMPREPLY=( $(compgen -W "host docker" -- "$cur") ); return 0 ;;
        --docker-network) COMPREPLY=( $(compgen -W "none bridge host" -- "$cur") ); return 0 ;;
        --adapter) COMPREPLY=( $(compgen -W "auto none maven gradle python pipenv node composer dotnet ruby cargo go" -- "$cur") ); return 0 ;;
        --source-reducer) COMPREPLY=( $(compgen -W "auto none java python" -- "$cur") ); return 0 ;;
        --semantic-reducer) COMPREPLY=( $(compgen -W "none http" -- "$cur") ); return 0 ;;
    esac
    if [[ " $value_options " == *" $prev "* ]]; then
        COMPREPLY=( $(compgen -f -- "$cur") )
        return 0
    fi
    if [[ "$cur" == -* ]]; then
        COMPREPLY=( $(compgen -W "$options" -- "$cur") )
    else
        COMPREPLY=( $(compgen -f -- "$cur") )
    fi
}
complete -F _repomin repomin
'''


_ZSH = r'''#compdef repomin

_repomin() {
    local -a options
    options=(
        '--version[show the installed version]'
        '--help[show command help]'
        '--command[failure reproduction command]:command:'
        '--match[regular expression that must remain present]:pattern:'
        '--exit-code[required exit code]:code:(0 1 2 7 9)'
        '--output[output directory]:directory:_files -/'
        '--session[persistent session directory]:directory:_files -/'
        '--resume[resume an existing session]'
        '--timeout[seconds per run]:seconds:'
        '--backend[execution backend]:backend:(host docker)'
        '--docker-image[Docker image]:image:'
        '--docker-network[Docker network policy]:network:(none bridge host)'
        '--docker-cpus[Docker CPU quota]:cores:'
        '--docker-memory[Docker memory limit]:size:'
        '--docker-pids-limit[maximum container processes]:count:'
        '--docker-tmpfs-size[container /tmp size]:size:'
        '--docker-workspace-limit[writable workspace limit]:size:'
        '--jobs[concurrent candidate commands]:count:'
        '--no-cache[disable result caching]'
        '--max-attempts[logical candidate attempt budget]:count:'
        '--max-duration[wall-clock budget]:seconds:'
        '--ignore[ignored basename]:name:'
        '--ignore-path[ignored repository-relative path]:path:_files'
        '--gitignore[apply repository .gitignore]'
        '--gitignore-file[apply a gitignore-style file]:file:_files'
        '--gitignore-recursive[apply nested .gitignore files]'
        '--keep[protect a repository-relative path]:path:_files'
        '--env[reproduction environment variable]:NAME=VALUE:'
        '--java-exception[preserve a normalized Java exception]'
        '--python-exception[preserve a normalized Python exception]'
        '--process-failure[preserve process termination]'
        '--baseline-runs[baseline samples]:count:'
        '--min-baseline-passes[minimum baseline passes]:count:'
        '--candidate-runs[candidate samples]:count:'
        '--min-candidate-passes[minimum candidate passes]:count:'
        '--min-baseline-rate[minimum baseline rate]:rate:'
        '--min-candidate-rate[minimum candidate rate]:rate:'
        '--confidence[confidence level]:level:'
        '--run-confidence[run-wide confidence]:level:'
        '--holdout-runs[holdout samples]:count:'
        '--min-holdout-rate[minimum holdout rate]:rate:'
        '--holdout-confidence[holdout confidence]:level:'
        '--adapter[structured manifest reducer]:adapter:(auto none maven gradle python pipenv node composer dotnet ruby cargo go)'
        '--source-reducer[source-level reducer]:reducer:(auto none java python)'
        '--text-file[line-reduce a UTF-8 text file]:path:_files'
        '--semantic-reducer[semantic reducer backend]:backend:(none http)'
        '--semantic-endpoint[OpenAI-compatible endpoint]:url:'
        '--semantic-model[semantic model name]:name:'
        '--semantic-timeout[semantic HTTP timeout]:seconds:'
        '--java-classpath[Java analysis classpath]:path:_files'
        '--verbose[print reduction progress]'
    )
    _arguments -s $options '*:repository or completion command:_files'
}

_repomin "$@"
'''


_FISH = r'''# Fish completion for repomin.
complete -c repomin -f -n '__fish_use_subcommand' -a completion -d 'print shell completion script'
complete -c repomin -f -n '__fish_seen_subcommand_from completion' -a 'bash zsh fish' -d 'shell'

set -l boolean_options version help resume no-cache gitignore gitignore-recursive java-exception python-exception process-failure verbose
for option in $boolean_options
    complete -c repomin -f -l $option
end

complete -c repomin -f -l command -r -d 'failure reproduction command'
complete -c repomin -f -l match -r -d 'failure output pattern'
complete -c repomin -f -l exit-code -r -d 'required exit code'
complete -c repomin -f -l output -r -a '(__fish_complete_directories)'
complete -c repomin -f -l session -r -a '(__fish_complete_directories)'
complete -c repomin -f -l timeout -r
complete -c repomin -f -l backend -r -a 'host docker'
complete -c repomin -f -l docker-image -r
complete -c repomin -f -l docker-network -r -a 'none bridge host'
complete -c repomin -f -l docker-cpus -r
complete -c repomin -f -l docker-memory -r
complete -c repomin -f -l docker-pids-limit -r
complete -c repomin -f -l docker-tmpfs-size -r
complete -c repomin -f -l docker-workspace-limit -r
complete -c repomin -f -l jobs -r
complete -c repomin -f -l max-attempts -r
complete -c repomin -f -l max-duration -r
complete -c repomin -f -l ignore -r
complete -c repomin -f -l ignore-path -r -a '(__fish_complete_path)'
complete -c repomin -f -l gitignore-file -r -a '(__fish_complete_path)'
complete -c repomin -f -l keep -r -a '(__fish_complete_path)'
complete -c repomin -f -l env -r
complete -c repomin -f -l baseline-runs -r
complete -c repomin -f -l min-baseline-passes -r
complete -c repomin -f -l candidate-runs -r
complete -c repomin -f -l min-candidate-passes -r
complete -c repomin -f -l min-baseline-rate -r
complete -c repomin -f -l min-candidate-rate -r
complete -c repomin -f -l confidence -r
complete -c repomin -f -l run-confidence -r
complete -c repomin -f -l holdout-runs -r
complete -c repomin -f -l min-holdout-rate -r
complete -c repomin -f -l holdout-confidence -r
complete -c repomin -f -l adapter -r -a 'auto none maven gradle python pipenv node composer dotnet ruby cargo go'
complete -c repomin -f -l source-reducer -r -a 'auto none java python'
complete -c repomin -f -l text-file -r -a '(__fish_complete_path)'
complete -c repomin -f -l semantic-reducer -r -a 'none http'
complete -c repomin -f -l semantic-endpoint -r
complete -c repomin -f -l semantic-model -r
complete -c repomin -f -l semantic-timeout -r
complete -c repomin -f -l java-classpath -r -a '(__fish_complete_path)'
'''


def completion_script(shell: str) -> str:
    """Return the completion script for one supported shell."""
    scripts = {"bash": _BASH, "zsh": _ZSH, "fish": _FISH}
    try:
        return scripts[shell]
    except KeyError as exc:
        raise ValueError(
            "unsupported shell %r (choose one of: %s)"
            % (shell, ", ".join(SUPPORTED_SHELLS))
        ) from exc
