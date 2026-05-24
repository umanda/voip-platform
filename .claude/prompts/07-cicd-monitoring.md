# Prompt: CI/CD Pipeline + Monitoring (Phases 7–8)

## Prerequisites
- Phase 6 complete: CDK infrastructure deployed to staging

## Task

Build the GitHub Actions CI/CD pipeline and production monitoring.

## CI/CD: `.github/workflows/`

### `ci.yml` — Runs on every PR

```yaml
name: CI

on:
  pull_request:
    branches: [main, staging]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install ruff mypy
      - run: ruff check backend/
      - run: mypy backend/app/ --ignore-missing-imports

  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_DB: galaxy_2_test
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
      redis:
        image: redis:7-alpine
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r backend/requirements.txt
      - run: pytest backend/tests/ -v --cov=app --cov-fail-under=80
        env:
          DATABASE_URL: postgresql+asyncpg://test:test@localhost:5432/galaxy_2_test
          REDIS_URL: redis://localhost:6379
          INTERNAL_JWT_SECRET: test_secret_min_32_characters_long

  build-images:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build API image
        run: docker build -t voip-api:pr-${{ github.event.number }} ./backend
      - name: Build worker image
        run: docker build -t voip-worker:pr-${{ github.event.number }} -f backend/Dockerfile.worker ./backend
```

### `deploy-staging.yml` — Runs on merge to staging branch

```yaml
name: Deploy to Staging

on:
  push:
    branches: [staging]

env:
  AWS_REGION: ap-southeast-1
  ECR_REGISTRY: ${{ secrets.AWS_ACCOUNT_ID }}.dkr.ecr.ap-southeast-1.amazonaws.com

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      id-token: write  # OIDC auth to AWS
      contents: read

    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      - name: Login to ECR
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build and push API image
        run: |
          IMAGE_TAG="${GITHUB_SHA::8}"
          docker build -t $ECR_REGISTRY/voip-api:$IMAGE_TAG ./backend
          docker push $ECR_REGISTRY/voip-api:$IMAGE_TAG
          echo "API_IMAGE=$ECR_REGISTRY/voip-api:$IMAGE_TAG" >> $GITHUB_ENV

      - name: Build and push worker image
        run: |
          IMAGE_TAG="${GITHUB_SHA::8}"
          docker build -t $ECR_REGISTRY/voip-worker:$IMAGE_TAG -f backend/Dockerfile.worker ./backend
          docker push $ECR_REGISTRY/voip-worker:$IMAGE_TAG

      - name: Deploy to ECS (API)
        run: |
          aws ecs update-service \
            --cluster voip-staging \
            --service voip-api \
            --force-new-deployment

      - name: Deploy to ECS (Billing Worker)
        run: |
          aws ecs update-service \
            --cluster voip-staging \
            --service voip-billing-worker \
            --force-new-deployment

      - name: Deploy Lua scripts to FreeSWITCH
        run: |
          # Sync Lua scripts to S3 (FreeSWITCH EC2 pulls from S3)
          aws s3 sync freeswitch/lua/ s3://voip-config-staging/lua/ --delete
          
          # Trigger hot reload via SSM (no SSH needed)
          INSTANCE_ID=$(aws ec2 describe-instances \
            --filters "Name=tag:Role,Values=freeswitch" "Name=tag:Environment,Values=staging" \
            --query "Reservations[0].Instances[0].InstanceId" --output text)
          
          aws ssm send-command \
            --instance-ids $INSTANCE_ID \
            --document-name "AWS-RunShellScript" \
            --parameters 'commands=["aws s3 sync s3://voip-config-staging/lua/ /usr/share/freeswitch/scripts/ && fs_cli -x \"reload mod_lua\""]'

      - name: Wait for ECS stability
        run: |
          aws ecs wait services-stable \
            --cluster voip-staging \
            --services voip-api voip-billing-worker

      - name: Smoke test
        run: |
          API_URL=${{ secrets.STAGING_API_URL }}
          curl -f $API_URL/health || exit 1
```

### `deploy-production.yml` — Manual approval required

```yaml
name: Deploy to Production

on:
  workflow_dispatch:
    inputs:
      confirm:
        description: "Type DEPLOY to confirm production deployment"
        required: true

jobs:
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: Check confirmation
        run: |
          if [ "${{ github.event.inputs.confirm }}" != "DEPLOY" ]; then
            echo "Deployment cancelled — must type DEPLOY exactly"
            exit 1
          fi

  deploy:
    needs: guard
    # ... same as staging but targeting production resources
    # Additional step: create CloudWatch alarm to detect call volume drop
    # If active calls drop > 20% within 10 min of deploy → auto-rollback
```

## Monitoring: `monitoring/`

### Grafana Dashboard JSON: `monitoring/grafana/dashboards/voip-calls.json`

Create dashboards with panels:
1. **Active calls right now** (gauge)
2. **Calls per minute** (time series, last 6h)
3. **Answer Seizure Ratio** — answered/total (gauge, target > 95%)
4. **API latency p50/p95/p99** (time series)
5. **Credit deductions per minute** (time series)
6. **Failed auth requests** (counter with alert threshold)
7. **CDR write rate** (time series)
8. **Redis memory usage** (gauge with 80% alert line)

### `monitoring/prometheus.yml` (local dev only)

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: fastapi
    static_configs:
      - targets: ["api:8000"]
    metrics_path: /metrics

  - job_name: billing_worker
    static_configs:
      - targets: ["billing-worker:9100"]
```

### Custom Metrics (emit from FastAPI)

Add `prometheus-client` to FastAPI:
```python
# In app/core/metrics.py
from prometheus_client import Counter, Histogram, Gauge

calls_authorized = Counter("voip_calls_authorized_total", "Calls authorized", ["account_id"])
calls_denied = Counter("voip_calls_denied_total", "Calls denied", ["reason"])
active_calls = Gauge("voip_active_calls", "Currently active calls")
auth_latency = Histogram("voip_auth_latency_seconds", "Auth endpoint latency",
                          buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0])
credit_deductions = Counter("voip_credit_deducted_cents_total", "Total credits deducted")
billing_tick_failures = Counter("voip_billing_tick_failures_total", "Failed billing ticks")
```

## Runbook: `docs/runbooks/`

Create these runbooks (Markdown, with exact commands):

### `docs/runbooks/freeswitch-restart.md`
- When to restart vs reload
- Pre-restart checklist (active calls)
- Reload commands (mod_lua, xml)
- Post-restart verification

### `docs/runbooks/billing-worker-restart.md`
- Safe restart procedure
- How to verify reconciliation ran
- How to check for missed CDRs

### `docs/runbooks/credit-discrepancy.md`
- How to identify discrepancies between Redis and PostgreSQL
- SQL + Redis CLI commands
- Manual reconciliation steps
- Escalation path

### `docs/runbooks/voxbone-trunk-down.md`
- How to detect (SIP registration failure)
- How to verify (FreeSWITCH console commands)
- Contact information for Voxbone support
- Failover procedure

## Constraints
- All deployments: zero-downtime for API (ECS rolling update)
- Lua deployments: hot-reload via SSM (never SSH in production)
- Production deployments: manual approval gate always required
- Alerts: must go to a real notification channel (email/PagerDuty/Slack)
- Never auto-rollback based on 5xx alone — could be normal spike; use active call metric
