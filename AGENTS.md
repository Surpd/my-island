# My Island project identity

This repository is **My Island**, the school Telegram Mini App and admin system.
It is not IslandQuiz, Island Quiz, Island Kingdom, or any similarly named project.

## Hosting and release path

- The frontend is hosted and published through GPT Sites. GPT Sites is the production frontend destination.
- The backend/API is hosted on Render. Render is not the frontend deployment target.
- Git is used as the source-code/version-storage path for GPT Sites; a Git push alone is not a frontend deployment.
- For frontend releases, verify the exact GPT Sites project/account against `.project-identity.json`, update its configured source, then save and deploy a GPT Sites version. Do not substitute a GitHub Pages, mirror-hosted, or Render frontend deployment.
- Keep the project identity separate from IslandQuiz, Island Quiz, and Island Kingdom.

Before any external action (Render deploy/config/log access, Supabase migration,
Sites publish, GitHub push, Google resource access), verify the target against
`.project-identity.json` and report the exact account/workspace ID, service ID,
repository, and public domain. A display name such as `Dmitrii's workspace` or a
similar service name is not sufficient proof.

The approved Render target is recorded under `approvedExternalTargets`. All of
its exact identity fields must agree with the connector response; a partial name
match is not enough.

Never use a target listed under `forbiddenExternalTargets`. If a connector only
exposes a forbidden account or workspace, stop and ask the user to reconnect the
correct account. Do not deploy, restart, migrate, or probe the forbidden target.

Production frontend builds must receive an explicit `VITE_API_BASE_URL`. Never
restore a hard-coded production API fallback.
