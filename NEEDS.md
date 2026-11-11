# What Martin needs to provide / approve

| # | Item | Why | Secret? Where it lives |
|---|------|-----|------------------------|
| 1 | **OK to create a PUBLIC GitHub repo** (e.g. `ripandgo/distributor-watch`) under the connected account **ripandgo** | Public = free unlimited Actions minutes. It holds only the public-site scanner code and the public catalog snapshots. | Not secret. No passwords or personal data in the repo. |
| 2 | **A way to create and push the repo.** The Grok Bot GitHub connector can read but has no "create repository" tool, and `gh` on the box is not signed in. Either Martin creates an empty repo on github.com while signed in as ripandgo (2 min), or he runs `gh auth login` on the box once. | So the code and workflow can be pushed. | The GitHub login stays with GitHub (Martin signs in himself). |
| 3 | **2-Step Verification on ripndgo@gmail.com**, then a **Google App Password** (myaccount.google.com → Security → App passwords, name it "distributor watch"). Martin does this himself. The bot never handles his Google password. | Read-only IMAP access to All Mail + Trash for the distributor senders. Mail is opened read-only, so nothing gets marked read, moved, or deleted. | **Secret.** GitHub Actions secret `GMAIL_APP_PASSWORD` (plus `GMAIL_USER` = ripndgo@gmail.com). Can be revoked anytime from the same Google page. |
| 4 | **OK to create the webhook-triggered routine** and pause the current cron one at switch-over (after the test day passes) | Wakes the bot only on real changes. | The webhook **URL** and **sender key** are copied by Martin from the routine panel into the GitHub secrets `WATCH_WEBHOOK_URL` / `WATCH_WEBHOOK_TOKEN`. The bot never sees the key. |
| 5 | (Later, optional) **Discord webhook link** for Go's news-feed channel | Only if the TCG news feeds move to GitHub too. | **Secret.** GitHub Actions secret `DISCORD_WEBHOOK_URL`. |

Rules:
- Secrets go only in **GitHub → repo → Settings → Secrets and variables → Actions**,
  never in the repo, a commit, an issue, or chat.
- `.gitignore` already excludes `.env`, `out/` and `test_state/`.
- Public in the repo: code, the workflow, and `state/` (snapshots of the
  public distributor catalogs only).
- NOT in the repo: the ASM inventory PDF and its parsed dealer prices, plus the
  Gmail message ids already seen. These live in `state/private/` (gitignored),
  carried between runs in the GitHub Actions cache. Only the repo owner can
  read that cache. If the cache is evicted, the next run quietly rebaselines
  Gmail and ASM and reports nothing for that one run.
- The ASM fixture PDFs in `tests/` are gitignored too. They stay on the box and
  don't get pushed.
