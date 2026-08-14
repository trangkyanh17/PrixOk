# V151 Telegram shadow production canary

This is the managed phone-side activation path for V151 phase 1 after the observe-only bridge has landed in `main` and CI is green.

The objective is narrow: let the existing Python/Pyrogram worker keep **100% Telegram ownership** while a local V151 observer mirrors update envelopes to the Go supervisor for parity measurement.

## Safety boundary

The canary manager is `rewrite/termux-v151-shadow-canary.sh`.

It does **not** replace the live `/app` tree and does not perform repository update/reset/switch/clean operations. The only live Python paths it may touch are:

```text
/app/bot/__main__.py
/app/bot/modules/atri_v150_shadow.py
```

`rewrite/v151_shadow_patch.py` patches `bot/__main__.py` only when it finds exactly one known import anchor and exactly one `add_handlers()` call anchor. It keeps all other customized live code unchanged. It backs up the original file and the pre-existing shadow module, if any, before mutation.

If the new module fails compile/verification during the patch transaction, the patcher restores the pre-apply files before returning failure.

The canary also keeps the existing V150 deployment transaction underneath it. A supervisor upgrade still receives the normal V150 runtime snapshot and automatic rollback protection.

## What `apply` does

In one transaction it:

1. requires Termux host context, a clean isolated `/opt/prixok-v150` clone on `main`, one V150 watchdog owner, zero legacy watchdogs, a live bot pane, held singleton lock, and healthy local production;
2. creates a timestamped V151 backup under `$HOME/.local/state/atri-v151-shadow/backups/`;
3. guardedly injects the V151 observer registration into the customized live `bot/__main__.py` and copies only `bot/modules/atri_v150_shadow.py`;
4. writes a private host enable sentinel plus `$HOME/.local/state/atri-v151-shadow/runtime.env`;
5. installs the current V150 deploy manager from the isolated clone and performs a normal V150 `upgrade`;
6. waits for the loopback-only shadow ingress to become healthy;
7. sends `Ctrl-C` only to the `prixok-bot` tmux pane and lets the sole V150 watchdog recreate the worker;
8. requires a new pane PID, held singleton lock and healthy local production;
9. requires the fresh Python startup log to contain `ATRI_V150_TELEGRAM_SHADOW_ENABLED`;
10. sends one synthetic local shadow envelope and requires HTTP 202;
11. re-checks the historical boot-hook FD leak invariant;
12. emits one report into Android Download.

If a post-patch stage fails, `apply` disables the shadow state, restores the guarded Python backup, rolls the V150 runtime back when the V150 upgrade had committed, and reloads the worker when a restart had been attempted.

## One-time phone command

First update only the isolated clone. Never run these source update commands in `/app`.

```bash
proot-distro login debian
cd /opt/prixok-v150
git pull --ff-only origin main
cp rewrite/termux-v151-shadow-canary.sh \
  /data/data/com.termux/files/home/termux-v151-shadow-canary.sh
chmod 700 \
  /data/data/com.termux/files/home/termux-v151-shadow-canary.sh
exit

bash "$HOME/termux-v151-shadow-canary.sh" apply
```

The apply command already writes its full report to:

```text
/storage/emulated/0/Download/atri-v151-shadow-apply-YYYYMMDD-HHMMSS.txt
```

Do not manually restart the bot or watchdog while this transaction is running.

## Expected final state

A successful apply ends with all of these properties:

```text
one V150 watchdog owner
legacy watchdogs = 0
prixok-bot session present
new bot pane PID
worker singleton lock held
local production health healthy
shadow ingress healthy on 127.0.0.1:18750
Python observer enabled
synthetic local probe accepted
NO_BOOT_LOCK_FD
Python remains Telegram owner
```

The shadow bridge still has no Telegram send/edit ownership and there is no second Telegram polling client.

## Real Telegram observation check

`apply` proves the local path with a synthetic envelope. To prove the Python dispatcher -> V151 bridge on the real bot, send one ordinary Telegram message to the bot after `apply` finishes, then run:

```bash
bash "$HOME/termux-v151-shadow-canary.sh" status
```

`ingress=` contains the Go `/healthz` JSON counters. Because `apply` contributes one synthetic accepted event, a real observed Telegram update should increase `accepted` beyond that baseline.

The status command is read-only and also reports production health, source-hook state and boot-lock-FD state. Its report is written to Android Download.

## Manual rollback

If V151 shadow observation must be removed before any later V150 deployment changes the rollback pointer:

```bash
bash "$HOME/termux-v151-shadow-canary.sh" rollback
```

Rollback is intentionally stale-safe. It refuses to use an old canary backup when either the current deployed SHA or V150 deployment backup pointer has changed since that canary.

The guarded source patcher also refuses to overwrite `bot/__main__.py` if that live file has been edited after the V151 canary was applied.

## State and backups

V151 canary state:

```text
$HOME/.local/state/atri-v151-shadow/
```

V151 source backups:

```text
$HOME/.local/state/atri-v151-shadow/backups/
```

V150 runtime snapshots remain under:

```text
$HOME/.local/state/atri-v150-deploy/backups/
```

Do not delete these directories while the shadow canary is active.
