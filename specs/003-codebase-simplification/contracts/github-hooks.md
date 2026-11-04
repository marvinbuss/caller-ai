# Contract: Spec Kit GitHub Hooks

Hooks are registered in a committed `.specify/extensions.yml` and run only inside the Spec Kit workflow, after user confirmation. No GitHub Actions workflow is added or changed.

## Hook set

| Event | Extension | Purpose | Behavior |
| --- | --- | --- | --- |
| before specify | bundled `git` | create the feature branch | optional, prompted |
| after specify, plan, tasks, implement | bundled `git` | commit the workflow artifacts | optional, prompted |
| after tasks | bundled `github` | convert tasks to GitHub issues | optional, prompted |
| after implement | repository-local `caller-github` | push the branch and open a pull request with `gh` | optional, prompted, explicit confirmation of branch and target |

## Rules

- Every entry sets `optional: true` and a human-readable prompt; no hook executes without confirmation.
- Before acting, each hook checks for `git`, `gh` and authentication. If any is missing the hook reports the reason and skips; the workflow continues and no partial remote change is made.
- The push and pull-request hook shows the branch, target and title before running and never force-pushes.
- Nothing runs on events outside Spec Kit lifecycle events.

## Installed command identifiers

Read from the installed manifests (`specify extension list`):

| Extension | Command | Skill |
| --- | --- | --- |
| `git` (v1.0.1) | `speckit.git.feature`, `speckit.git.commit` (also `validate`, `remote`, `initialize`) | `speckit-git-feature`, `speckit-git-commit` |
| `github` (v1.0.2) | `speckit.github.taskstoissues` | `speckit-github-taskstoissues` |
| `caller-github` (local, v1.0.0) | `speckit.caller-github.push-pr` | `speckit-caller-github-push-pr` |

The bundled `git` extension registers `optional: false` hooks (for example `before_specify`) and many more events by default; the committed `.specify/extensions.yml` replaces them with exactly the set above, all `optional: true`, and sets `auto_execute_hooks: false`.

## Installation and documentation

- Bundled extensions are enabled with the Spec Kit extension command; the local extension lives under `.specify/extensions/` with its manifest.
- Contributor documentation lists each hook, the prompt text, prerequisites and how to disable a hook.
- Exact command identifiers are read from each extension's installed manifest during implementation rather than assumed.

## Verification

- A test loads `.specify/extensions.yml` and asserts every hook is optional and prompted.
- A test asserts graceful skip when `gh` or authentication is unavailable (stubbed commands).
- Documentation checks cover the new contributor page.
