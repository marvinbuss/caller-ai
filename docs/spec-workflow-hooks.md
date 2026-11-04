# Spec Kit workflow hooks

Spec Kit can offer GitHub steps at fixed points of the specify, plan, tasks and implement workflow.
Every hook is optional and prompted: nothing runs until you confirm, and a hook that cannot run
(no `gh`, not signed in) says why and the workflow continues unchanged. No GitHub Actions workflow
is added or changed by these hooks.

The registry is the committed [`.specify/extensions.yml`](../.specify/extensions.yml).

| Event | Command | Prompt | Prerequisites |
|-------|---------|--------|---------------|
| before specify | `speckit.git.feature` | Create a feature branch before the specification? | `git` |
| after specify | `speckit.git.commit` | Commit the specification? | `git` |
| after plan | `speckit.git.commit` | Commit the plan? | `git` |
| after tasks | `speckit.git.commit` | Commit the tasks? | `git` |
| after tasks | `speckit.github.taskstoissues` | Convert the tasks to GitHub issues? | `git` origin remote and the GitHub MCP server |
| after implement | `speckit.git.commit` | Commit the implementation? | `git` |
| after implement | `speckit.caller-github.push-pr` | Push the branch and open a pull request (you confirm branch, target and title first)? | `git`, `gh`, `gh auth login` |

The push and pull request command first runs a preflight. If `git`, `gh` or the `gh` login is
missing it prints `SKIP: <reason>`, makes no remote change and the workflow continues. Otherwise
it shows the branch, the target (`main`) and the title, asks for explicit confirmation, pushes with
a plain `git push --set-upstream` (never a force push, never from `main`) and runs `gh pr create`.
It never stores or prints credentials.

## Disable a hook

- Set `enabled: false` on its entry in `.specify/extensions.yml`, or delete the entry.
- Disable a whole extension with `specify extension disable <git|github|caller-github>`.
- `auto_execute_hooks` is `false`, so hooks are only offered, never run silently.

The bundled `git` extension registers more hooks (some not optional) when it is installed again;
after `specify extension add` or `update`, restore the registry to the table above.

## Verification

`code/tools/tests/test_spec_hooks.py` parses the registry, checks that every hook is optional with a
prompt and on a Spec Kit event only, and exercises the skip and refusal paths with stubbed
commands. Run with `gh` off the path, the command prints `SKIP: gh is not installed` and exits 0.
