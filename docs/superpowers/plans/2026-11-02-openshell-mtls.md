# OpenShell mTLS Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan.

**Goal:** Provide reviewable configuration and an executable operator procedure
for the existing OpenShift deployment's authenticated HTTPS cutover.

**Architecture:** Use the OpenShell 0.1.6 chart's cert-manager integration and a
dedicated OCE client Certificate. Preserve OIDC and existing runtime state.

**Tech Stack:** Helm 3, Kustomize, cert-manager, yq v4, Bash, OpenShift.

**Spec:** ../specs/2026-11-02-openshell-mtls-design.md

## Global constraints

- Private inputs and rendered resources remain outside both source checkouts.
- No cluster mutation, image change or repository enrollment during PR preparation.
- Preserve identities, JWT/encryption keys, model sources and PVCs.

## Review focus

- Chart-managed CA lifetime and key retention during subsequent Helm upgrades.
- OIDC dashboard requests must retain user identity without an admin client key.
- Cross-namespace client renewal requires synchronization and client restart.
- Existing supervisor connections may need repair after the transport change.
- Rollback restores original transport and all original client configurations.

## Tasks

- [x] Add opt-in Gateway/dashboard values, OCE client Certificate, and CA
      lifetime post-renderer. Render the pinned charts and inspect TLS, Secret mounts,
      SANs, certificate usages, and unchanged image/storage references.
- [x] Document preparation, cutover, verification, renewal and rollback; link
      from the installation, upgrade and dashboard guides.
- [x] Validate shell/YAML/Kustomize, links and formatting; review the whole diff.
- [ ] Create a signed-off commit and PR; report validation and live-proof gaps.
