# Design

- Domain: an empty folder tree contains only visible directories, with no files,
  symlinks or other entries. Hidden entries refuse the operation; they are never
  omitted from emptiness checks. The vault root and ancestors outside the selected
  subtree cannot be selected for deletion.
- Preflight the entire tree before mutation. Use fresh beneath-root directory
  lookups from one root descriptor, record directory device/inode identities,
  and bound the plan to 1,000 directories (including the selected folder).
  This is a per-operation traversal cap, not an infrastructure resource budget.
- Remove recorded directories deepest first. Reopen each parent beneath root,
  compare the directory identity, then issue only `rmdir`, never file unlink,
  recursive deletion or rename-to-trash. The kernel refuses a nonempty directory
  even when preflight was wrong or a writer added content afterwards.
- Apply existing write permission, both rate buckets, write concurrency class,
  vault admission, usage attribution and a fresh single-use root confirmation
  for every directory removal. No file hash precondition applies: no file bytes
  can be removed. Existing `delete_file` directory refusal remains intact.
- Success reports the number removed. An already absent selected tree is a
  successful no-op. A failure after removals is a typed partial outcome naming
  the count and stopping point, never a claim that nothing changed. A fresh
  retry revalidates the remaining tree rather than replaying a cached plan.
- Accepted limits: the tree operation is not atomic and does not lock Sync or
  other writers. Device/inode comparisons are optimistic; substitution after
  the last check can remove an empty replacement directory, but cannot remove
  files. As with existing anchored writes, containment holds when each parent
  descriptor resolves, not after a concurrent rename of that parent (D26).
  A concurrent administrator reassignment after its confirmation retains the
  existing one-syscall optimistic window. Directory flush failures are logged
  without turning a completed removal into a false failure.
- Validation uses synthetic trees only. Exercise ordinary/hidden files,
  symlinks, root/traversal refusal, unreadable and oversized trees, replacement,
  a file arriving immediately before `rmdir`, interrupted cleanup/retry,
  reassignment and protocol metadata/admission. Production adoption is separate.
