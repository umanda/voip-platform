# AWS Target Architecture

## Account & Region
- Region: `ap-southeast-1` (Singapore — closest to Sri Lanka, good for Voxbone APAC)
- Alternatively: `ap-south-1` (Mumbai) — benchmark latency both
- Use a single AWS account for now; separate prod/staging via environment tags

## CDK App Structure

```python
# infrastructure/app.py
import aws_cdk as cdk
from stacks.network_stack import NetworkStack
from stacks.secrets_stack import SecretsStack
from stacks.freeswitch_stack import FreeSwitchStack
from stacks.api_stack import ApiStack
from stacks.data_stack import DataStack
from stacks.monitoring_stack import MonitoringStack

app = cdk.App()
env = cdk.Environment(account="123456789", region="ap-southeast-1")

network = NetworkStack(app, "VoipNetwork", env=env)
secrets = SecretsStack(app, "VoipSecrets", env=env)
data = DataStack(app, "VoipData", vpc=network.vpc, env=env)
freeswitch = FreeSwitchStack(app, "VoipFreeSWITCH", vpc=network.vpc, env=env)
api = ApiStack(app, "VoipApi", vpc=network.vpc, data_stack=data, env=env)
monitoring = MonitoringStack(app, "VoipMonitoring", env=env)
```

## Network Stack

```
VPC: 10.0.0.0/16
├── Public Subnets (2 AZs):  10.0.0.0/24, 10.0.1.0/24
│   └── FreeSWITCH EC2 (Elastic IP)
│   └── NAT Gateway
└── Private Subnets (2 AZs): 10.0.10.0/24, 10.0.11.0/24
    └── ECS Fargate (FastAPI, Billing Worker)
    └── ElastiCache Redis
    └── RDS PostgreSQL (future)
    └── EC2 PostgreSQL (current — internal access only)
```

## Security Groups

### SG: freeswitch-sg
| Rule       | Port      | Source              | Reason           |
|------------|-----------|---------------------|------------------|
| Inbound    | 5060 UDP  | Voxbone IPs         | SIP              |
| Inbound    | 5060 TCP  | Voxbone IPs         | SIP TCP          |
| Inbound    | 5061 TCP  | Voxbone IPs         | SIP TLS          |
| Inbound    | 16384-32768 UDP | 0.0.0.0/0    | RTP media        |
| Inbound    | 22 TCP    | Admin CIDR          | SSH (temp)       |
| Outbound   | 8000 TCP  | api-sg              | FastAPI          |
| Outbound   | 16384-32768 UDP | 0.0.0.0/0  | RTP outbound     |
| Outbound   | 5060 UDP  | 0.0.0.0/0           | SIP to gateway   |

### SG: api-sg
| Rule       | Port     | Source          | Reason            |
|------------|----------|-----------------|-------------------|
| Inbound    | 8000 TCP | alb-sg          | From ALB          |
| Inbound    | 8000 TCP | freeswitch-sg   | From Lua scripts  |
| Outbound   | 5432 TCP | db-sg           | PostgreSQL        |
| Outbound   | 6379 TCP | redis-sg        | Redis             |
| Outbound   | 8021 TCP | freeswitch-sg   | ESL (billing wkr) |

### SG: billing-worker-sg
| Rule       | Port     | Source          | Reason            |
|------------|----------|-----------------|-------------------|
| Outbound   | 8021 TCP | freeswitch-sg   | ESL connection    |
| Outbound   | 6379 TCP | redis-sg        | Redis             |
| Outbound   | 5432 TCP | db-sg           | PostgreSQL        |

## ECS Task Definitions

### FastAPI Service
```python
task_def = ecs.FargateTaskDefinition(
    self, "ApiTask",
    cpu=512,
    memory_limit_mib=1024,
)
container = task_def.add_container(
    "api",
    image=ecs.ContainerImage.from_ecr_repository(api_repo),
    environment={
        "ENVIRONMENT": "production",
    },
    secrets={
        # Injected from Secrets Manager at start
        "DATABASE_URL": ecs.Secret.from_secrets_manager(db_secret, "url"),
        "REDIS_URL": ecs.Secret.from_secrets_manager(redis_secret, "url"),
        "INTERNAL_JWT_SECRET": ecs.Secret.from_secrets_manager(jwt_secret),
    },
    logging=ecs.LogDrivers.aws_logs(
        stream_prefix="fastapi",
        log_group=api_log_group,
    ),
)
```

### Billing Worker Service
```python
# Single task (NOT behind ALB — ESL is outbound from worker)
# min_healthy_percent=0 to allow single-instance replacement
billing_service = ecs.FargateService(
    self, "BillingWorker",
    task_definition=billing_task_def,
    desired_count=1,
    min_healthy_percent=0,
    max_healthy_percent=100,
)
```

## Secrets Manager Structure

```
/voip-platform/prod/
├── database          → {"url": "postgresql+asyncpg://...", "host": "...", "port": 5432, ...}
├── redis             → {"url": "rediss://...", "host": "...", "port": 6379}
├── freeswitch-esl    → {"host": "10.0.0.x", "port": 8021, "password": "..."}
├── jwt-internal      → {"secret": "..."}
└── voxbone           → {"username": "...", "password": "...", "trunk": "..."}
```

## CloudWatch Alarms (Required — Not Optional)

| Alarm                         | Metric                        | Threshold     | Action      |
|-------------------------------|-------------------------------|---------------|-------------|
| API 5xx error rate            | HTTPCode_Target_5XX_Count     | > 10/min      | SNS alert   |
| API response time             | TargetResponseTime p99        | > 1000ms      | SNS alert   |
| Billing worker down           | RunningTaskCount < 1          | Immediate     | SNS alert   |
| FreeSWITCH CPU                | EC2 CPUUtilization            | > 80%         | SNS alert   |
| Redis memory                  | DatabaseMemoryUsagePercentage | > 80%         | SNS alert   |
| Credit deduction failures     | Custom metric                 | > 0           | SNS alert   |
| Active calls sudden drop      | Custom metric                 | > 20% drop    | SNS alert   |

## FreeSWITCH EC2 User Data

```bash
#!/bin/bash
# Install FreeSWITCH from packages
# Install Lua modules (luasocket, cjson)
# Install AWS CLI
# Pull Lua scripts from S3 on boot
# Configure CloudWatch agent
# Set NTP to 169.254.169.123
# Register with Systems Manager (SSM) for shell access without SSH
```

## Cost Estimates (ap-southeast-1, monthly)

| Resource              | Type               | Est. Cost/mo |
|-----------------------|--------------------|--------------|
| FreeSWITCH EC2        | t3.xlarge          | ~$130        |
| Elastic IP            | 1x EIP             | ~$4          |
| FastAPI ECS           | 0.5 vCPU, 1GB x2  | ~$30         |
| Billing Worker ECS    | 0.25 vCPU, 512MB  | ~$8          |
| ElastiCache Redis     | cache.t3.micro     | ~$25         |
| EC2 PostgreSQL        | (existing)         | $0 (existing)|
| ALB                   | 1x ALB             | ~$20         |
| CloudWatch Logs       | ~10GB/mo           | ~$5          |
| Secrets Manager       | 5 secrets          | ~$2          |
| Data transfer         | ~50GB/mo           | ~$4          |
| **Total**             |                    | **~$228/mo** |
