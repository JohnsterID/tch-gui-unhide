# Fork CI: the `develop` branch

This branch (`ci`) holds the tooling that keeps the fork's `develop` branch current. Nothing here is meant for
upstream.

## What `develop` is

`develop` = upstream `seud0nym/tch-gui-unhide` master + every branch listed in [`branches.txt`](branches.txt), merged in
that order. It is rebuilt from scratch on each run and force-updated, like an integration branch: don't base work
on it, and expect its history to be rewritten.

- **Only the fork owner's branches are trusted.** The list is explicit. Upstream pull requests are never merged
  automatically. `build-develop.sh` refuses the whole run when any listed branch carries a commit whose author or
  committer isn't the owner's address, or a `Co-authored-by` trailer.
- **Branch housekeeping.** A branch whose commits are already upstream is reported as `already in` and is harmless.
  Remove it from the list after its pull request is merged. A branch that is missing or no longer merges cleanly is
  skipped and reported, and the run is marked failed so it gets noticed.
- **Reproducible.** Merge commits take the newest committer date of their inputs, so the same inputs give the same
  `develop` SHA on any machine. `develop` is pushed only when that SHA changes.

## What the workflow checks

[`.github/workflows/develop.yml`](../.github/workflows/develop.yml) runs daily and on demand (Actions → develop → Run
workflow):

1. `build-develop.sh`: merges the branches and checks their authorship.
2. `check.py` on each listed branch and on `develop`, against upstream master:
   - every `*.lua` and transformer `*.map` compiles with Lua 5.1 `luac -p`;
   - every `*.lp` page compiles the way the stock `web/lp.lua` loads it;
   - every `sh` script passes `sh -n` and `busybox ash -n`;
   - no shellcheck *error* (BusyBox dialect) that upstream master doesn't already have; new warnings are listed;
   - `extras/tch-gui-unhide-xtra.*` equal a fresh `extras/src/make -all`.
3. `build/build` for upstream master and `develop`, then a check of every embedded installer payload: it must
   decode, decompress and list, and every `*.bz2` inside must decompress. This catches the CRLF-damaged `tproxy-go`
   archives that upstream master ships today.
4. The `develop` installers are uploaded as a workflow artifact (kept 30 days), and `develop` is pushed.

## What it doesn't do

It runs no device tests. The installers aren't executed, and nothing reaches a router. Each change still needs its
spare-router test before its pull request is updated.

## Running it locally

```sh
git remote add upstream https://github.com/seud0nym/tch-gui-unhide.git   # once
sh ci/build-develop.sh upstream origin       # leaves a local branch `develop`
python3 ci/check.py <tree> --base <upstream-master-tree> --extras [--installers]
```

Tools: `git`, Python 3, Lua 5.1 `luac` (`luac5.1`, or set `LUAC`), shellcheck ≥ 0.10 (for the `busybox` dialect),
`busybox`, `dos2unix` and `file` (for `build/build`).
