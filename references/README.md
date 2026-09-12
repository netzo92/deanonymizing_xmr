# Upstream implementation references

Monero v0.18.5.1 is checked out locally at `references/monero`, pinned to commit
`4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5`. The checkout is ignored by the app
repository; [source metadata](monero-source.json) records the upstream URL,
release, tag object, resolved commit and retrieval time. The release is the
latest published GitHub release observed during this audit. Release pinning does
not imply that all historical or third-party wallets used this version.

The source reference has not been built or run. Submodules were not initialized.
It supplies implementation context for the source-linked audits in `research/`
and the maintained research brain. It is excluded from web/runtime deployment
archives; only this README and the pin metadata are packaged for note provenance.

Recreate this reference from the project root:

```bash
git clone --depth 1 --single-branch --branch v0.18.5.1 https://github.com/monero-project/monero.git references/monero
git -C references/monero rev-parse HEAD
```

Check that HEAD matches the full commit recorded above before using line-based
references. Keep local modifications separate from the pinned reference and
record a new pin when auditing a different release or historical version.
