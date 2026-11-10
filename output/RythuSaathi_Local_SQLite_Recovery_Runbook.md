# RythuSaathi Local SQLite Recovery Runbook

Use this procedure only after a database restore is needed. It deliberately
requires credential re-enrollment; encrypted records from a backup are never
activated or decrypted automatically.

## Restore procedure

1. Stop every RythuSaathi process using the SQLite database. Keep the current
   database file and the backup in the same protected local storage boundary.
2. Run `restore_from_backup(backup_path, live_database_path)` from
   `rythusaathi.recovery` while the application is offline. For example, in a
   controlled Python maintenance shell:

   ```python
   from rythusaathi.recovery import restore_from_backup

   report = restore_from_backup(r"C:\data\rythusaathi-backup.sqlite3",
                                r"C:\data\rythusaathi.sqlite3")
   print(report)
   ```

3. Confirm the report counts and inspect `CredentialService.recovery_required()`;
   it must be `True`. The restore invalidates every session, quarantines all
   non-deleted credential rows, clears their old ciphertext and wrapped DEKs,
   and imports current revoke/delete tombstones and their allowlisted audit
   events. A failed staging or integrity check leaves the prior live database
   in place.
4. Restart the application in recovery mode. Users must log in again. Normal
   credential creation, rotation, and dispatch remain blocked while recovery
   is required. Deleted records remain deleted.
5. For each credential the owner wants to retain, require a newly entered
   provider key and call `CredentialService.reenroll` with a protected async
   validator. The validator must verify that new key with the expected
   provider, return `True` only on success, and never log or retain the key.
   `reenroll` encrypts the newly entered key with the currently available
   OS-protected KEK and clears that row's reenrollment requirement. Failed
   validation leaves the credential unavailable.
6. Owners may delete credentials they do not want to re-enroll. Once every
   non-deleted credential is either validated and re-enrolled or deleted,
   `CredentialService.complete_recovery()` clears the global recovery gate.
   It refuses completion while any reenrollment remains pending.
7. Verify the owner status list, recovery flag, preserved revoke/delete audit
   events, and expected credential generations before resuming normal traffic.
   Retain the restored backup under the normal encrypted-backup retention
   policy; do not restore it again after later revocations without repeating
   this runbook.

## Guarantees and limits

- A stale active row in the backup is restored as revoked and marked for
  reenrollment; its old envelope bytes are erased from the staged live database.
- A current revoke or delete tombstone overrides an older backup row. Deleted
  credentials cannot be re-enrolled; a replacement must be a new credential.
- Sessions from the backup are not trusted and must be reissued after login.
- Re-enrollment needs the OS-protected KEK to be available. If it is missing,
  recovery fails closed; users must restore the OS key-store profile or create
  a new credential after an authorized key-store recovery. There is no cloud
  escrow or plaintext backup.
- Restore is an offline maintenance operation. Backup scheduling, backup
  encryption, retention, and key-store profile backup remain operator-owned.
