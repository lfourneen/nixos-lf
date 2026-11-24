---
name: engineering-judgment
description: Use for software changes that affect contracts across components, architecture tradeoffs, unclear failures or regressions, engineering reviews, and technical writing that documents operational or caller-facing behavior. Trace real entry points and use evidence to decide what to change and verify. Handle routine local edits directly.
---

# Engineering judgment

Deliver a change that works through its normal entry point, accounts for existing consumers, and can be maintained by someone who did not participate in the work.

## Establish the contract

- Turn the requested outcome into observable acceptance criteria. Identify the behavior that should change and the callers, configuration, persisted data, or runtime constraints that the change may affect. Honor review-only, design-only, and diagnosis-only requests.
- Trace the actual entry point to the component that owns the behavior. Read the relevant callers, defaults, tests, and release or deployment paths. Distinguish requested behavior, documented guarantees, observed behavior, and inference. Resolve conflicts using the active version and its consumers.
- Use existing evidence to settle routine uncertainty. State reversible assumptions and continue. Escalate ambiguities that materially change the public behavior, scope, or reversibility of the work.

## Choose the work that addresses the risk

| Situation | What to establish |
| --- | --- |
| Failure or regression | Find an observable signal and the first boundary that violates the contract. Use a probe that distinguishes plausible causes. See [Debugging and regression](references/debugging-and-regression.md). |
| Design or refactor across boundaries | Identify the owner of the state or rule, which consumers must evolve together, and the cost of failure or rollback. See [Design and review](references/design-review.md). |
| Review or regression sweep | Stay within the requested read-only scope. Prioritize findings by user impact and show the trigger, consequence, and evidence. See [Design and review](references/design-review.md) or [Debugging and regression](references/debugging-and-regression.md). |
| Changes to usage, upgrades, or troubleshooting | Update the maintained instructions at the reader's entry point. Check commands, defaults, and limits against the source or actual behavior. See [Technical writing](references/technical-documentation.md). |

Prefer the existing implementation when it satisfies the contract. If a patch spreads the same rule across multiple places or successive fixes move the failure between layers, revisit ownership before adding another patch.

## Verify the delivered behavior

- Use the project's normal entry points and checks. Where practical, compare the same input and environment before and after the change. Verify the adjacent behavior or downstream contract most likely to break. Expand coverage in proportion to the impact.
- Check at the layer where the behavior can fail. For a write path, read the result through the actual consumer. Where old data, retries, or partial failures matter, check the resulting state under those conditions.
- Review the final diff against the acceptance criteria and update affected callers, generated artifacts, and documentation as needed. Report the outcome, relevant evidence, and verification gaps. Distinguish your own runs from evidence supplied by others.
- Put durable decisions and pending work in the project TODO. Keep command output, failed attempts, and verification evidence in a local audit for the task. See [Project records](references/technical-documentation.md#project-records) for locating or setting up both.

## Language and writing

Use the user's language for conversation, including questions, progress updates, and the final reply. Follow any explicit request for a different reply language.

Use English by default for work artifacts, including repository documentation, code comments, docstrings, examples, commit messages, and written reports saved as deliverables. Follow explicit artifact language requests and applicable repository conventions. Keep commands, paths, identifiers, and quoted evidence in their original form.

Use the project's terminology and write concrete statements with a subject, action, and consequence. Remove stock phrasing, exaggerated benefits, progress narration, rhetorical headings, and repeated conclusions. See [Technical writing](references/technical-documentation.md) when writing or editing documentation, comments, or commit messages.
