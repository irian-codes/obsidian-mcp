### delete_empty_folder_tree_impl — directory-only cleanup

- Success: complete selected empty-tree removal or already-absent no-op.
- Refused: write permission/admission, unsafe or hidden path, files/symlinks,
  oversized/unreadable preflight, identity conflict, kernel refusal or changed
  assignment before any removal. No removals are reported.
- Partial: a later confirmation or removal fails after some directories were
  removed. Count and stopping point remain in the response. The typed outcome
  retains its refusal code and never carries `nothing_written`.
- Files are never deleted, renamed or put in trash; only `rmdir` is issued.
