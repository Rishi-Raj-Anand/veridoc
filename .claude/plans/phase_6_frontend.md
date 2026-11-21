# Phase 6 — Frontend (Milestone 7) + landing page + funny 404

| | |
|---|---|
| **Status** | 🟡 **6.1–6.8 built 2026-11-21; 6.9 browser walkthrough still open** |
| **Branch** | `feature/phase-6-frontend` (created from `main` at `f982b8d`) |
| **Tasks** | 6.0 housekeeping, 6.1–6.6 as listed in §17, **6.7 `/home` landing page**, **6.8 funny 404 page**, 6.9 live gate |
| **Touches** | `frontend/` and `CLAUDE.md` (§5, §10, §17). Backend and AI service are unchanged, and the §9 contracts are not modified |
| **New deps** | **None.** `react-markdown`, `react-router` and TanStack Query are already installed. Tests use `fireEvent` and do not add `user-event` or `jest-dom` |
| **Record** | Copy this plan to `.claude/plans/phase_6_frontend.md` at 6.0 and fill in the results at the end, the same convention as Phases 0–5 |

## Context

Phases 0–5 delivered a working backend and AI pipeline, but the frontend is still Phase 0 placeholders: four stub pages, a dev health strip, `/` redirecting to `/chat`, and a plain "Page not found." Phase 6 builds the real UI described in §10. You also asked for two additions:
- A public **`/home` landing page** branded "VERIDOC AI", with a **"Try out AI Doctor"** call to action that leads into the app.
- A custom, **funny 404 page**.

**Reused as-is or extended:**
- `src/api/client.ts`: `request<T>`, `ApiError` and `setAuthTokenProvider`.
- `src/api/health.ts` and `HealthBanner`, which move into the admin page.
- `main.tsx` providers.
- The `App.test.tsx` render helper pattern (stubbed `fetch`, `MemoryRouter`, `QueryClient` with `retry: false`).

## Decisions

| Question | Decision |
|---|---|
| Route map | Routes split into public, signed-in and fallback groups:<br>• Public: `/home` is the landing page, and `/` redirects to `/home`. `/login` and `/register` are also public; a signed-in visitor is sent to their default page.<br>• Signed in: `/chat` is open to any signed-in user, admin included (§9). `/admin` is admin only, and a plain user is redirected to `/chat`.<br>• Fallback: `*` shows the 404 page. |
| "Try out AI Doctor" | A link to `/chat`. A signed-out visitor is guarded to `/login` with `state.from`, and after login returns to `/chat`. |
| Layouts | The landing page and the 404 page are full-bleed, standalone pages. `/login`, `/register`, `/chat` and `/admin` share an `AppLayout` (a layout route with `<Outlet>`). Its header shows the logo (linking to `/home`), Chat, Admin (admin only), the user's email and Log out. |
| Default page per role | `admin` goes to `/admin`; `user` goes to `/chat`. |
| Auth storage | `{token, role}` in `localStorage` under `veridoc.auth`, per §10, with every access wrapped in try/catch. The email comes from `GET /api/auth/me` through TanStack Query. |
| 401 handling | `client.ts` gains `setUnauthorizedHandler`. A 401 on a request that **carried a token** clears auth and goes to `/login`. A failed login, which has no token, just shows its error. |
| Register | On 201, the page logs in automatically with the same credentials and lands on `/chat`. |
| 422 messages | FastAPI's `detail` for a 422 is a list. `errorDetail` joins the `msg` fields so the user sees "String should have at least 8 characters" rather than "Unprocessable Entity". |
| Uploads | `request()` accepts a `FormData` body. It sends the body as-is and does not set `Content-Type`, so the browser sets the multipart boundary. |
| Emergency banner | Phase 5 prefixes the answer with `EMERGENCY_NOTICE + "\n\n"`. When `emergency=true`, the UI splits off that first paragraph and renders it in the red `role="alert"` banner, then renders the rest as the answer. The notice text is not duplicated in the frontend, and the user does not see it twice. |
| Disclaimer | Every answer shows `response.disclaimer` from the API. The **persistent** chat banner has to render before the first reply, so its text is one frontend constant in `src/constants.ts`. It is recorded in CLAUDE.md as UI copy that mirrors `ai_service/config.py`. The existing "one place" test covers only the AI service. |
| Markdown | `react-markdown` with `skipHtml`, so raw HTML is never rendered. Lists and paragraphs are styled with Tailwind arbitrary variants, without the typography plugin. |
| Chat session | `sessionId = useState(() => crypto.randomUUID())`, held in React state only, so a refresh starts a new session. Messages live in local state. A send is a `useMutation`, and input is disabled while one is pending, because turns are serialised server-side. |
| Thinking indicator | Shows "Thinking…" with animated dots and a gentle note that answers can take up to a minute (§6.8: 30–90 s). No agent states and no scores (§1). |
| Fallback answers | Rendered like any answer, with a muted amber accent. `status` is not shown as a word, and no scores or iteration counts appear. |
| Health strip | Removed from the global header and shown as a "System status" card at the top of `/admin` only. |
| Fonts and assets | System font stack and inline SVG only, with no external fonts or CDNs. Everything runs locally. |

