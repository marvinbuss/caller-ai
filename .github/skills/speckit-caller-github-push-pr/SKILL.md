---
name: speckit-caller-github-push-pr
description: Push the current feature branch and open a pull request with gh after
  explicit confirmation.
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: caller-ai
  source: extension:caller-github
---

# Caller Github Push Pr Skill

## User Input

```text
$ARGUMENTS
```

## Steps

1. Run `.specify/extensions/caller-github/scripts/python/push_pr.py --check`. It verifies `git`, `gh` and `gh auth status`. If anything is missing it prints `SKIP: <reason>` and exits 0: tell the user the reason, make no remote change and continue the workflow.
2. Show the branch, the target branch (`main`) and the proposed pull request title (the feature name) to the user and ask for explicit confirmation. Do not continue without a clear "yes".
3. Only after confirmation run `.specify/extensions/caller-github/scripts/python/push_pr.py --confirm --title "<title>"`. It pushes the branch with a plain `git push --set-upstream origin <branch>` (never force) and runs `gh pr create`.
4. Never store, print or ask for credentials; authentication is whatever `gh auth login` already holds.
