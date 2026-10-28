# Mandatory Completion Report

End every task handled by this plugin with a self-contained completion report.
Scale the detail to the task, but never omit the report because the task was
small, diagnostic, read-only, partially complete or blocked.

Lead with the actual outcome: `completed`, `partially completed`, `blocked`,
`diagnosed`, or `no changes made`.  Never let a successful build, upload or
command imply that the complete system is accepted.

Cover these items when applicable:

1. **What was done**: summarize implemented or reviewed work and link the
   important source, generated artifact, report and evidence paths.  State
   explicitly when no files were changed.
2. **How it works now**: explain the current operator-visible behavior and the
   relevant signal, data, control, lifecycle or deployment flow.  Describe
   important state transitions, ownership, safe-stop behavior and fixed
   latencies.  Distinguish requested, configured, simulated and measured state.
3. **How to use or adjust it**: identify critical parameters, where they are
   reviewed or edited, their practical consequences and a safe minimal example
   when this helps the operator.  Do not reproduce every register or option.
4. **Verification evidence**: list the tests and validators actually run and
   their results.  Include timing/DRC/CDC/constraint status, hashes, acceptance
   checks or qualification result when relevant.  State important checks that
   were not run.
5. **Hardware and deployment state**: name every live target touched, action
   performed, output/safe-state result, backup and rollback status.  If no board
   was contacted, no output was enabled or no deployment occurred, say so.
6. **Limitations and unresolved items**: report warnings, unknown target facts,
   untested claims, missing UI/service integration, timing or constraint debt,
   and anything that blocks deployment or release.  Separate new findings from
   known legacy debt when evidence supports that distinction.
7. **Next safe step**: give the smallest useful next action and route it to the
   owning app, FPGA, deployment or qualification skill when required.

Use exact evidence instead of vague claims.  Prefer concise tables for several
measured gates, and a short flow chain only when it makes the new behavior
clearer.  Do not dump raw logs.  The final report must make sense without any
earlier progress messages.