---

## 6.0 Housekeeping
- ✅ Created `feature/phase-6-frontend` from `main` (`f982b8d`).
- ✅ Copied this plan to `.claude/plans/phase_6_frontend.md`.

## 6.1 `src/api/` — typed calls
- **`client.ts`:**
  - `FormData` body support.
  - `setUnauthorizedHandler`, called on a 401 when a token was sent.
  - `errorDetail` handles a list-form `detail`.
- **`auth.ts`:**
  - Types: `Role`, `TokenResponse`, `User`.
  - Calls: `register`, `login`, `getMe`.
- **`chat.ts`:**
  - Types: `Citation`, `ChatResponse` (exactly as in §9), `ChatMessageRequest`.
  - Calls: `sendMessage`.
- **`admin.ts`:**
  - Types: `DocumentStatus`, `DocumentItem` (mirroring `backend/app/schemas/document.py`).
  - Calls: `listDocuments`, `uploadDocument(file)`, `deleteDocument(id)`.

## 6.2 `context/AuthContext.tsx` + guards
- **`AuthProvider`** exposes `{token, role, isAuthenticated, login(email, pw), logout()}`.
  - It hydrates from `localStorage`.
  - It wires `setAuthTokenProvider` and `setUnauthorizedHandler`.
  - On logout it clears the query cache.
- **`hooks/useAuth.ts`** provides access to the context.
- **`components/RequireAuth.tsx`** takes an optional `role="admin"` and renders a `<Navigate>` to `/login` with `state.from`, or to `/chat` for a user who lacks the role.
- **`components/RedirectIfAuthed.tsx`** wraps the login and register pages.
- **`main.tsx`** wraps `<App/>` in `AuthProvider`, inside `BrowserRouter` and `QueryClientProvider`.

## 6.3 `LoginPage`, `RegisterPage`
- Each page is a centred card with email and password fields, a submit button showing a pending state, an inline error area (`role="alert"`), and a link to the other page.
- Register validates `password.length >= 8` on the client to mirror the backend, then auto-logs in.

## 6.4 `ChatPage` + components
- **Layout:**
  - The persistent disclaimer banner sits at the top.
  - The message list scrolls and auto-scrolls to the newest message.
  - A sticky input sits at the bottom: a textarea where Enter sends and Shift+Enter adds a newline, with a counter for the 2,000-character limit.
- **Empty state:** "Ask Doctor AI about your uploaded references", with 2–3 example prompt chips.
- **`components/chat/`:**
  - `MessageBubble` (user/assistant).
  - `AnswerBody` (`react-markdown` with `skipHtml`).
  - `CitationList` ("[1] disease.md · p. 1").
  - `EmergencyBanner` (red, `role="alert"`).
  - `ThinkingIndicator`.
- **Errors:** a 503 or network failure shows the backend's friendly `detail` as an error bubble with a **Retry** button. A 401 is handled globally.

