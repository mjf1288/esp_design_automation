# ESP iteration handoff

- The user-facing mirror is `https://github.com/mjf1288/esp_design_automation`.
- After each requested work session, update this runnable application and README, test it, scan staged files and outgoing Git history for secrets, and push a normal new commit to this repository.
- Confirm GitHub reports the repository is private before every push. Stop if privacy cannot be confirmed.
- Use short, plain-language commit messages that describe the user-visible change.
- Fetch first. If the remote has changed or histories diverge, do not force-push, reset, discard or silently merge the user's work. Explain the difference and ask how to proceed.
- Preserve local experiments and databases. A remote push cannot inspect edits that only exist on the user's computer; do not claim otherwise.
- Never commit working credentials, `.env` values, logs, local databases or private keys. References to credential variable names are fine; usable example credentials are not.
- Keep the default local app deterministic and credential-free. The optional platform narrative SDK is not part of the local setup.
- Keep synthetic provenance visible. Prototype only, not for field use.
- Provide English reporting and a concise Russian engineering summary as requested in the current session.
