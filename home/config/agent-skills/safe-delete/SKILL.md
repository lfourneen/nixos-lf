---
name: safe-delete
description: Use when deleting files or directories or clearing caches. Verify the deletion scope and move targets to the trash with trash-put. The user empties the trash.
---

# Safe deletion

Stay within the scope authorized for the task.

## Verify the target

Inspect the target and its parent directory. Confirm the path, file type, and contents. For recursive operations, inspect subdirectories and mount points to distinguish user data, reproducible outputs, and other project files.

- For a symlink, determine whether the task concerns the link or its target. Use `realpath` to verify the location and keep the original deletion argument unless the target itself is in scope.
- For variables and batches, check for empty values and inspect the expanded paths. Build an explicit list before operating on each entry.
- Determine scope from the actual contents and task. A directory named `cache`, `dist`, or `node_modules` still needs inspection.

## Move targets to the trash

Use `trash-put` for every deletion, including temporary files, build outputs, and caches. Quote each path and use `--` to end option parsing.

```bash
trash-put -- "$target"
```

Call `trash-put` directly, even in shells where `rm` is aliased to it. Never substitute `rm`, `rm -rf`, or any other permanent deletion method.

If `trash-put` is missing, make the tool available first. If the move fails, keep the target in place and diagnose the failure.

The agent's deletion task ends once the move is verified. The user empties the trash themselves.

## Verification and recovery

Use `trash-list` to check the original path and deletion time. Use `trash-restore` when recovery is needed. The default home trash is `${XDG_DATA_HOME:-$HOME/.local/share}/Trash`, with trashed files under `files` and recovery metadata under `info`. Other mounts may use their own trash directories. Use the records from the tool to identify the correct one.

Verify the target is absent from its original path and report which targets were moved to the trash.

## Permission errors

Pause the failed operation and check file permissions, mount settings, file attributes, and the active client's sandbox. See [Local deletion protections](references/local-protection.md) for this machine's writable paths, restricted directories, and immutable files.

Once the cause is known, continue within the existing authorization and the client's permission mechanism. Clearing immutable flags, changing mounts, or extending sandbox access requires the corresponding authorization. If ownership or deletion scope is unclear, keep the target and report what needs to be established.
