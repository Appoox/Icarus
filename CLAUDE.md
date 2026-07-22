# Repository Guidelines & Operational Rules

## 🛑 STRICT VERSION CONTROL CONSTRAINTS (NON-NEGOTIABLE)

1. **NO GIT COMMIT OR PUSH**:
   - You are strictly forbidden from executing `git commit`, `git push`, or `git add`.
   - Do NOT attempt to create new git branches, rebase, or alter git history.

2. **FILE-BASED `.patch` PROTOCOL**:
   - Write ALL code changes—including modifications, brand-new files, and file deletions—directly to a file named `changes.patch` in the repository root directory using file-writing tools.
   - Do NOT output raw file contents or rely solely on chat markdown code blocks when making edits; the patch content must be written directly to `changes.patch` on disk.
