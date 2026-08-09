# Security policy

- Never commit account names, passwords, tokens, cookies, full camera IDs,
  wake signing values, packet captures, media, vendor binaries, or raw traces.
- Supply account secrets through the operating system secret store or Home
  Assistant app options, never command-line arguments.
- Vendor artifacts must match `docs/vendor-artifacts.json` before extraction.
- The tracer writes sanitized JSONL only. Treat even sanitized traces as local
  development artifacts under ignored `captures/`.
- The bridge exposes no public inbound port. Home Assistant reaches it over the
  private app network and all vendor connections are outbound.

If a secret is accidentally committed, revoke/rotate it before rewriting
history. Do not rely on history rewriting as revocation.
