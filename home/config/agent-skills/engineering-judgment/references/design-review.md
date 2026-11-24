# Design and review

## Choose the change boundary

Trace a real call from its entry point through the rule owner, state changes, and output. Record constraints that affect the choice, such as supported consumers, state lifetime, release order, and environment differences. Identify which component maintains each invariant and how other components rely on it.

- Check whether an existing pattern can express the new behavior. Consolidate logic that represents the same rule and must evolve together. Give every new interface, configuration option, or abstraction a concrete consumer.
- Compare viable designs when the choice has consequences. Explain change scope, operating cost, compatibility, and recovery. Name a fact that would change the recommendation, such as an installed client that cannot upgrade alongside the service. Record decisions with lasting consequences where the repository keeps them.
- For a stateful change, trace the most consequential failure. A write can succeed while its response is lost, a migration can stop halfway, and old and new processes can run together. Identify the expected state after each relevant failure and whether a retry duplicates work.
- For published interfaces or stored data, check the version combinations that can actually coexist. When a migration is required, account for deployment order, reading old state, the introduction of new writes, and what a rollback can read. Use a representative old fixture and test resuming after interruption when relevant.

## Review observable consequences

Start from the diff and follow the affected entry points and consumers. Prioritize data errors, broken calls, and failed operations. For each finding, give the triggering input or environment, the code location, what a caller or operator would observe, and the supporting evidence. For a static finding, show the reachable path and state the limits of the review. When there are no findings, summarize meaningful coverage and any gap that affects confidence.

For example, before renaming an internal state from `expired` to `stale`, check whether it reaches stored records, API responses, or log filters. If jobs resume after restart, verify that old records still load, the state survives restart, and any supported old reader can handle new records.

[Software Architecture](https://github.com/edhahn/agent-skills/tree/main/software-architecture) · [Regression Sweep](https://github.com/khasky/awesome-agent-skills/tree/main/skills/awesome-regression-sweep)
