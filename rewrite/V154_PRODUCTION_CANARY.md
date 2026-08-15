# V154 production canary

This is the phone-side acceptance gate for the Atri V154/V154.1/V154.2 hardening already merged into `main`.

It is intentionally **not** a source deploy. `/app` remains the customized production tree and is never updated with Git. The canary copies only the five V154 guard modules and inserts the narrow startup hooks into `bot/__init__.py` and `bot/__main__.py` through `v154_production_patch.py`.

## Preconditions

- Run from the Termux host, not inside Debian.
- `/opt/prixok-v150` is `main`, has zero tracked/staged/untracked changes, and `HEAD` exactly matches the local `origin/main` tracking ref.
- Exactly one V150 supervisor owns production; no legacy mutating watchdog exists.
- `prixok-bot` exists, its lock is held and local health is healthy.
- V151 Gate A, V152 Gate B1 and the V153 live source guard are already active.

If a prerequisite is missing, `apply` stops before touching V154 source.

## One production run

From Termux host:

```bash
proot-distro login debian -- bash -lc 'cd /opt/prixok-v150 && git status --short --branch && printf "HEAD=" && git rev-parse HEAD && printf "origin/main=" && git rev-parse origin/main'
cp "$(find "$PREFIX/var/lib/proot-distro" -path '*/debian/rootfs/opt/prixok-v150/rewrite/termux-v154-production-canary.sh' -print -quit)" "$HOME/termux-v154-production-canary.sh"
chmod 700 "$HOME/termux-v154-production-canary.sh"
bash "$HOME/termux-v154-production-canary.sh" apply
```

The script itself does not run `git pull`, `git reset`, `git checkout` or `git clean`. Git operations inside the canary are read-only integrity checks only.

A complete report is written to:

```text
/storage/emulated/0/Download/atri-v154-production-apply-YYYYMMDD-HHMMSS.txt
```

If Android shared storage is unavailable, the report falls back to:

```text
$HOME/.local/state/atri-v154-production/
```

## What apply proves

1. Termux/PRoot bridge and production venv are available.
2. Isolated candidate clone is trusted locally: branch `main`, no tracked/staged/untracked drift, and `HEAD == origin/main`.
3. One V150 supervisor, no legacy watchdog, one healthy bot session and held worker lock.
4. V151 Gate A and V152 Gate B1 still have zero parity mismatch; V153 live patch verifies.
5. `python-docx`, `openpyxl`, `PyMuPDF`, `PyYAML` and `playwright` import in the production venv. If installation is needed, `v154_package_guard.py` first snapshots every installed distribution/version, runs a pip dry-run plan, then performs the install. A successful transaction may add distributions but may not change or remove any pre-existing distribution/version.
6. Exactly seven live source paths are transactionally managed:
   - `bot/__init__.py`
   - `bot/__main__.py`
   - `bot/modules/atri_system_guard.py`
   - `bot/modules/atri_sticker_privacy_guard.py`
   - `bot/modules/atri_webapp_safety_guard.py`
   - `bot/modules/atri_xlsx_formula_guard.py`
   - `bot/modules/atri_artifact_relevance_guard.py`
7. `bot/modules/atri_ai.py` is not edited.
8. Bot performs one controlled restart; the existing V150 watchdog remains the sole lifecycle owner.
9. The restarted real bot emits all V153/V154 install markers, including the post-import tool-round guard.
10. An isolated probe loads the **live** guard files without importing the bot package and tests:
    - nested ZIP expansion and streamed-member overrun rejection;
    - voice classification and oversize audio rejection before download;
    - model `functionCall` round detection;
    - persistent-artifact unrelated-query rejection plus relevant follow-up acceptance;
    - sticker chat-scope isolation using an isolated SQLite fixture;
    - XLSX raw `=...` text escaping, explicit safe formula preservation and network-formula rejection;
    - webapp loopback rejection and public-literal acceptance.
11. If a configured CDP runtime state exists, its `127.0.0.1` endpoint must answer `/json/version`. Absence of an optional browser runtime does not block the core canary.
12. V151/V152/V153 invariants, boot-lock FD cleanliness and final production health are rechecked after all probes.

## Automatic rollback

Any failure after mutation triggers `AUTO ROLLBACK`:

- restore the pre-V154 seven-file source snapshot only if its stale-SHA checks still match;
- restore the pre-canary logical distribution/version snapshot; newly added distributions are removed, and an interrupted pip transaction that changed an old version is restored and verified;
- perform a controlled restart only when source/package rollback completed safely;
- recheck V151 Gate A, V152 Gate B1, V153 and production health.

If source or package rollback is stale/incomplete, the canary refuses the automatic restart and marks production for manual inspection instead of pretending rollback passed.

## Manual status / rollback

```bash
bash "$HOME/termux-v154-production-canary.sh" status
bash "$HOME/termux-v154-production-canary.sh" rollback
```

Manual rollback accepts only `last-backup` paths resolving under `$HOME/.local/state/atri-v154-production/backups/apply-*`, requires a complete source/package snapshot, and requires the backup `REPO_SHA` to match the current trusted clone SHA. This prevents a later canary revision from interpreting an older backup with different rollback semantics.

Do not use manual rollback to erase later intentional production edits. The source patcher will refuse a stale destructive rollback and report the changed path.
