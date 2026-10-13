# Working on this repository

## Read first

- `docs/requirements.md` restates the challenge brief as checkable requirements. Read it before planning, implementing or reviewing anything, and use its checklist at the end of each plan.
- The README's decision log records client answers and documented assumptions. When the brief is ambiguous, the decision log is the answer.

## Rules

- **Never run git write commands** (`add`, `commit`, `push`, `merge`, `rebase`, `reset`, `checkout` of files). Propose a commit plan with the files and messages; the owner runs it.
- **Never create, modify or delete cloud resources.** Write the commands or scripts; the owner reviews and runs them.
- **Plan first** for any new phase: propose the plan and wait for approval before writing code.
- **Do not touch the backend, the rules engine or the decision log silently.** If a change outside the agreed scope is needed, say so before doing it.
- **No secrets and no real data in the repository.** The API key lives only in `.env` locally and in the secret store in the cloud. Reference data, test invoices, uploads and cached model responses stay under the git-ignored `data/` folder.
- **Do not touch the owner's local database or uploads** (`backend/contoso.db`, `data/uploads/`). Manual checks run against a temporary database and folder, with extraction served from the cache.
- **Report real results.** Give the actual test counts, and say plainly what was not verified (for example, anything not seen in a real browser).
- Every new assumption goes into the decision log, with its status: `closed` with a source when the client confirmed it, `open` otherwise.

## Environment

- Windows with Git Bash. In Git Bash, a value starting with `/` on the command line is converted to a Windows path; put such values in `.env` files instead.
- Backend: Python, `backend/`, virtual environment in `backend/.venv`. Tests: `.venv/Scripts/python -m pytest -q` from `backend/`.
- Frontend: Next.js, `frontend/`. Checks: `npx tsc --noEmit` and `npx vitest run` from `frontend/`.