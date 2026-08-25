# Community index status

**Submission status: externally blocked.** The Hermes 0.20.4/0.20.5 default index URL targets `NousResearch/hermes-plugin-index`, but the repository returns HTTP 404, as does the raw `index.json` endpoint.

Current CLI evidence:

```text
$ hermes plugins search t3 --json --refresh
{
  "source": "seed",
  "query": "t3",
  "results": []
}
```

Upstream state as of 2026-08-25:

- The documented destination `https://github.com/NousResearch/hermes-plugin-index` is unavailable.
- `https://github.com/NousResearch/hermes-agent/issues/86154` remains open for the dead index/submission path.
- `https://github.com/NousResearch/hermes-agent/issues/87565` independently tracks the still-unpublished seed repository.
- `https://github.com/NousResearch/hermes-agent/pull/87627` proposes a third-party temporary host and remains unmerged.
- `https://github.com/NousResearch/hermes-agent/pull/86214` proposes keeping the index in the Hermes repository and remains unmerged.

Do not submit the entry to the non-existent repository or present the unmerged third-party host as official. A community-index PR becomes valid only after Hermes maintainers establish and ship an accessible canonical target.

## Prepared entry

[`community-index-entry.json`](community-index-entry.json) is valid schema-v1 entry metadata and pins the current public supported v1.2.2 release commit with its eleven-tool surface. It declares only the discovery capability `tools`; the plugin has no hooks, commands, dashboard, or tool overrides.

When an official target exists:

1. Update `ref` to the exact new release commit (40 lowercase hexadecimal characters).
2. Re-run the release's public supported-install checks on Hermes 0.20.4 and 0.20.5.
3. Insert the entry into the canonical index's `plugins` array without changing its schema.
4. Open the upstream PR and read back the merged raw index.
5. Verify `hermes plugins search t3 --json --refresh` returns `source: remote` and resolves `hermes-t3-control` to that exact ref.

Index inclusion is metadata review, not a code audit; installation must retain scanner, disabled-install, Doctor, and explicit enable consent.
