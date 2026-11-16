# Qchip TEC

Mac owns the website, canonical shared UI and publisher repository. Lab owns native runtime, instrument/bridge code, Windows packaging and installation. Shared UI is a pinned immutable vendor package; edit its canonical Mac source first and deliver a new version.

Never use real COM/VISA or instrument parameter/output writes for tests. Use explicit isolated simulation data and ports 28774..28779. Do not copy real profiles, credentials or history into source exports or public packages. Preserve settings, pairing and logs during authorised upgrades; do not replay parameters or automatically acquire Remote control. Source development requires an explicit data directory.

Native and bridge share PRODUCT_VERSION. Build/update manifests, tag and executable versions must match. Updater replaces only its four program/legal files; runtime data/logs stay outside that allowlist. Legacy import is explicit copy-only. Public releases and formal installations require user authorisation. The user authorised the 1.0.1 website/native release on 2026-11-16; acceptance still excludes all real instrument parameter/output writes.
