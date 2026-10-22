---
name: ASCII-locale test boundaries
description: Distinguishing hosted protocol decoding from Python OS argument decoding in locale regression tests.
---

An ASCII Python process can decode international operating-system arguments with
surrogate escapes before application code runs. Testing hosted UTF-8 decoding
does not establish support for those international CLI arguments.

**Why:** ASCII-locale hosted-output tests must not silently enable Python UTF-8
mode to fix argument handling; that would remove the condition they need to test.

**How to apply:** Explicitly disable UTF-8 mode and locale coercion and assert the
child's actual preferred encoding. Pass UTF-8 payloads as raw bytes from offline
fixtures. Keep operating-system argument decoding a separately evidenced concern;
do not claim it is fixed by changing the hosted-output decoder.

For hosted branch CLI arguments on POSIX, use an explicit strict UTF-8 byte
contract in both directions: recover the original OS argument bytes before
decoding, and send hosted subprocess arguments as UTF-8 bytes. Never normalize
composed/decomposed spellings or enable UTF-8 mode to hide an ASCII-locale bug.
Local paths and non-hosted options retain their existing decoding policy.

For invalid-byte local filename regressions, keep real byte paths and exercise
the complete CLI, including recovery snapshot creation. Do not relax the shared
text command runner's decoding merely to ignore filenames in object inventories.

**Why:** Recovery snapshot creation can encounter raw Git object-path labels
before the ledger's surrogateescape tree reader runs. The snapshot needs object
IDs, not those labels; broad replacement decoding would hide a separate boundary
failure and weaken unrelated text-output checks.

**How to apply:** Request ID-only output for object inventories and retain
byte-oriented path fixtures plus full read-only assertions. Keep local filename
support distinct from the strict hosted UTF-8 protocol contract.

**Why:** Fixing only incoming argv still leaves subprocess encoding dependent
on the ASCII filesystem codec; fixing only outgoing argv leaves branch identity
as surrogate escapes in comparisons and reports.

**How to apply:** Keep real CLI tests with UTF-8 byte arguments, disabled locale
coercion/UTF-8 mode, and offline command stand-ins that verify their own active
ASCII encoding. Reject invalid UTF-8 during argument parsing before commands.
