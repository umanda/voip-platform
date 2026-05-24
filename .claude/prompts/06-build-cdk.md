# Prompt: Build AWS CDK Infrastructure (Phase 6)

## Prerequisites
- Phase 5 complete: all Docker images build and pass integration tests
- ECR repositories created (or create them in this phase)
- AWS account configured with appropriate IAM permissions
- You have read `.claude/context/aws-target.md` completely

## Task

Build the complete AWS CDK Python application under `infrastructure/`.
This is production infrastructure — treat it with the same rigor as application code.

## Structure

```
infrastructure/
├── app.py                      # CDK app entry point
├── cdk.json
├── requirements.txt            # aws-cdk-lib, constructs
├── stacks/
│   ├── __init__.py
│   ├── network_stack.py        # VPC, subnets, SGs, NACLs
│   ├── secrets_stack.py        # Secrets Manager + rotation
│   ├── freeswitch_stack.py     # EC2 + EIP + user-data
│   ├── api_stack.py            # ECS Fargate + ALB + ECR
│   ├── data_stack.py           # ElastiCache Redis (+ future RDS)
│   └── monitoring_stack.py     # CloudWatch dashboards + alarms + SNS
├── constructs/
│   ├── fargate_service.py      # Reusable Fargate service construct
│   └── freeswitch_ec2.py       # FreeSWITCH EC2 construct with EIP
└── config/
    ├── staging.py
    └── production.py
```

## Stack 1: `network_stack.py`

```python
"""
VoIP Platform — Network Stack

Creates VPC with:
- 2 public subnets (FreeSWITCH EC2)
- 2 private subnets (ECS, Redis, RDS)
- NAT Gateway (ECS outbound)
- All security groups with telecom-specific rules

Telecom note: RTP requires UDP 16384-32768 both inbound AND outbound.
Missing outbound RTP rules = one-way audio.
"""
from aws_cdk import (
    Stack, CfnOutput, Tags,
    aws_ec2 as ec2,
)
from constructs import Construct

class NetworkStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)
        
        self.vpc = ec2.Vpc(
            self, "VoipVPC",
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
        )
        
        # Security Groups
        self.freeswitch_sg = self._create_freeswitch_sg()
        self.api_sg = self._create_api_sg()
        self.billing_worker_sg = self._create_billing_worker_sg()
        self.redis_sg = self._create_redis_sg()
        self.db_sg = self._create_db_sg()
        
        # Allow FreeSWITCH → FastAPI
        self.freeswitch_sg.add_egress_rule(
            self.api_sg, ec2.Port.tcp(8000), "FreeSWITCH Lua to FastAPI"
        )
        # Allow billing worker → FreeSWITCH ESL
        self.billing_worker_sg.add_egress_rule(
            self.freeswitch_sg, ec2.Port.tcp(8021), "Billing worker ESL"
        )
        # Allow FreeSWITCH ESL inbound from billing worker only
        self.freeswitch_sg.add_ingress_rule(
            self.billing_worker_sg, ec2.Port.tcp(8021), "ESL from billing worker"
        )
    
    def _create_freeswitch_sg(self) -> ec2.SecurityGroup:
        sg = ec2.SecurityGroup(self, "FreeSwitchSG", vpc=self.vpc,
                               description="FreeSWITCH SIP/RTP/ESL")
        
        # Voxbone SIP IPs (update with actual Voxbone IP ranges)
        voxbone_ips = ["185.61.144.0/22", "62.240.160.0/21"]  # Example — verify with Voxbone
        for cidr in voxbone_ips:
            sg.add_ingress_rule(ec2.Peer.ipv4(cidr), ec2.Port.udp(5060), "SIP UDP from Voxbone")
            sg.add_ingress_rule(ec2.Peer.ipv4(cidr), ec2.Port.tcp(5060), "SIP TCP from Voxbone")
            sg.add_ingress_rule(ec2.Peer.ipv4(cidr), ec2.Port.tcp(5061), "SIP TLS from Voxbone")
        
        # RTP inbound (from anywhere — callers send RTP from many IPs)
        sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.udp_range(16384, 32768),
            "RTP inbound media"
        )
        # RTP outbound handled by default VPC egress
        
        return sg
    
    # ... create other SGs similarly
```

## Stack 2: `freeswitch_stack.py`

