# Technical writing

## Write for a reader and a task

- Give operators the working directory, prerequisites, commands, expected result, and recovery details they need. Give callers the inputs, outputs, defaults, errors, side effects, and compatibility limits that affect use. Give maintainers the rationale for decisions they will need to revisit.
- Keep durable facts at the reader's usual entry point. Maintain API docs near the interface and edit the inputs of generated docs. Link to the authoritative source. Preserve any requested template, format, and language.
- Verify paths, flags, identifiers, units, defaults, and limits against code, configuration, help, or observed behavior. Distinguish upstream defaults, project overrides, and measurements. Give the conditions for measured results, label illustrative examples, and mark unresolved facts using the project's conventions. Where a repository uses specification markers, respect their established meanings, such as `TBD` for an unresolved value, `REF` for a reference pending verification, and `OK` for a frozen value.

## Project records

| Record | What belongs there |
| --- | --- |
| Project TODO | Milestones, priorities, pending decisions, constraints, risks, and follow-up work that affect the project beyond the current task. |
| Local audit | Task-specific commands, relevant output, failures, retries, verification evidence, and temporary blockers. |

When a repository task produces verification evidence and permits writing, identify the target project and read its instructions. Search the project tree for an existing `audit/` directory at any depth, including ignored directories. Exclude dependency trees, generated outputs, and separate repositories. If there is an existing project audit directory, write there and keep the layout and ignore rules as they are. If several directories qualify, prefer the documented location or the one used for the affected component.

If the project has no audit directory, tell the user which files and paths you will create before changing them. Create `docs/audit/` and add `/docs/audit/` to the repository's `.gitignore` if existing rules do not cover it. Check a prospective audit path with `git check-ignore` before writing. Create `docs/TODO.md` for durable project work if the project has no established TODO. Keep an established TODO in place.

Update the project TODO when a finding changes plans or priorities. Keep logs and command output in the audit. Carry the conclusion into the TODO or authoritative documentation when it changes the plan or a documented fact. Sanitize audit evidence before saving it.

## Edit for clarity

Use consistent domain terms and precise verbs. Keep identifiers exact and avoid emoji and decorative status symbols. Write one point per sentence. Use numbered lists for ordered actions, tables for comparisons or status, and language-tagged fences for commands and configuration. Cut hype, filler, rhetorical questions, obvious commentary, and accounts of the editing process. Replace vague claims with a behavior, limit, mechanism, or failure mode when the reader needs that information.

Follow the documented procedure from its stated starting point when it is safe to do so. Check modified links, heading anchors, commands, and examples using the project's usual checks. Use neutral example data without credentials or private addresses. Keep maintained translations aligned when a fact, command, or link changes.

## Code comments and docstrings

Write comments in English by default. Explain a constraint, invariant, side effect, or source that the code alone cannot convey. Keep public interface docs focused on caller needs, including units, valid ranges, errors, or compatibility where relevant. Update comments alongside behavior changes and remove obsolete notes or commented-out implementations within the edit's scope.

## Commit messages

Write in English by default and follow the repository's history and conventions. Where no convention is evident, use `type(scope): summary` with an optional scope and a concrete subject of at most 72 characters, without a closing period. Common types include `feat`, `fix`, `refactor`, `docs`, `test`, and `chore`.

Add a body after a blank line when the reader needs the original problem, the rationale, or the resulting behavior. Leave execution logs, audit labels, and conversation instructions in their appropriate records. When amending a commit, describe the final diff as a single coherent change.

[Writing Technical Documentation](https://github.com/ramkumar-r-goml/agent-skills/tree/main/skills/writing-technical-documentation) · [Documentation Engineering](https://github.com/nledford/engineering-review-board/tree/main/skills/documentation-engineering)