## 6.5 `AdminPage`
- **System status card:** the existing `HealthBanner`, restyled.
- **Upload:** a file input with `accept=".pdf,.txt,.md,.docx"`, a client-side extension check, and an upload `useMutation`. The server's 415/413/409/422 `detail` is shown inline. On success the list query is invalidated.
- **Document table:**
  - Columns: filename, size, status badge (pending with a spinner, ready in green, failed in red), chunks and uploaded time.
  - A `failed` row shows its `error` text below.
  - `useQuery` with `refetchInterval: rows.some(pending) ? 2000 : false`.
- **Delete:** a two-step inline confirmation ("Delete" → "Confirm" / "Cancel"). It is disabled while a row is `pending` (the backend returns 409). A 503 shows its detail.

## 6.6 Quality checks + tests
Tests stub `fetch` with a small route-matching helper in `src/test/fetchMock.ts`:
- **`api/client.test.ts`:**
  - A 401 with a token calls the handler; one without a token does not.
  - A list-form 422 detail is joined.
  - A `FormData` body has no JSON `Content-Type`.
- **`App.test.tsx`** (rewritten):
  - `/` reaches `/home`.
  - The landing page shows "VERIDOC AI" and a "Try out AI Doctor" link to `/chat`.
  - Signed out, `/chat` goes to `/login`.
  - A user visiting `/admin` goes to `/chat`.
  - An admin sees `/admin`.
  - An unknown path shows the 404 page.
- **`pages/ChatPage.test.tsx`:**
  - Sending shows "Thinking…", then the answer, citations and disclaimer.
  - With `emergency=true`, the notice is in the alert and not in the body.
  - `<img onerror>` in an answer is not rendered as HTML.
  - A 503 shows the friendly text and a Retry button.
- **`pages/LoginPage.test.tsx`:** wrong credentials show an error, and success routes by role.
- **`pages/AdminPage.test.tsx`:**
  - Rows render with their status.
  - A failed row shows its error.
  - The two-step delete issues a `DELETE`.
- Finish with `npm run lint ; npm run typecheck ; npm run test ; npm run format:check`, all green.

## 6.7 `/home` landing page — `pages/HomePage.tsx`
- **Hero:**
  - A large **VERIDOC AI** wordmark with a subtle gradient.
  - The tagline "Medical answers you can trace back to the source."
  - The primary CTA **"Try out AI Doctor"** links to `/chat`, with a secondary "Sign in" or "Create account" (or "Go to Admin" for the admin).
- **"How it works"** has four steps with inline SVG icons, the §6.2 loop in plain words:
  1. Retrieve from your references.
  2. The Generator drafts with citations.
  3. The Verifier audits every claim.
  4. Escalate or answer safely.
- **Feature cards:**
  - Cited answers only.
  - Runs fully on local AI (Ollama, no cloud).
  - Emergency red-flag detection.
  - Refuses to guess.
- **Disclaimer strip and footer:** the project credit (B.Tech CSE Major Project, the authors and the mentor from §1).
- **Layout:** responsive at phone width. The navbar shows the logo and Sign in / Chat depending on auth.

## 6.8 Funny 404 — `pages/NotFoundPage.tsx`
A medical "case file" for a missing page:
- **Visuals:** a large "404" over an **animated flatlining EKG line**. Inline SVG, with a CSS `stroke-dashoffset` keyframe in `index.css`; it beeps a few times, then goes flat.
- **Heading:** "Diagnosis: Acute Page-Not-Found-itis".
- **Chart card:**
  - Patient: `{pathname}`
  - Symptoms: "Clicked a link. Saw nothing. Felt lost."
  - Verifier audit: "faithfulness 0.00 · relevance 0.00 · verdict: fail ❌"
  - Escalation: "Searched Top-3, Top-8, Top-15. Still nothing."
  - Prescription: "Take one click on the button below and call us in the morning."
- **Fine print:** "Side effects may include: finding what you were looking for."
- **Buttons:** "Take me home" (`/home`) and "Ask Doctor AI instead" (`/chat`).
- Jokes stay on the page itself. Nothing jokes about real emergencies or illness.

