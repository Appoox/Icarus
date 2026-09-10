# Repository Guidelines & Operational Rules

## 🛑 STRICT VERSION CONTROL CONSTRAINTS (NON-NEGOTIABLE)

1. **NO GIT COMMIT OR PUSH**:
   - You are strictly forbidden from executing `git commit`, `git push`, or `git add`.
   - Do NOT attempt to create new git branches, rebase, or alter git history.

2. **FILE-BASED `.patch` PROTOCOL**:
   - Write ALL code changes—including modifications, brand-new files, and file deletions—directly to a file named `changes.patch` in the repository root directory using file-writing tools.
   - Do NOT output raw file contents or rely solely on chat markdown code blocks when making edits; the patch content must be written directly to `changes.patch` on disk.

3. **NEVER SUBMIT CHANGES TO GITHUB**:
   - Do NOT open pull requests, push branches, or otherwise submit anything to GitHub.
   - In addition to writing `changes.patch`, always reproduce the full patch inline in the chat reply. The user applies and tests it themselves.

4. **PATCHES ARE INCREMENTAL WITHIN A CHAT**:
   - Every patch after the first one builds on the changes already made earlier in the same conversation — never regenerate it against the pristine repository.
   - Keep the working tree edits from earlier turns in place so each new `changes.patch` is a cumulative diff against the original checkout, with the later work stacked on top of the earlier work.
