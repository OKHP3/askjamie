# Hosted Lighthouse source revision signal

Lighthouse records the revision checked out by the runner, but that does not
identify the source currently served by a hosted target. A hosted server may
opt in by adding this response header to the page at the `--base-url` origin:

```http
X-AskJamie-Source-Revision: 552ec0a06aca2b68b820df9ef00667c6ef057957
```

Use the full Git object ID: exactly 40 hexadecimal characters for SHA-1 or 64
for SHA-256. Do not use a branch name, abbreviated hash, or deployment label.
The Lighthouse runner makes a no-cache request to the target at the start and
end of the run and records the header when present.

The report keeps this target-provided value separate from `sourceRevision`,
which describes the runner's own checkout. It records whether the deployed
revision matches, differs from, or cannot be compared with the runner checkout.
It also marks whether the target-reported revision stayed stable across the
run. A missing header, unsuccessful request, or malformed value is reported as
unavailable metadata; it does not stop measurement.

This is an explicit deployment-provided assertion, not a cryptographic proof
that the response body was built from that commit. A trustworthy deployment
must set the header from the exact source revision used for its release.
Revision metadata does not grant or change owner approval for Lighthouse
references.
