---
name: safe-delete
description: Use when deleting files or directories or clearing caches. Verify the deletion scope, use the trash for recoverable user data, and handle path, recovery, and permission issues.
---

# Safe deletion

Choose a deletion method based on what the data is used for. Stay within the scope authorized for the task.

## Verify the target

Inspect the target and its parent directory. Confirm the path, file type, and contents. For recursive operations, inspect subdirectories and mount points to distinguish user data, reproducible outputs, and other project files.

- For a symlink, determine whether the task concerns the link or its target. Use `realpath` to verify the location and keep the original deletion argument unless the target itself is in scope.
- For variables and batches, check for empty values and inspect the expanded paths. Build an explicit list before operating on each entry.
- Determine scope from the actual contents and task. A directory named `cache`, `dist`, or `node_modules` still needs inspection.

## Deletion methods

Use the trash for user files and data that should remain recoverable.

```bash
trash-put -- "$target"
```

Use `rm -- "$target"` for disposable temporary files, build outputs, or caches confirmed to be in scope. Add a recursive flag only when deleting a directory. Quote paths and use `--` to end option parsing. Inspect the results of a bulk search before passing them to a deletion command.

If `trash-put` is missing, make the tool available first. If moving a file fails, keep it in place and diagnose the failure. Empty the trash only when the task explicitly calls for it.

On this machine, interactive Brush aliases `rm` to `trash-put`. Call `trash-put` explicitly when trashing from scripts. Python deletion APIs and the external `rm` binary retain their usual behavior.

## Verification and recovery

Use `trash-list` to check the original path and deletion time. Use `trash-restore` when recovery is needed. The default home trash is `${XDG_DATA_HOME:-$HOME/.local/share}/Trash`, with trashed files under `files` and recovery metadata under `info`. Other mounts may use their own trash directories. Use the records from the tool to identify the correct one.

After permanent deletion, verify the intended target is gone and that the parent and neighboring paths are consistent with the requested scope. Report whether each target was moved to the trash or permanently deleted.

## Permission errors

Pause the failed operation and check file permissions, mount settings, file attributes, and the active client's sandbox. See [Local deletion protections](references/local-protection.md) for this machine's writable paths, restricted directories, and immutable files.

Once the cause is known, continue within the existing authorization and the client's permission mechanism. Clearing immutable flags, changing mounts, or extending sandbox access requires the corresponding authorization. If ownership or deletion scope is unclear, keep the target and report what needs to be established.
