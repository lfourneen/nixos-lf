# Debugging and regression

## Gather evidence that distinguishes causes

1. Record the expected behavior, observed result, entry point, and relevant inputs, versions, call order, or environment. Choose a repeatable check that produces the reported failure. For intermittent behavior, record how many runs failed and the conditions used.
2. Compare with a known-good version, a comparable path, or the same input under a different configuration. Inspect inputs, outputs, and state at the relevant boundaries. Locate the first divergence from the contract.
3. Before changing a factor or instrumenting a boundary, identify the result that would support or rule out each plausible cause. If probes stop narrowing the problem, reassess the entry point, prerequisites, and evidence. When reproduction is unavailable, use logs, prior failures, or a reachable code path and report the remaining uncertainty.
4. Fix the boundary that owns the behavior when the evidence supports the causal path. Run the same check against the fix. If a new regression test is warranted, choose a stable assertion at an entry point that can detect the original failure.

## Select checks based on the change

| Surface | What to verify |
| --- | --- |
| Caller or configuration contract | Exercise the real caller, CLI, or configuration entry point. Check success, rejection, and defaults where relevant. |
| Persistence, retries, or concurrency | Inspect final state through the reader. Test old data, interruption, duplicate requests, or concurrent writes when the change affects them. |
| Release artifact or shared format | Build an actual downstream consumer, inspect shipped or generated files, or compare outputs using the same input across implementations or versions. |
| Intermittent failure | Keep the trigger conditions comparable and report sample sizes and failure rates. Preserve failures seen before a successful rerun. |
| Deployed behavior | Verify that the tested version and configuration match the running artifact. Work in an isolated environment or use authorized read-only observations. |

Compare specific test failures and contract changes against a comparable baseline when one exists. Check whether each difference comes from the product, the environment, or the harness. Inspect reported results as well as exit codes, and confirm the runner selected the intended tests if discovery is in doubt. Use isolated resources for tests that write shared state. Name blocked checks and the conclusions that depend on them.

## Memory pressure and OOM

- On Linux, use `nproc`, `free -h`, and `swapon --show` for the starting budget. Use `/proc/pressure/memory` and `/proc/pressure/io` to check stalls. For cgroup v2 workloads, check `cpu.max`, `memory.current`, `memory.high`, `memory.max`, and `memory.events` in the worker cgroup and relevant ancestors. Identify the actual worker cgroup when a client delegates work to a daemon.
- With systemd, use `systemctl is-active systemd-oomd` and `oomctl dump` to check the active policy, monitored cgroups, and pressure limits. Systemd-oomd can act on sustained stalls. The kernel can invoke its own OOM killer when an allocation fails. Free RAM and unused swap alone cannot establish that a high-order allocation can obtain contiguous pages.
- After an unexpected process exit, correlate timestamps in `journalctl -k -b` and `journalctl -u systemd-oomd -b`. Match the victim PID and cgroup to the affected workload. For kernel OOM, check the allocation order, zone watermarks, and buddy free-list counts before attributing it to fragmentation or a limit. A `SIGKILL` or exit code `137` alone cannot identify who sent the signal.

[Systematic Debugging](https://github.com/khasky/awesome-agent-skills/tree/main/skills/awesome-bug-fix) · [Regression Sweep](https://github.com/khasky/awesome-agent-skills/tree/main/skills/awesome-regression-sweep)
