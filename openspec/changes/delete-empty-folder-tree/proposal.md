# Delete a selected empty folder tree

Agents can list folders but neither file deletion tool accepts directories.
Add `delete_empty_folder_tree(path)` for explicit cleanup of a tree containing
directories only. Empty directory entries have no content to recover, so this
operation permanently removes them without creating trash entries.

Existing file/note deletion, trash policy, indexes, deployed images and vaults
are unchanged. No automatic pruning follows another write.

Implementation plan and progress live in `tasks.md`; safety decisions and
accepted concurrency limits live in `design.md`.
