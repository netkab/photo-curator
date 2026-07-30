---
name: photo-reclaim
description: Reclaim disk space from the Takeout folder by MOVING redundant local duplicates to a quarantine folder — only when the keeper exists on disk AND is live in Google Photos, so two copies always survive. Never deletes, never touches a sole copy, and every run is undoable from its manifest. Use when the Takeout export is taking too much disk.
---

Free disk space from the Takeout export without losing anything.

## ⚠️ Read this before touching any file

The obvious version of this idea destroys photos. Files that feel most "already dealt with" — the
duplicates you removed from Google Photos months ago — are exactly the ones where **your Takeout copy
is now the only copy in existence**. Deleting by "already handled", "unlinked", or "not in Google
Photos" is data loss, not cleanup.

Check the split first:

```powershell
cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli reclaim
```

or `GET /api/reclaim/summary`, which splits the library into **backed by Google** (two copies exist),
**sole copies** (last copy anywhere — never touched), and **already quarantined**.

## Steps

1. Run `photo-gp-sync` first. Without link coverage, everything looks like a sole copy and almost
   nothing qualifies.

2. Preview (read-only, changes nothing):

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli reclaim
   ```

   Reports what would move plus a breakdown of *why* everything else was excluded.

3. Apply:

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli reclaim --apply
   ```

4. Undo any run:

   ```powershell
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli reclaim --list
   cd "C:\photo-curator\backend" ; .\.venv\Scripts\python -m app.cli reclaim --undo 20260728-143255
   ```

Same flow in the extension's **Storage** tab, with the summary and history rendered.

## The safety rule

A file is a candidate only when **all** hold:

1. it's a non-keeper member of a duplicate group;
2. its group's keeper exists **on disk**;
3. that keeper is **live in Google Photos**;
4. it isn't itself the keeper of some other group;
5. another copy of its exact bytes exists.

Conditions 2 and 3 together guarantee two independent copies survive every move — one local, one in
Google.

## Notes

- **Moves, never deletes.** The catalog follows the file (`abs_path` updated, `archived_at` /
  `archived_from` set), so thumbnails and analysis keep working from quarantine.
- `QUARANTINE_DIR` in `backend/.env` sets the destination; defaults to a sibling of `TAKEOUT_DIR`.
  **Keep it on the same drive** — a same-volume move is instant and atomic, whereas crossing drives
  copies every byte and can half-finish.
- Videos are excluded unless `--include-videos`: biggest files, hardest to re-acquire, and their
  "duplicates" are usually re-encodes rather than true copies.
- Every run writes `data/exports/reclaim_<run_id>.json` — that manifest is what makes undo possible.
  Don't delete them.
- Collisions are disambiguated (`name__1.jpg`), never overwritten — Takeout splits one library across
  archive folders, so repeated relative paths are normal.

## Rules

- Never delete a local original. This skill only ever moves files.
- Never propose deleting files that aren't in Google Photos — those are sole copies.
- Always preview before applying, and report the exclusion breakdown, not just the headline number.
