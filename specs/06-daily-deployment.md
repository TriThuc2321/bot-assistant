# Spec 06 — Daily Deployment & Logs

Worth **15 points**. Needs: runs daily, on a public platform, with a **linkable** log or last-run artifact.

## Option A (recommended): GitHub Actions scheduled workflow
Free, zero servers, public logs if the repo is public, artifacts downloadable.

`.github/workflows/daily.yml`
```yaml
name: daily-kb-sync
on:
  schedule:
    - cron: "0 2 * * *"      # 02:00 UTC daily
  workflow_dispatch: {}       # manual trigger for demo
jobs:
  sync:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t kb-sync .
      - run: |
          mkdir -p artifacts
          docker run --rm \
            -e OPENAI_API_KEY="${{ secrets.OPENAI_API_KEY }}" \
            -e VECTOR_STORE_ID="${{ secrets.VECTOR_STORE_ID }}" \
            -v "$PWD/artifacts:/app/artifacts" \
            kb-sync | tee artifacts/run.log
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: last-run
          path: artifacts/
```
Notes:
- Store key + store ID as repo **Secrets**.
- Scheduled workflows can be delayed and are disabled after 60 days of repo inactivity — fine for the review window, mention it.
- Artifacts dir must be writable by the container's non-root user (create it before mounting, or `chmod 777`).
- README link: the Actions page for the workflow (`https://github.com/<you>/<repo>/actions/workflows/daily.yml`).

Is this "cloud hosting"? The brief lists DO/Railway/Render/Fly/AWS/GCP as examples ("any cloud/public hosting platform"). GitHub Actions is a hosted runner; if you want zero ambiguity, also do Option B.

## Option B: DigitalOcean App Platform Job (brief's first suggestion)
- Deploy from the GitHub repo using the Dockerfile; component type **Job**, trigger **scheduled** (cron `0 2 * * *`).
- Env vars as encrypted secrets.
- Logs are in the DO console — **not public**. Provide the last-run artifact instead: e.g., the job uploads `last_run.json` to DO Spaces (public-read) or posts it as a GitHub Gist. Or screenshot the log in the README.
- Alternatives with similar shape: Railway cron service, Render cron job, Fly Machines schedule.

## Deliverable
- [ ] At least one real scheduled run (not just manual) before submitting — trigger `workflow_dispatch` once, and let the cron fire at least once.
- [ ] README contains a working link to logs/last run.
- [ ] Log visibly contains the RUN SUMMARY line with `added/updated/skipped`.
- [ ] Demonstrate delta: show a run with non-zero `added` (first) and a later run with `skipped=N`.
