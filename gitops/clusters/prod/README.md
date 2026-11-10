# Production environment (placeholder)

This directory is the shape prod will take. It is not deployable yet: there is
no `root.yaml` and `scripts/bootstrap-argocd` refuses `--env prod`.

When prod is created, mirror `../stage` with these differences:

- **Sync policy.** Automatic sync stays off for every Application. A release
  moves a reviewed revision into the prod branch or tag, and an operator syncs
  each wave in order after the runbook's migration and fleet checks. This keeps
  the OCE upgrade lifecycle in the runbooks, as AGENTS.md requires.
- **Revision.** Prod tracks a dedicated branch or tag, not `main`, so a stage
  merge cannot reach prod. Pick the mechanism when the prod cluster exists;
  stage tracks `main` today (see `docs/gitops.md`).
- **Scope.** Start with the same five platform stages. Decide separately
  whether control-plane, control-ui and control-ui-access become Applications;
  their inputs are private and cluster-specific today.
- **RBAC boundary.** Use a separate Argo CD instance or a separate AppProject
  and controller identity for prod, so a stage mistake or credential cannot
  touch prod namespaces.
- **Sizing and storage.** Replace the stage placeholders with the prod
  allocation; do not reuse the stage database volume size.
- **Data.** Prod is adopted from the existing runbook deployment, so review
  `argocd app diff` per wave and enable sync only after an empty diff.

Do not copy secrets, kubeconfigs or rendered inputs into this directory.
