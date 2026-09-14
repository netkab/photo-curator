# Local cleanup security

The supported server binds only to loopback, validates Host, and requires either a random 256-bit
pairing token (Bearer header) or a same-origin UI session for all API and thumbnail reads/writes.
Only the two explicit local UI origins and configured exact Chrome extension IDs are allowed.
No tokens are accepted in URLs; query strings never grant access. Cookie writes require an allowed
UI Origin. Login does not expose the local token. Disconnect expires the UI session; to rotate the
pairing token, stop the backend, remove `.local-token`, restart, then pair clients again.

The supported extension uses narrow Google Photos/localhost host permissions. Only its app page may
invoke worker commands. Google page scripts cannot ask the worker to call arbitrary backend URLs or
execute arbitrary mutations, and they never receive the local token. The adapter is invoked using
Chrome's scripting API rather than a public window-message mutation listener. Thumbnail proxy requests
are restricted to authenticated cached image paths on the fixed local backend.

Google Photos itself remains a trusted execution surface: undocumented calls execute in its MAIN
world with its signed-in session. A compromised Google page or another malicious installed extension
is outside this tool's protection. Origin and local authentication do not protect against malware
already running as your OS user. Keep the local data private and do not expose either server remotely.

Live trash needs server opt-in, explicit approval of the exact account/content keys, a surviving
catalog keeper with a different content key, and an explicit operation start. Unknown ownership and
unreviewed aliases block approval. Gates are rechecked for every execution batch. Single-process
serialization and durable expiring leases prevent competing clients from receiving the same batch
at once. Do not run multiple backend workers against this SQLite file.

Approval and catalog checks cannot guarantee that the remote library is unchanged since scanning.
Google's responses are undocumented and batch acceptance is not item-by-item verification. Stop on
uncertain results. An in-flight operation may finish after Stop; its result must be recorded before
Undo. No permanent-delete payload or empty-trash control exists in the supported adapter/API.

Automated tests use synthetic images and mocked Google responses. Real library scanning, mutation
acceptance, Google retention, optional model inference, and Windows ACL/process behavior still need
manual validation in the target environment. The dependency audit is a point-in-time check, not a
claim of complete security.