## 6.9 Live gate + docs
- Start all three services (§12) and `npm run dev`, then check in the browser:
  - `/` loads the landing page, and the CTA leads to login, then `/chat`.
  - Register and auto-login work.
  - A question about `disease.md` returns a `verified` answer with citations and a disclaimer.
  - A red-flag message shows the red banner.
  - An off-corpus question gets the fallback text.
  - Admin login leads to `/admin`: upload goes from `pending` to `ready`, and delete works.
  - A plain user cannot reach `/admin`.
  - Refresh starts a fresh session.
  - A random URL shows the 404.
- **`CLAUDE.md` updates:**
  - §5 page list: `HomePage`, `NotFoundPage`.
  - §10 routes: `/home` landing, `/` → `/home`, the 404 page, the admin health card, and the disclaimer constant.
  - §17: 6.0/6.7/6.8/6.9 rows, a Phase 6 **Gate** line, and the ✅ status block.
- **Commits:** conventional commits per task group (`feat: typed api + auth context (6.1-6.2)` and so on), then `docs:` for the gate. No merge to `main` unless you ask.

## Critical files
- **Modified:**
  - `frontend/src/App.tsx`, `main.tsx`, `index.css`
  - `api/client.ts`
  - `components/HealthBanner.tsx`
  - `pages/LoginPage.tsx`, `RegisterPage.tsx`, `ChatPage.tsx`, `AdminPage.tsx`
  - `App.test.tsx`
  - `CLAUDE.md`
- **New:**
  - `api/auth.ts`, `chat.ts`, `admin.ts`
  - `context/AuthContext.tsx`
  - `hooks/useAuth.ts`
  - `components/RequireAuth.tsx`, `RedirectIfAuthed.tsx`, `AppLayout.tsx`, `chat/*`
  - `pages/HomePage.tsx`, `NotFoundPage.tsx`
  - `constants.ts`
  - `test/fetchMock.ts` and the page tests

## Verification
1. In `frontend/`, run `npm run lint ; npm run typecheck ; npm run test ; npm run format:check`.
2. In `backend/` and `ai/`, run `uv run ruff check . ; uv run pytest`. These should be unchanged, as a sanity check that nothing else moved.
3. Run the 6.9 live walkthrough in a browser against real services, and record the timings and outcomes in the plan record.

---

## Results

**Built (commit `d037ff8`):** 6.1–6.8. Frontend only; `backend/` and `ai/` unchanged, no new dependencies.

**Automated checks:** 28 frontend tests in 6 files pass (client, routing, login/register, chat, admin). ESLint, `tsc --noEmit`, `prettier --check` and `vite build` are clean (442 kB JS, 137 kB gzip).

**Read-only checks against the running services** (Vite 5173, backend 8000, AI 8001, Ollama up, real `data/`):
- Vite compiled `main.tsx` and the Home, NotFound, Chat and Admin pages (200).
- `/api/health` was `ok`, with both models present and Qdrant open.
- The CORS preflight from `http://localhost:5173` allowed `content-type,authorization`.
- A wrong password returned 401 `{"detail":"incorrect email or password"}`.
- A short password returned a list-form 422, the shape `errorDetail` joins into readable text.
- Chat and admin routes without a token returned 401.

**Not done: 6.9 browser walkthrough.** It has to be driven in a real browser, and it writes to the real `data/`: a registered user, an uploaded and deleted document, and chat transcripts. Checklist for the person running it:
1. `/` lands on `/home`. "Try out AI Doctor" goes to login, then `/chat`.
2. Register logs in automatically.
3. An in-corpus question gives a cited answer with the disclaimer.
4. A red-flag message ("I have chest pain") shows the red banner, once.
5. An off-corpus question shows the fallback.
6. Admin: upload goes `pending` → `ready`, then delete with confirmation.
7. A normal user opening `/admin` lands on `/chat`.
8. Refresh starts a new session.
9. An unknown URL shows the 404 page.
10. Check the landing page and 404 at phone width.

## Deviations from the plan
- None to scope. The planned `test/fetchMock.ts` became `test/helpers.tsx`, which also holds `renderApp`.
