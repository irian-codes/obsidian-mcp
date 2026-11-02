## ADDED Requirements

### Requirement: Explicit empty-tree deletion
The server SHALL expose `delete_empty_folder_tree(path)` as a closed-world,
destructive write tool requiring write permission. It SHALL use existing vault,
rate, concurrency and usage guards and confirm the selected root before each
directory removal. It SHALL not accept a permanent toggle or file hash.

#### Scenario: Nested directory-only tree
- **WHEN** the selected tree contains only visible empty directories
- **THEN** its descendants and selected directory are permanently removed
- **AND** ancestors and siblings remain, with no trash entry or file deletion

#### Scenario: Entire-tree preflight
- **WHEN** a file, hidden entry, symlink, unreadable directory, unsafe path or
  more than 1,000 directories is encountered
- **THEN** preflight refuses before removing any directory

### Requirement: Kernel-enforced emptiness and truthful progress
Every removal SHALL use `rmdir` through a freshly anchored parent, after comparing
the directory's planned identity. Nonempty directories SHALL never be recursively
deleted or moved to trash. Interrupted operations SHALL report actual removals
as partial work. An absent selected tree SHALL be an idempotent success.

#### Scenario: File arrives after preflight
- **WHEN** another writer adds a file immediately before a directory removal
- **THEN** the kernel refuses that removal and the file remains unchanged
- **AND** earlier directory removals, if any, are reported as partial work

#### Scenario: Reassignment interrupts cleanup
- **WHEN** the selected root assignment changes between directory removals
- **THEN** further removals are refused and earlier removals are reported

#### Scenario: Retry after interruption
- **WHEN** a caller retries against the selected path
- **THEN** the current tree is preflighted again, with no file deletion
- **AND** an already absent tree succeeds without another mutation
