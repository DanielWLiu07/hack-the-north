#!/usr/bin/env bash
# scripts/deploy_web.sh — the web tier on AWS (docs/19): t4g.small in us-east-1, Caddy with
# automatic TLS, an IAM role instead of keys, and the S3 bucket with a 7-day lifecycle rule.
# Then the Sentry Uptime monitor on the public URL — the sixth product.
#
#   scripts/deploy_web.sh                              plan: print what would happen, NO AWS calls
#   scripts/deploy_web.sh --apply --account 1234…      create/update (refuses a different account)
#   scripts/deploy_web.sh --ship --account 1234…       re-sync code + .env to the running box
#   scripts/deploy_web.sh --teardown --account 1234…   delete it all (the bucket only with --delete-bucket)
#
# Env: AWS_PROFILE picks the credentials. DOMAIN=example.com uses gitspace.example.com (point its
# A record at the Elastic IP first) instead of <ip>.sslip.io, which needs no DNS at all.
# The box gets ES + Sentry settings only: no OpenAI, AWS, GitHub or Sentry-auth secrets.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
REGION=us-east-1                    # docs/19: same metro as Elastic's GCP us-east4, and S3/SQS
NAME=gitspace-web
TYPE=t4g.small
KEY=$HOME/.ssh/$NAME.pem
BOX_KEYS="ELASTIC_URL ELASTIC_API_KEY SENTRY_DSN SENTRY_DSN_WEB SENTRY_ENVIRONMENT SENTRY_RELEASE
  SENTRY_TRACES_SAMPLE_RATE SENTRY_PROFILES_SAMPLE_RATE SENTRY_REPLAYS_SAMPLE_RATE SENTRY_ORG_SLUG
  WEB_ALLOWED_COMMANDS S3_BUCKET"
MODE=plan ACCOUNT="" DELETE_BUCKET=0
while [ $# -gt 0 ]; do
  case $1 in
    --apply) MODE=apply ;; --ship) MODE=ship ;; --teardown) MODE=teardown ;;
    --account) ACCOUNT=$2; shift ;; --delete-bucket) DELETE_BUCKET=1 ;;
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

