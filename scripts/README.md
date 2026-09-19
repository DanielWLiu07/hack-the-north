# `scripts/`

| file | purpose |
|---|---|
| `bootstrap_pi.sh` | Clone BB quickstart, install deps, copy `robot/`, install the systemd unit, set static IP. |
| `bootstrap_laptop.sh` | venv, deps, pre-download model weights, `.env` check, then proves the stack runs offline. **Run tonight — venue wifi cannot be trusted for large downloads.** `--env-only` re-checks keys after a booth; `--offline` rebuilds the venv from the uv cache at the venue. |
| `requirements-laptop.txt` | The laptop venv. Tracks add their own `<folder>/requirements.txt` instead of editing this; the bootstrap installs those too. |
| `check_offline.py` | Blocks the network in-process, then loads every YOLO weight **by bare name**, runs SGBM → cloud → plane removal, writes an `.rrd`. Anything that lazily downloads fails here instead of at the venue. |
| `demo.sh` | Start Rerun viewer, start the agent, open the terminal with big fonts, put the LED in `clean`. One command, so nobody fumbles at judging. |
| `snapshot.sh` | Tag a known-good `room.git` + save the Rerun `.rrd`. **Run it the moment the first end-to-end run works**, and after every improvement. |

`snapshot.sh` is cheap insurance: never let the only working version be the one you're
currently editing.

## Model weights — the contract

Weights live in `$MODELS_DIR` (default `~/.cache/gitspace/models`), **outside the repo**.
The bootstrap drops a `gitspace_env.pth` into the venv that sets `YOLO_CONFIG_DIR` and
`YOLO_OFFLINE=1` for every Python process, so BB-style code works unchanged and offline:

```python
YOLO("yolo11s-seg.pt")   # resolves from $MODELS_DIR/weights — no network, no cwd copy
```

`YOLO_OFFLINE=1` means a weight that was never downloaded **fails fast** instead of hanging on
venue wifi. To fetch a new one on a good network: `YOLO_OFFLINE=0 python -c 'from ultralytics
import YOLO; YOLO("yolo26l-seg.pt")'`, or add it to `YOLO_WEIGHTS` and re-run the bootstrap.

SAM 3 is meant to run on **Baseten**. `--with-sam3` pulls `sam3.pt` (gated, ~3.5 GB) plus the
CLIP tokenizer that Ultralytics would otherwise `pip install` from GitHub at runtime.
