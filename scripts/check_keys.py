#!/usr/bin/env python3
"""check_keys.py — which credentials are real, which are placeholders, which are missing.

Checks VALUE PLAUSIBILITY, not just presence. A placeholder left in .env reports as
configured and then fails at the first network call, which is the worst of both.
"""
import re, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
env = {}
for line in (ROOT/".env").read_text().splitlines():
    if line.strip() and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); env[k.strip()] = v.strip()

PLACEHOLDER = re.compile(r"xxx|yyy|zzz|<|your[-_]|example|changeme|TODO|\bkey_here\b", re.I)
SHAPE = {
  "ELASTIC_URL":        (r"^https://(?!xxx)[\w-]+\.(es|kb)\.", "a real Serverless/Cloud endpoint"),
  "ELASTIC_API_KEY":    (r"^[A-Za-z0-9+/=_-]{40,}$",           "base64-ish, 40+ chars"),
  "SENTRY_DSN":         (r"^https://\w+@o\d+\.ingest\.",       "https://<key>@o<org>.ingest…"),
  "SENTRY_DSN_WEB":     (r"^https://\w+@o\d+\.ingest\.",       "https://<key>@o<org>.ingest…"),
  "SENTRY_AUTH_TOKEN":  (r"^sntry[us]_[A-Za-z0-9]{40,}$",      "sntryu_/sntrys_ + 40+ chars"),
  "OPENAI_API_KEY":     (r"^sk-[A-Za-z0-9_-]{20,}$",           "sk-…"),
  "HF_TOKEN":           (r"^hf_[A-Za-z0-9]{20,}$",             "hf_…"),
  "JINA_API_KEY":       (r"^jina_[A-Za-z0-9_-]{20,}$",         "jina_…"),
  "TELEGRAM_BOT_TOKEN": (r"^\d{8,}:[A-Za-z0-9_-]{30,}$",       "<id>:<secret>"),
  "ELEVENLABS_API_KEY": (r"^[a-z0-9_]{30,}$",                  "30+ chars"),
  "AWS_ACCESS_KEY_ID":  (r"^(AKIA|ASIA)[A-Z0-9]{16}$",         "AKIA…/ASIA… + 16"),
  "GITHUB_TOKEN":       (r"^(gh[pousr]_|github_pat_)",         "ghp_/github_pat_…"),
  "PI_HOST":            (r"^\d+\.\d+\.\d+\.\d+$",              "an IP"),
  "LAPTOP_IP":          (r"^\d+\.\d+\.\d+\.\d+$",              "an IP"),
}
G,Y,R,X = "\033[32m","\033[33m","\033[31m","\033[0m"
bad = 0
print(f"\n  CREDENTIALS — value plausibility, not just presence\n  " + "─"*62)
for k,(rx,hint) in SHAPE.items():
    v = env.get(k,"")
    if not v:                       state, note = f"{R}MISSING {X}", ""
    elif PLACEHOLDER.search(v):     state, note = f"{R}PLACEHOLDER{X}", f"still the template value"
    elif not re.match(rx, v):       state, note = f"{Y}SUSPECT {X}", f"expected {hint}"
    else:                           state, note = f"{G}  ok    {X}", ""
    if "ok" not in state: bad += 1
    print(f"   {state} {k:22s} {note}")
print(f"\n  {len(SHAPE)-bad} usable · {bad} need attention\n")
sys.exit(1 if bad else 0)
