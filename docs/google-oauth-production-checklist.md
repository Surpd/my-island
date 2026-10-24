# Google OAuth: External Testing → In production

Target: existing Google Cloud project **My Island** (`my-island-507717`, project number `527485591204`), OAuth client **My Island Backend**. These identifiers come from `.project-identity.json`; the Cloud Console configuration and current verification status were not accessible/checked for this checklist.

## Local configuration facts

The server builds the consent request with `openid`, `email`, and these Google API scopes (`backend/services/google_oauth.py`, `backend/services/google_contract.py`):

- `https://www.googleapis.com/auth/spreadsheets.readonly` normally; schedule export requests the broader `https://www.googleapis.com/auth/spreadsheets`.
- `https://www.googleapis.com/auth/classroom.courses.readonly`
- `https://www.googleapis.com/auth/classroom.student-submissions.students.readonly`
- `https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly`

Thus declared OAuth scope inventory must cover both normal and schedule-export paths. Google classifies scopes in Cloud Data Access; do not assume the Sheets write or Classroom scopes are non-sensitive. Confirm each current classification and whether any is restricted in the Console [scope list](https://developers.google.com/identity/protocols/oauth2/scopes). The code does not request Classroom coursework `*.students.readonly` / write scopes listed as constants but not used in the authorization request.

The callback path is `/api/integrations/google/callback`. `.env.example` contains localhost only; the production environment value was not read. Based on the known backend public URL, the expected production callback is **`https://my-island-api.onrender.com/api/integrations/google/callback`**. Verify `GOOGLE_OAUTH_REDIRECT_URI` in the approved Render service/config and make the Cloud OAuth client's Authorized redirect URI byte-for-byte identical before using this value. Google requires an exact match and HTTPS for non-localhost web callbacks ([web-server OAuth guide](https://developers.google.com/identity/protocols/oauth2/web-server)).

The known production frontend is `https://my-island.mitya971108.chatgpt.site`. A targeted source search found no `/privacy` or `/terms` route. Google requires a public homepage describing the app with privacy-policy and optional terms links; the privacy policy must disclose access, use, storage, and sharing of Google user data. Add and verify public policy pages before claiming production readiness ([production policy](https://developers.google.com/identity/protocols/oauth2/policies), [sensitive-scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)).

## Checklist

1. In Cloud Console, select project `my-island-507717`; confirm OAuth audience is External and review App name, support/developer contacts, homepage, privacy policy, terms, enabled APIs, test users, and the Web client callback above. Check the verification state in the [Verification Center](https://console.cloud.google.com/auth/verification?project=my-island-507717).
2. Ensure homepage, privacy policy, terms, redirect URI, and any JavaScript origins use domains the project can prove it owns or is licensed to use. Google requires Search Console ownership of authorized domains by an account associated with the Cloud project ([domain verification](https://support.google.com/cloud/answer/13804266?hl=en), [policy](https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance)). `chatgpt.site` is a provider-owned shared domain, not a domain shown as owned by this project; do not assume the project can verify it. Google recommends configuring a provider to use a subdomain of a domain you own. A custom domain under project control is the straightforward path. The Render callback also introduces `onrender.com`, which is provider-owned; assess whether that domain can be authorized/verified and whether the callback can be placed on an owned domain. Console verification is not confirmed here.
3. Submit **all actually requested scopes** (including conditional Sheets write) in OAuth Data Access and Verification Center. Justify each sensitive scope with the specific product feature and why narrower access does not suffice. If Google classifies a scope as restricted, follow the additional restricted-scope review/security-assessment requirements; do not treat moving to Production as verification approval. Google says sensitive scopes need review, and production public apps requesting sensitive/restricted scopes must be verified ([scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification), [OAuth policies](https://developers.google.com/identity/protocols/oauth2/policies)).
4. Prepare the Google reviewer walkthrough/video in English: consent and app name/client ID, then show Sheets read, schedule export/write, Classroom course and submissions/material use as applicable. Explain data handling consistently with the public privacy policy. Track requests and replies in Verification Center.
5. Once the app metadata, policy links, domains, scope declarations, and review prerequisites are ready, switch publishing status from **Testing** to **In production** and submit required branding/data-access verification. Verify published/approved status in Console; production audience alone does not mean sensitive/restricted scopes are approved.

## Testing limits and transition

Google currently allows up to **100 listed test users** for Testing status. For External apps, user authorizations expire after seven days; in particular, a refresh token expires in seven days when the app is Testing and requests any scope beyond the basic identity-only set. This app requests Sheets/Classroom scopes, so plan to reauthorize during testing. The exception for only `openid`, `email`, `profile` does not apply. These are test-mode constraints, distinct from the OAuth user cap that can apply to unapproved sensitive/restricted scopes. Google documents a 100 new-user lifetime cap for users who encounter an unverified-app screen; approved sensitive/restricted scopes remove that cap for those approved scopes, but additional unapproved scopes may still trigger it. Moving to Production removes the Testing 100-tester allowlist and seven-day Testing refresh-token rule, but does not itself verify scopes or guarantee token permanence ([audience and caps](https://support.google.com/cloud/answer/15549945?hl=en), [token lifetime](https://developers.google.com/identity/protocols/oauth2#expiration)).

## Official references

- [OAuth app states and verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview)
- [Sensitive/restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)
- [Google OAuth policies](https://developers.google.com/identity/protocols/oauth2/policies)
- [Testing audience and user caps](https://support.google.com/cloud/answer/15549945?hl=en)
- [Google Search Console ownership verification](https://support.google.com/webmasters/answer/9008080?hl=en)
