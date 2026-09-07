# Expense Approval

An MVP service for approving internal expense claims. An employee submits a
reimbursement claim; it is **automatically routed to the right approver based on
the expense category**; the approver approves or rejects it (**a rejection
requires a comment**); the submitter tracks their claims' status in real time.

When an approver opens a claim, an **AI helper** produces a one‑to‑two sentence
summary and **flags the claim when the amount, category and description look
inconsistent** (e.g. category *Office* but a description about a flight to
London). The AI is **advisory only** — it never decides a claim and never blocks
the approver, and the whole app keeps working if the AI service is missing,
slow, or down.

> **Stack:** FastAPI · SQLAlchemy 2.0 + PostgreSQL · Alembic · Jinja2 + HTMX ·
> Anthropic Claude (advisory) · pytest. Server‑rendered, no SPA.

---

## Quick start (Docker)

```bash
cp .env.example .env          # optionally set ANTHROPIC_API_KEY to enable the AI helper
docker compose up --build
```

Open **http://localhost:8000**. On start‑up the `web` container applies
migrations (`alembic upgrade head`, revisions `0001 → 0007`) and seeds idempotent
demo data (`python -m app.seed`), then serves the app.

### Demo accounts — password `demo1234`

| Email | Roles | Approves categories |
|-------|-------|---------------------|
| `alice@example.com` | employee **+** approver | Office, Software/Subscriptions, Other |
| `carol@example.com` | employee **+** approver | Travel, Client Entertainment |
| `bob@example.com`   | employee only | — |

The seed also creates demo claims from Bob, including one intentionally
**inconsistent** one (*Office, "round‑trip flight to London"*) so you can log in
as Alice, open it, and see the AI summary and the inconsistency flag.

---

## The required functionality (task spec) — where each part lives

| Requirement | Implementation |
|-------------|----------------|
| **Two roles, combinable** (employee / approver; one person can be both) | `User.is_employee` / `is_approver`; Alice & Carol have both |
| **Submit a claim** — amount (USD), category, description, expense date, payment details | `routers/claims.py` → `services.create_claim` |
| **Category‑based routing** | `app/routing.py` (`resolve_approver`) — approver resolved at submit time and **snapshotted** on the claim, so later routing changes don't silently reassign live claims |
| **Statuses** `pending / approved / rejected / withdrawn` | `app/enums.py`; transitions only in `services.py` |
| **Withdraw only while pending** | `services.withdraw_claim` |
| **Approver queue** (only claims routed to them) + approve / reject | `routers/approvals.py` |
| **Rejection requires a comment** | `services.reject_claim` — empty comment refused, claim stays pending |
| **Scoping** (not everyone sees everything) | `services.can_view` + `require_approver`; enforced **server‑side** (403), not just hidden in the UI |
| **AI review** (summary + inconsistency flag, mandatory) | `app/ai/service.py` (`generate_review`), rendered lazily when an approver opens a claim |
| **AI advisory + graceful degradation** | never approves/rejects; on no key / timeout / error → `status="unavailable"`, the approver flow is unaffected |
| **Audit trail** | append‑only `AuditLog`, one row per action |

### AI graceful degradation (how it stays non‑blocking)

- Called **lazily** — only when an approver opens a claim — so submitting and
  deciding never wait on it.
- With **no API key**, or on **timeout / network / API error / unparseable
  output**, the review returns `status="unavailable"` and the UI shows a neutral
  "AI review unavailable" panel. Claims still submit, route, and get decided.
- The AI never approves or rejects; a flag is a hint, not a veto. A successful
  review is cached on the claim; a failed one is not, so a later view retries.

---

## Configuration

Environment variables (see [`.env.example`](.env.example)):

| Variable | Default | Purpose |
|----------|---------|---------|
| `SECRET_KEY` | `change-me-in-production` | Signs the session cookie. Generate: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | `postgresql+psycopg2://expense:expense@db:5432/expense` | SQLAlchemy database URL |
| `ANTHROPIC_API_KEY` | *(empty)* | Enables the AI helper. **Empty ⇒ AI runs in degraded mode.** Never commit it. |
| `AI_MODEL` | `claude-haiku-4-5` | Model for the summary + flag. A lightweight task — Haiku is fast and cheap and sufficient. |
| `AI_TIMEOUT_SECONDS` | `12` | Hard timeout for a single AI call |
| `AI_AUTO_CATEGORIZE` | `true` | *Extra beyond the spec* — on submit, if the AI confidently flags a wrong category it also auto‑corrects and re‑routes. Set `false` for the pure "flag only" behaviour. |

