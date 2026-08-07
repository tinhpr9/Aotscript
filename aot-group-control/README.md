# AOT Group Control MVP

Phase 1 isolates the local Android controller from the existing Telegram Agent/Worker path.

Implemented in this phase:
- root capability probe;
- sanitized UI hierarchy collection (no UI text/content-description output);
- stable UI fingerprint based on package/resource-id/class/structure;
- semantic resource-id resolution;
- normalized tap resolution;
- selector tap with screen precondition;
- normalized swipe and Back with screen precondition;
- root PNG screencap validation;
- local unit self-test.

Not implemented yet:
- fleet relay / Cloudflare realtime session;
- AOT Hub UI;
- follower ACK/dedupe protocol;
- multi-device preview;
- provisioning recipe integration.

Security rule: this controller does not print password values, cookies, Shouko keys, tokens, or private config values.