```python
"""
FreeSWITCH EC2 Stack

IMPORTANT: FreeSWITCH runs on EC2, NOT ECS Fargate.
Reasons: RTP/UDP handling, SIP NAT traversal, kernel tuning, ESL.
Elastic IP is mandatory for Voxbone SIP trunk registration.
"""
from aws_cdk import (
    Stack, CfnOutput,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_ssm as ssm,
)

class FreeSwitchStack(Stack):
    def __init__(self, scope, id, vpc, network_stack, **kwargs):
        super().__init__(scope, id, **kwargs)
        
        # IAM role for EC2 (Secrets Manager + SSM + CloudWatch)
        role = iam.Role(
            self, "FreeSwitchRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
                iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchAgentServerPolicy"),
            ],
        )
        # Grant read access to specific secrets
        # secrets_stack.freeswitch_secret.grant_read(role)
        
        # User data — runs on first boot
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            # System updates
            "apt-get update && apt-get upgrade -y",
            # NTP — CRITICAL for SIP auth
            "timedatectl set-ntp true",
            'echo "server 169.254.169.123 prefer iburst" >> /etc/ntp.conf',
            # FreeSWITCH installation (from SignalWire packages)
            "TOKEN=$(aws secretsmanager get-secret-value --secret-id /voip/signalwire-token --query SecretString --output text)",
            "apt-get install -y freeswitch freeswitch-mod-lua freeswitch-mod-commands",
            # Lua modules
            "apt-get install -y lua5.1 lua-socket lua-cjson",
            # CloudWatch agent
            "wget https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb",
            "dpkg -i amazon-cloudwatch-agent.deb",
            # Pull config from S3
            "aws s3 sync s3://voip-config-bucket/freeswitch/ /etc/freeswitch/",
            "aws s3 sync s3://voip-config-bucket/lua/ /usr/share/freeswitch/scripts/",
            # Kernel tuning for SIP/RTP
            "sysctl -w net.core.rmem_max=16777216",
            "sysctl -w net.core.wmem_max=16777216",
            "sysctl -w net.ipv4.udp_rmem_min=8192",
            # Start services
            "systemctl enable freeswitch && systemctl start freeswitch",
            "systemctl enable amazon-cloudwatch-agent && systemctl start amazon-cloudwatch-agent",
        )
        
        self.instance = ec2.Instance(
            self, "FreeSwitchEC2",
            instance_type=ec2.InstanceType("t3.xlarge"),
            machine_image=ec2.MachineImage.from_ssm_parameter(
                "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=network_stack.freeswitch_sg,
            role=role,
            user_data=user_data,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/sda1",
                    volume=ec2.BlockDeviceVolume.ebs(50),  # 50GB for recordings
                )
            ],
        )
        
        # Elastic IP — mandatory for Voxbone SIP trunk
        self.eip = ec2.CfnEIP(self, "FreeSwitchEIP")
        ec2.CfnEIPAssociation(
            self, "EIPAssoc",
            instance_id=self.instance.instance_id,
            allocation_id=self.eip.attr_allocation_id,
        )
        
        CfnOutput(self, "FreeSwitchIP", value=self.eip.ref,
                  description="Register this IP with Voxbone SIP trunk")
```

## Stack 3: `api_stack.py` (ECS Fargate)

Create ECS cluster, ECR repos, task definitions for FastAPI and billing worker.
Include:
- ALB with HTTPS listener (ACM cert)
- Auto-scaling on CPU (target 60%)
- CloudWatch log groups
- ECR with image scanning enabled
- Task role with Secrets Manager read permissions

## Stack 4: `data_stack.py`

```python
# ElastiCache Redis (Multi-AZ, encryption in transit and at rest)
redis_cluster = elasticache.CfnReplicationGroup(
    self, "VoipRedis",
    replication_group_description="VoIP platform credit cache",
    num_cache_clusters=2,  # Multi-AZ
    cache_node_type="cache.t3.medium",  # t3.micro for staging
    engine="redis",
    engine_version="7.0",
    at_rest_encryption_enabled=True,
    transit_encryption_enabled=True,
    automatic_failover_enabled=True,
    # snapshot_retention_limit=7,  # 7-day backup
)
```

## Stack 5: `monitoring_stack.py`

Required dashboards:
- **Call Volume Dashboard:** active calls, calls/min, ASR (answer seizure ratio)
- **Billing Dashboard:** credit deductions/min, failed deductions, CDR write rate
- **Infrastructure Dashboard:** CPU, memory, Redis memory, API latency

Required alarms (see `aws-target.md` for full list).
All alarms → SNS topic → email notification.

## CDK App Entry

```python
# infrastructure/app.py
import aws_cdk as cdk
from config.production import ProductionConfig

app = cdk.App()
config = ProductionConfig()

env = cdk.Environment(
    account=config.aws_account_id,
    region=config.aws_region,
)

network = NetworkStack(app, f"{config.prefix}-Network", config=config, env=env)
secrets = SecretsStack(app, f"{config.prefix}-Secrets", env=env)
data = DataStack(app, f"{config.prefix}-Data", 
                 vpc=network.vpc, 
                 security_groups=network,
                 env=env)
freeswitch = FreeSwitchStack(app, f"{config.prefix}-FreeSWITCH",
                              vpc=network.vpc,
                              network_stack=network,
                              secrets_stack=secrets,
                              env=env)
api = ApiStack(app, f"{config.prefix}-Api",
               vpc=network.vpc,
               network_stack=network,
               data_stack=data,
               secrets_stack=secrets,
               env=env)
monitoring = MonitoringStack(app, f"{config.prefix}-Monitoring",
                              api_stack=api,
                              freeswitch_stack=freeswitch,
                              env=env)

Tags.of(app).add("Project", "voip-platform")
Tags.of(app).add("ManagedBy", "CDK")

app.synth()
```

## Deployment Commands

Create `scripts/deploy.sh`:
```bash
#!/bin/bash
set -e
ENVIRONMENT=${1:-staging}

echo "Deploying to $ENVIRONMENT..."

# Bootstrap (first time only)
# cdk bootstrap aws://ACCOUNT_ID/REGION

# Diff first
cdk diff --all

# Deploy
cdk deploy --all --require-approval=never
```

## Constraints
- `RemovalPolicy.RETAIN` for ALL stateful resources (Redis, RDS when added)
- Every resource tagged: Project, Environment, Component
- No hardcoded account IDs or ARNs in stack code — use config objects
- CDK synth must pass with zero errors before any deploy
- Staging stack must mirror production stack exactly (different sizes/counts ok)
- Use `ssm.StringParameter` for cross-stack references (not `CfnOutput` imports)
