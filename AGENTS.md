# Finlyst checkpoint workflow

The user authorizes automatic Git commits and pushes after each completed, verified checkpoint, without asking for confirmation again.

- Finish the authorized slice, run appropriate checks, and update project documentation before committing.
- Review the files being staged. Commit project code, synthetic tests and relevant documentation; exclude private source files, generated financial outputs, passwords, API keys and other secrets. Preserve the input/, output/ and environment-file ignore rules.
- Push checkpoint commits to the configured remote and current branch. Never force-push or discard unrelated work to resolve a push failure. Report any authentication, permission or remote-divergence blocker.
- Report the checkpoint commit and push result. Do not claim a push succeeded unless Git confirms it.
- This standing authorization does not expand implementation scope beyond the user's current slice.