---

## Running the tests

The suite runs on a throwaway **SQLite** database and needs neither Postgres nor
an API key.

```bash
pip install -r requirements.txt
pytest            # 36 passed
```

Covers: category routing · full lifecycle · **rejection needs a comment** ·
withdraw‑only‑pending · **server‑side scoping (403)** · only the assigned
approver decides · **AI degradation** (no key ⇒ unavailable, submit/decide still
work) · audit trail growth · plus the report and receipt features below.

> **End‑to‑end verification:** a spec‑by‑spec check (core 21/21 and AI 3/3,
> including the exact *"Office + flight to London"* flag example and graceful
> degradation) passed. The full `alembic 0001 → 0007` chain was verified to build
> a fresh database cleanly, seed, and serve.

---

## Local development (without Docker)

Requires Python 3.11+ and (for the Alembic path) a PostgreSQL. For a quick
SQLite run you can also use `python run_local.py`.

```bash
pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg2://USER:PASS@localhost:5432/expense"
export SECRET_KEY="dev-secret"
export ANTHROPIC_API_KEY="sk-ant-..."   # optional, enables the AI helper
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload
```

---

## Beyond the required MVP (extras)

Built on top of the core, in the spirit of a real expense product (all still
server‑side‑scoped and audited):

- **Reports** — bundle several expenses into one report with a full lifecycle
  `draft → submit → approved / rejected → paid` (+ retract); a submitted report
  goes to the shared approval queue and **any approver** can decide it (mixed
  categories allowed); group‑by‑category with subtotals, activity feed, and a
  **multi‑select bar** on the list (submit / duplicate / export‑to‑PDF / delete).
- **Receipts** — attach **several** receipts per expense (drag‑and‑drop),
  preview / remove, and an **AI receipt scan** that pre‑fills amount, merchant,
  date, description, category **and currency** from the image/PDF.
- **UX** — Expensify‑style layout, slide‑out drawer, custom dropdowns and a
  custom date picker, saved filters, CSV export, currency selection.

---

## Design notes

- **Statuses are canonical and minimal**, matching how the expense‑management
  industry models the claim life cycle.
- **Routing is a single seam** (`app/routing.py`): today category → approver;
  amount thresholds or multi‑level chains can be added there without touching the
  submit flow.
- **Authorization lives in the backend** (dependencies + service layer); the UI
  only reflects what the backend already enforces.
- **Audit is append‑only** — rows are inserted, never mutated.
- **AI output is stored separately** from the claim's own fields, so a model
  failure can never corrupt the claim record.
- **Migrations are portable** — the FK‑bearing `ALTER`s use Alembic batch mode,
  so `alembic upgrade head` runs on both PostgreSQL and SQLite.

## Out of scope (deliberately)

Corporate cards, actual disbursement (ACH/SEPA), travel booking, deep
accounting/ERP integrations, **currency conversion** (amounts are stored in their
entered currency), SSO/SAML, and no‑code approval‑workflow builders. Paying the
money out is assumed to happen outside the system.

---

## Project structure

```
app/
  main.py            # FastAPI app, middleware, route wiring, /healthz
  config.py          # settings from environment (.env)
  database.py        # engine, session, declarative base
  models.py          # User, Category, ExpenseClaim, AuditLog, Report, Receipt, SavedView
  enums.py           # ClaimStatus, ReportStatus, AuditAction
  security.py        # password hashing (pbkdf2_sha256)
  dependencies.py    # current user, require_user, require_approver
  routing.py         # category -> approver routing (the extension seam)
  services.py        # claim & report operations (the only place status changes)
  ai/service.py      # advisory AI review + receipt scan, with graceful degradation
  routers/           # auth, claims, approvals, reports, assistant
  templates/         # Jinja2 + HTMX views
  static/            # CSS
alembic/versions/    # migrations 0001 → 0007
tests/               # pytest suite (SQLite, no Postgres / no key needed)
```

## Hosting

`docker compose up --build` is production‑shaped (Postgres + migrations + seed).
For a public host (e.g. **Railway** or **Render**):

1. Set env vars: `DATABASE_URL` (managed Postgres), a strong `SECRET_KEY`,
   `ANTHROPIC_API_KEY`, `AI_MODEL=claude-haiku-4-5`.
2. The container's entrypoint runs `alembic upgrade head` + seed on boot.
3. **Receipts** are written to `uploads/` on the container filesystem — mount a
   **persistent volume** at `/app/uploads` (or switch to object storage) so they
   survive redeploys.
```
