# V154 production canary

This is the phone-side acceptance gate for the Atri V154/V154.1/V154.2 hardening already merged into `main`.

It is intentionally **not** a source deploy. `/app` remains the customized production tree and is never updated with Git. The canary copies only the five V154 guard modules and inserts the narrow startup hooks into `bot/__init__.py` and `bot/__main__.py` through `v154_production_patch.py`.

## Preconditions

- Run from the Termux host, not inside Debian.
- `/opt/prixok-v150` is a clean `main` clone at the candidate SHA.
- Exactly one V150 supervisor owns production; no legacy mutating watchdog exists.
- `prixok-bot` exists, its lock is held and local health is healthy.
- V151 Gate A, V152 Gate B1 and the V153 live source guard are already active.

If a prerequisite is missing, `apply` stops before touching V154 source.

## One production run

From Termux host:

```bash
proot-distro login debian -- bash -lc 'cd /opt/prixok-v150 && git status --short --branch'
cp "$(find "$PREFIX/var/lib/proot-distro" -path '*/debian/rootfs/opt/prixok-v150/rewrite/termux-v154-production-canary.sh' -print -quit)" "$HOME/termux-v154-production-canary.sh"
chmod 700 "$HOME/termux-v154-production-canary.sh"
bash "$HOME/termux-v154-production-canary.sh" apply
```

The script itself does not run `git pull`, `git reset`, `git checkout` or `git clean`. The first line above is read-only and is only there to make the candidate clone state visible before the transaction.

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
2. Isolated candidate clone is clean `main`.
3. One V150 supervisor, no legacy watchdog, one healthy bot session and held worker lock.
4. V151 Gate A and V152 Gate B1 still have zero parity mismatch; V153 live patch verifies.
5. `python-docx`, `openpyxl`, `PyMuPDF`, `PyYAML` and `playwright` import in the production venv. Missing packages are installed without `--upgrade`; all newly-created distributions are recorded for rollback.
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

- restore the exact pre-V154 seven-file source snapshot;
- controlled restart back into the previous source;
- remove only Python distributions that were absent before this canary;
- recheck V151 Gate A, V152 Gate B1, V153 and production health.

Rollback refuses to overwrite a live source file whose SHA changed after V154 apply.

## Manual status / rollback

```bash
bash "$HOME/termux-v154-production-canary.sh" status
bash "$HOME/termux-v154-production-canary.sh" rollback
```

Do not use manual rollback to erase later intentional production edits. The patcher will refuse a stale destructive rollback and report the changed path.