env_get() { grep -E "^$1=" "$ROOT/.env" | tail -n 1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/^"(.*)"$/\1/'; }
BUCKET=$(env_get S3_BUCKET); BUCKET=${BUCKET:-gitspace-clouds}
aws() { command aws --region "$REGION" --output text "$@"; }
say() { printf '  %s\n' "$*"; }

if [ "$MODE" = plan ]; then
  cat <<PLAN
plan — nothing below has run; --apply --account <id> does it
  bucket     s3://$BUCKET  (us-east-1, public access blocked, objects expire after 7 days)
  iam        role $NAME-role + instance profile: s3 read/write on that bucket only
  network    default VPC · security group $NAME-sg: 80, 443 from anywhere; 22 from YOUR IP only
  box        $TYPE (arm64, Amazon Linux 2023) · key pair $NAME -> $KEY · Elastic IP
  host       ${DOMAIN:+gitspace.$DOMAIN}${DOMAIN:-<elastic-ip>.sslip.io} · Caddy, Let's Encrypt, SSE unbuffered
  app        web/ + elastic/ + obs.py -> /opt/gitspace, uv venv, systemd; uvicorn on 127.0.0.1:8000
  secrets    $(echo $BOX_KEYS | wc -w | tr -d ' ') keys from .env: $(echo $BOX_KEYS | tr '\n' ' ')
  after      .env WEB_PUBLIC_URL := https://<host>; Sentry Uptime monitor on https://<host>/api/health
  cost       ~\$0.017/h instance + \$0.005/h public IPv4 · teardown: --teardown
PLAN
  exit 0
fi

# ── guard: the right account, every time ──────────────────────────────────────
[ -n "$ACCOUNT" ] || { echo "--account <aws account id> is required for $MODE" >&2; exit 2; }
WHO=$(aws sts get-caller-identity --query Account)
[ "$WHO" = "$ACCOUNT" ] || { echo "credentials are for account $WHO, not $ACCOUNT — refusing" >&2; exit 1; }
say "account $WHO · region $REGION"

instance_id() { aws ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped" --query 'Reservations[].Instances[].InstanceId' | awk '{print $1}'; }
eip_alloc() { aws ec2 describe-addresses --filters "Name=tag:Name,Values=$NAME" --query 'Addresses[0].AllocationId' | grep -v None || true; }
eip_ip() { aws ec2 describe-addresses --filters "Name=tag:Name,Values=$NAME" --query 'Addresses[0].PublicIp' | grep -v None || true; }
host_for() { if [ -n "${DOMAIN:-}" ]; then echo "gitspace.$DOMAIN"; else echo "$(echo "$1" | tr . -).sslip.io"; fi; }

if [ "$MODE" = teardown ]; then
  IID=$(instance_id); ALLOC=$(eip_alloc)
  [ -n "$ALLOC" ] && { aws ec2 release-address --allocation-id "$ALLOC" 2>/dev/null || { aws ec2 disassociate-address --association-id "$(aws ec2 describe-addresses --allocation-ids "$ALLOC" --query 'Addresses[0].AssociationId')"; aws ec2 release-address --allocation-id "$ALLOC"; }; say "released Elastic IP"; }
  [ -n "$IID" ] && { aws ec2 terminate-instances --instance-ids "$IID" >/dev/null; aws ec2 wait instance-terminated --instance-ids "$IID"; say "terminated $IID"; }
  SG=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$NAME-sg" --query 'SecurityGroups[0].GroupId' | grep -v None || true)
  [ -n "$SG" ] && { aws ec2 delete-security-group --group-id "$SG"; say "deleted $SG"; }
  aws ec2 delete-key-pair --key-name "$NAME" >/dev/null 2>&1 && say "deleted key pair" || true
  command aws iam remove-role-from-instance-profile --instance-profile-name "$NAME-profile" --role-name "$NAME-role" 2>/dev/null || true
  command aws iam delete-instance-profile --instance-profile-name "$NAME-profile" 2>/dev/null || true
  command aws iam delete-role-policy --role-name "$NAME-role" --policy-name s3-clouds 2>/dev/null || true
  command aws iam delete-role --role-name "$NAME-role" 2>/dev/null && say "deleted IAM role" || true
  if [ "$DELETE_BUCKET" = 1 ]; then command aws s3 rb "s3://$BUCKET" --force && say "deleted bucket"; else say "kept s3://$BUCKET (--delete-bucket to remove)"; fi
  exit 0
fi

ship() {  # $1 = public ip. Code + a .env with only what the web tier needs.
  local ip=$1 host tmp
  host=$(host_for "$ip"); tmp=$(mktemp)
  { for k in $BOX_KEYS; do v=$(env_get "$k"); [ -n "$v" ] && printf '%s=%s\n' "$k" "$v"; done
    printf 'WEB_BIND=127.0.0.1:8000\nWEB_PUBLIC_URL=https://%s\nAWS_REGION=%s\n' "$host" "$REGION"; } > "$tmp"
  local ssh="ssh -i $KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
  for _ in $(seq 1 40); do $ssh "ec2-user@$ip" 'test -f /var/lib/cloud/instance/boot-finished' 2>/dev/null && break; sleep 5; done
  rsync -az --delete -e "$ssh" --exclude __pycache__ --exclude tests --exclude '*.pyc' \
    "$ROOT/web" "$ROOT/elastic" "$ROOT/obs.py" "ec2-user@$ip:/opt/gitspace/"
  scp -q -i "$KEY" "$tmp" "ec2-user@$ip:/opt/gitspace/.env"; rm -f "$tmp"
  $ssh "ec2-user@$ip" "set -e; chmod 600 /opt/gitspace/.env
    cd /opt/gitspace && [ -x .venv/bin/python ] || uv venv -q --python 3.11 .venv
    uv pip install -q --python .venv/bin/python -r web/requirements.txt
    printf '%s {\n  encode gzip\n  reverse_proxy 127.0.0.1:8000 {\n    flush_interval -1\n  }\n}\n' '$host' | sudo tee /etc/caddy/Caddyfile >/dev/null
    sudo systemctl daemon-reload && sudo systemctl enable -q --now gitspace-web caddy && sudo systemctl restart gitspace-web caddy"
  say "shipped to $ip ($host)"
}

if [ "$MODE" = ship ]; then IP=$(eip_ip); [ -n "$IP" ] || { echo "no $NAME box" >&2; exit 1; }; ship "$IP"; exit 0; fi

# ── apply ────────────────────────────────────────────────────────────────────
if ! command aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  command aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null   # 409 = someone else's name
  say "created s3://$BUCKET"
fi
command aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
command aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration \
  '{"Rules":[{"ID":"expire-7d","Status":"Enabled","Filter":{},"Expiration":{"Days":7},"AbortIncompleteMultipartUpload":{"DaysAfterInitiation":1}}]}'
say "s3://$BUCKET: public access blocked, 7-day expiry"

if ! command aws iam get-role --role-name "$NAME-role" >/dev/null 2>&1; then
  command aws iam create-role --role-name "$NAME-role" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  command aws iam create-instance-profile --instance-profile-name "$NAME-profile" >/dev/null
  command aws iam add-role-to-instance-profile --instance-profile-name "$NAME-profile" --role-name "$NAME-role"
  sleep 10   # IAM is eventually consistent; run-instances rejects a profile it can't see yet
fi
command aws iam put-role-policy --role-name "$NAME-role" --policy-name s3-clouds --policy-document \
  "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:PutObject\",\"s3:ListBucket\"],\"Resource\":[\"arn:aws:s3:::$BUCKET\",\"arn:aws:s3:::$BUCKET/*\"]}]}"
say "IAM role $NAME-role: s3 on $BUCKET only"

if [ ! -f "$KEY" ]; then
  aws ec2 create-key-pair --key-name "$NAME" --key-type ed25519 --query KeyMaterial > "$KEY"; chmod 600 "$KEY"
  say "key pair -> $KEY"
fi
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId')
SG=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$NAME-sg" --query 'SecurityGroups[0].GroupId' | grep -v None || true)
if [ -z "$SG" ]; then
  SG=$(aws ec2 create-security-group --group-name "$NAME-sg" --description "gitspace web tier" --vpc-id "$VPC" --query GroupId)
  aws ec2 authorize-security-group-ingress --group-id "$SG" --ip-permissions \
    'IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0}]' 'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}]' >/dev/null
fi
MYIP=$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 --cidr "$MYIP/32" >/dev/null 2>&1 || true
say "security group $SG: 80/443 public, 22 from $MYIP"

IID=$(instance_id)
if [ -z "$IID" ]; then
  AMI=$(aws ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 --query Parameter.Value)
  USERDATA=$(cat <<'UD'
#!/bin/bash
set -euxo pipefail
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=arm64" -o /usr/local/bin/caddy && chmod +x /usr/local/bin/caddy
dnf install -y rsync
useradd --system --home /var/lib/caddy --create-home caddy || true
mkdir -p /opt/gitspace /etc/caddy && chown ec2-user:ec2-user /opt/gitspace
cat > /etc/systemd/system/gitspace-web.service <<'U'
[Unit]
Description=gitspace web tier
After=network-online.target
[Service]
User=ec2-user
WorkingDirectory=/opt/gitspace/web
ExecStart=/opt/gitspace/.venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
U
cat > /etc/systemd/system/caddy.service <<'U'
[Unit]
Description=Caddy
After=network-online.target
[Service]
User=caddy
ExecStart=/usr/local/bin/caddy run --config /etc/caddy/Caddyfile
AmbientCapabilities=CAP_NET_BIND_SERVICE
Environment=XDG_DATA_HOME=/var/lib/caddy
Restart=always
[Install]
WantedBy=multi-user.target
U
UD
)
  IID=$(aws ec2 run-instances --image-id "$AMI" --instance-type "$TYPE" --key-name "$NAME" \
    --security-group-ids "$SG" --iam-instance-profile "Name=$NAME-profile" --user-data "$USERDATA" \
    --metadata-options HttpTokens=required \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME},{Key=project,Value=gitspace}]" \
    --query 'Instances[0].InstanceId')
  aws ec2 wait instance-running --instance-ids "$IID"
  say "launched $IID"
fi
ALLOC=$(eip_alloc)
if [ -z "$ALLOC" ]; then
  ALLOC=$(aws ec2 allocate-address --domain vpc --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME}]" --query AllocationId)
fi
aws ec2 associate-address --instance-id "$IID" --allocation-id "$ALLOC" >/dev/null
IP=$(eip_ip); HOST=$(host_for "$IP")
say "Elastic IP $IP -> https://$HOST"

ship "$IP"
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "https://$HOST/api/health" || true)
  [ "$code" = 200 ] && break; sleep 5
done
[ "$code" = 200 ] && say "https://$HOST/api/health -> 200 (TLS by Let's Encrypt)" || { say "https://$HOST not answering yet ($code)"; exit 1; }

if grep -qE '^WEB_PUBLIC_URL=' "$ROOT/.env"; then
  sed -i.bak -E "s#^WEB_PUBLIC_URL=.*#WEB_PUBLIC_URL=https://$HOST#" "$ROOT/.env" && rm -f "$ROOT/.env.bak"
fi
say ".env WEB_PUBLIC_URL=https://$HOST"
"$ROOT/.venv/bin/python" "$ROOT/scripts/sentry_uptime.py" "https://$HOST/api/health"
