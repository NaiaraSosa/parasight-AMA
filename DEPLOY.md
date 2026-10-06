# Deploying Parasight-AMA

How to run the Parasight-AMA web app as a permanent service on a Linux server with systemd. It gets its own port, its data is stored apart from the code, and it starts on boot. It can run next to other web apps on the same host.

Supporting files live in [`deploy/`](deploy/):

| File | Purpose |
|---|---|
| `deploy/environment.yml` | Conda env: Python 3.10, PyTorch (CUDA), this repo (plus the `pyproject.toml` deps) |
| `deploy/parasight.env.example` | Runtime config: port, data paths, model caches, TMPDIR |
| `deploy/parasight.service` | systemd unit that starts the app on boot and restarts it on failure |
| `deploy/parasight-cleanup.sh` | Deletes old jobs (the app never deletes anything) |

The paths below are the defaults these files use. If you change them, change them in every file (see [Customizing paths](#customizing-paths)).

| Placeholder | Default | What |
|---|---|---|
| App dir | `/opt/parasight-AMA` | git checkout (small) |
| Data root | `/srv/parasight` | env, model caches, uploads, results (**large**) |
| Port | `8010` | HTTP port of the app |
| Service user | `parasight` | runs the process, owns the data |

---

## 1. Requirements

- Linux with systemd (tested on Ubuntu 22.04).
- Conda / Miniforge.
- Optional: an NVIDIA GPU. The PyTorch wheels from PyPI bundle CUDA 13.x and need driver ≥ 580; for older drivers see the comment in `deploy/environment.yml`. Without one, Cellpose falls back to the CPU, which is much slower.
- Disk space:
  - ~8 GB for the conda env (PyTorch + TensorFlow)
  - ~0.5 GB for model weights
  - storage for data: plan for **≈3× the size of the largest upload** while a job runs, plus the results you keep. Microscopy batches (`.tiff`/`.czi`, ZIPs) can be many GB.
- Outbound HTTPS on the first run, to download the Cellpose and StarDist weights.

## 2. How the app behaves (relevant to deployment)

- **FastAPI + uvicorn**, single process.
- **Models are cached per process** (Cellpose on PyTorch/GPU; StarDist on TensorFlow CPU). Run **1 worker**. Each extra worker would load another copy of the models, onto the GPU when there is one.
- **Model weights download on first use**: Cellpose to `~/.cellpose` (overridable with `CELLPOSE_LOCAL_MODELS_PATH`), StarDist to `~/.keras`. The service user needs a writable `$HOME`.
- **Storage** is set with env vars (`app/core/config.py`): `DATA_DIR`, `UPLOADS_DIR`, `OUTPUTS_DIR`, `PROCESSING_DIR` (formerly `TEMP_DIR`, still accepted). Per job the app writes `uploads/<uuid>/` (the original file), `outputs/<uuid>/` (previews, CSVs, a results ZIP) and, while the job runs, `processing/<uuid>/` (ZIPs are extracted here; deleted when the job ends). Uploads and outputs are never deleted by the app; see section 7. At startup the app writes a `README.txt` into each of these folders saying what it holds and whether it is safe to delete.
- **The service's `$TMPDIR` holds scratch files, not job data.** Starlette spools each upload there before the app copies it to `uploads/`, and TensorFlow/PyTorch keep temp files there. On many servers `/tmp` sits on a small root filesystem, so point `TMPDIR` at the large volume (`/srv/parasight/tmp`).
- **Jobs run in the background, one at a time.** Submitting a job queues it and redirects to `/jobs/{job_id}`, a status page that shows the queue position or progress and then the results. Users can close the tab and come back to that link. Each job's state is kept in `outputs/<uuid>/status.json` and `result.json`.
- `MAX_UPLOAD_MB` limits each uploaded file (HTTP 413 above it; `0` = no limit). The app default is 500 MB; `deploy/parasight.env.example` raises it to 20 GB for image batches.
- **No authentication.** Anyone who can reach the port can upload images and download a job's results if they know its UUID.

## 3. Layout

```
/opt/parasight-AMA/                 code (git clone), read-only to the service
/etc/parasight/parasight.env        runtime config (from deploy/parasight.env.example)
/etc/systemd/system/parasight.service

/srv/parasight/                     service user's HOME; put on a large volume
├── env/                            conda env (~8 GB)
├── cache/
│   ├── cellpose/                   Cellpose weights
│   └── matplotlib/
├── .keras/                         StarDist weights (created automatically)
├── tmp/                            $TMPDIR: service scratch (uploads in transit, TF/PyTorch
│                                   temp files); safe to empty when idle
└── data/                           <- per-job data only; can be moved/backed up on its own
    ├── uploads/<job-uuid>/         original .tiff/.czi/.zip as uploaded       KEEP
    ├── outputs/<job-uuid>/         results, previews, results_<uuid>.zip      KEEP
    └── processing/<job-uuid>/      unzipped inputs while the job runs; deleted when it ends
```

Each folder under `data/` gets a `README.txt` from the app, so someone looking only at the disk can tell what may be deleted. Old jobs should be removed with `deploy/parasight-cleanup.sh` (section 7), which deletes a job's `uploads/` and `outputs/` folders together.

Why this layout:
- **Code and data are separate.** `git pull` never touches data, and the data directory can be backed up, resized or moved on its own.
- **Large files stay off the root filesystem.** A big upload can't fill `/` and take the host down.
- A **dedicated system user** owns the data and runs the process, isolated from other apps and other users' homes.

Mount a large disk at `/srv/parasight`, or make it a symlink/bind mount to one. You can also split the data: keep `uploads/` and `outputs/` on persistent, backed-up storage and put `PROCESSING_DIR`/`TMPDIR` on fast local scratch. In that case add the scratch path to `ReadWritePaths=` in the unit.

## 4. Installation

Run as an administrator with sudo, in order.

### 4.1 Service user and directories

```bash
sudo useradd --system --user-group --home-dir /srv/parasight --no-create-home \
     --shell /usr/sbin/nologin parasight

P=/srv/parasight
sudo mkdir -p \
  "$P/env" \
  "$P/cache/cellpose" \
  "$P/cache/matplotlib" \
  "$P/data/uploads" \
  "$P/data/outputs" \
  "$P/data/processing" \
  "$P/tmp"
sudo chown -R parasight:parasight /srv/parasight
sudo chmod 2750 /srv/parasight /srv/parasight/data /srv/parasight/data/*   # setgid: new files keep the group
```

To let a group of people read results directly from disk, add the service user to that group (`sudo usermod -aG <group> parasight`) and `chgrp -R <group> /srv/parasight/data`.

### 4.2 Code

```bash
sudo git clone https://github.com/trypanosomatics/parasight-AMA /opt/parasight-AMA
sudo chmod -R a+rX /opt/parasight-AMA
```

Always create the env from **`/opt/parasight-AMA`**. It is an editable install (`pip install -e`), so the env points at whichever checkout it was built from. `ProtectHome=true` in the unit hides `/home` from the service, so an env pointing at a checkout under `/home` would fail to start.

### 4.3 Conda environment

```bash
cd /opt/parasight-AMA
sudo PYTHONNOUSERSITE=1 conda env create -p /srv/parasight/env -f deploy/environment.yml
sudo chmod -R a+rX /srv/parasight/env      # readable by the service, writable only by root

# Sanity checks
/srv/parasight/env/bin/python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
/srv/parasight/env/bin/python -c "import numpy, tensorflow as tf, stardist, cellpose; print('numpy', numpy.__version__, 'tf', tf.__version__)"
```

Expected: `numpy 1.26.x` (must be < 2 for TF 2.15/StarDist), `tf 2.15.x`, and `cuda True` if a GPU is present.
`PYTHONNOUSERSITE=1` stops pip from treating packages in *your* `~/.local/lib/python3.10/site-packages` as already installed. Without it, pip skips them, the env only works for you, and the service fails with errors like `No module named 'dateutil'`.

Check that nothing is missing: `PYTHONNOUSERSITE=1 /srv/parasight/env/bin/python -m pip check` should print `No broken requirements found.`

Without a GPU, you can install the CPU build of PyTorch instead (`--index-url https://download.pytorch.org/whl/cpu`).

Optionally, pin what works: `/srv/parasight/env/bin/pip freeze > requirements.lock.txt`.

### 4.4 Config

```bash
sudo mkdir -p /etc/parasight
sudo cp /opt/parasight-AMA/deploy/parasight.env.example /etc/parasight/parasight.env
sudoedit /etc/parasight/parasight.env        # APP_HOST / APP_PORT / paths
```

### 4.5 Pre-download model weights (optional, recommended)

Otherwise the first user request waits for the downloads.

```bash
cd /opt/parasight-AMA
sudo -u parasight env $(grep -v '^#' /etc/parasight/parasight.env | xargs) \
  /srv/parasight/env/bin/python -c "
from app.pipeline.cellpose import _get_cellpose_model; _get_cellpose_model()
from app.pipeline.stardist import _get_stardist_model; _get_stardist_model()
print('models OK')"
```

### 4.6 Smoke test the CLI as the service user

```bash
cd /opt/parasight-AMA
sudo -u parasight env $(grep -v '^#' /etc/parasight/parasight.env | xargs) \
  /srv/parasight/env/bin/parasight process test-imgs/Snap-12879.tiff \
  --output /srv/parasight/data/outputs --job-id smoketest
```

## 5. Start on boot: systemd

A startup script isn't needed. systemd handles boot start, restart on crash, logging, and ordering after the data mount. The unit calls the env's `uvicorn` binary directly, so no conda activation or login shell is involved.

```bash
sudo cp /opt/parasight-AMA/deploy/parasight.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now parasight      # enable = start at boot, --now = start now
systemctl status parasight
sudo journalctl -u parasight -f                 # logs
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8010/   # expect 200 (not curl -I: HEAD gets 405)
```

Key points of the unit (`deploy/parasight.service`):
- `RequiresMountsFor=/srv/parasight`: won't start before the data volume is mounted.
- `WorkingDirectory=/opt/parasight-AMA`: the repository root.
- `--workers 1`: one copy of the models in memory/GPU.
- `Restart=on-failure`, `UMask=0027`, plus sandboxing: `ProtectSystem=full`, `ProtectHome=true`, `PrivateTmp=true`, and write access only to `/srv/parasight`. `PrivateDevices` is deliberately left off because it would hide `/dev/nvidia*`.

### Updating the app

```bash
cd /opt/parasight-AMA && sudo git pull
# only if pyproject.toml deps changed:
#   sudo PYTHONNOUSERSITE=1 /srv/parasight/env/bin/pip install -e .
sudo systemctl restart parasight
```

A restart interrupts the job that is running: it is marked as failed and the user can retry it from its status page. Queued jobs are picked up again automatically. Check for a running job before restarting: `grep -l '"running"' /srv/parasight/data/outputs/*/status.json`.

## 6. Network access and coexistence

The app (uvicorn) listens on `APP_HOST:APP_PORT` (default `0.0.0.0:8010`) and doesn't use ports 80/443. Before choosing a port, check that it's free with `ss -ltn`. To change it, edit `APP_PORT` and restart the service.

The app has no login. Access control is done by a reverse proxy, and the firewall makes sure nobody can reach the app except through it. Choose one of the layouts below.

### A. Reverse proxy on another server (Apache, with basic auth)

```
browser --HTTPS :443--> proxy server (Apache, login) --HTTP :8010--> app server (parasight)
```

**What runs where**
- **App server (this one):** only the `parasight` service. Nothing else is needed: no nginx, no Apache, no `apache2-utils`.
- **Proxy server:** Apache with `mod_ssl`, `mod_proxy`, `mod_proxy_http`, `mod_headers`, `mod_reqtimeout`, plus `htpasswd` (package `apache2-utils`, usually installed with Apache).

**Ports**
| Where | Port | Open to |
|---|---|---|
| Proxy server | 443 (or another HTTPS port) | users |
| App server | 8010 | **only the proxy server's IP** |

The app must listen on an address the proxy can reach, so `APP_HOST=127.0.0.1` does **not** work in this layout (it would refuse the proxy's connections). Set `APP_HOST` to the app server's internal IP (or `0.0.0.0`) and restrict the port with a firewall.

**Why the firewall matters.** The login exists only in the proxy. Anyone who can open `http://<app-server>:8010` directly skips it. "Closing the direct port" therefore means a firewall rule on the app server that allows 8010 from the proxy only and drops everything else (replace any existing rule that allows a wider range):

```bash
sudo ufw allow from <proxy-ip> to any port 8010 proto tcp comment 'parasight-AMA via proxy'
sudo ufw delete allow from <trusted-CIDR> to any port 8010 proto tcp   # remove older, wider rule if present
sudo ufw status numbered
```

Also check the network's own firewall or security group, if there is one. Test from a third machine: `curl -m 3 http://<app-server>:8010/` must time out, while the same command from the proxy server must return the page.

**Proxy server setup**
1. Create the users (`-c` creates the file, so use it for the first user only; the command asks for the password):

   ```bash
   sudo htpasswd -c -B /etc/apache2/parasight.htpasswd lab1
   sudo htpasswd -B /etc/apache2/parasight.htpasswd lab2
   sudo chown root:www-data /etc/apache2/parasight.htpasswd && sudo chmod 640 /etc/apache2/parasight.htpasswd
   ```

   See "Managing users" below for adding, changing and removing users later. Apache rereads the file on each request, with no restart.
2. Enable the modules and the site, using `deploy/apache-parasight.conf` (edit `ServerName`, the certificate paths and `APP_SERVER`):

   ```bash
   sudo a2enmod ssl proxy proxy_http headers reqtimeout
   sudo cp apache-parasight.conf /etc/apache2/sites-available/parasight.conf
   sudo a2ensite parasight && sudo apachectl configtest && sudo systemctl reload apache2
   ```
3. Check: `curl -sk -o /dev/null -w '%{http_code}\n' https://<proxy>/` gives `401`; with `-u lab1:<password>` it gives `200`.

**Managing users** (run on the proxy server; the file is `/etc/apache2/parasight.htpasswd`, the path set by `AuthUserFile` in the Apache config)

| Task | Command |
|---|---|
ing First user (creates the file) | `sudo htpasswd -c -B /etc/apache2/parasight.htpasswd lab1` |
| Add a user | `sudo htpasswd -B /etc/apache2/parasight.htpasswd lab2` |
| Change a password | the same command as adding; it overwrites the existing entry |
| Remove a user | `sudo htpasswd -D /etc/apache2/parasight.htpasswd lab2` |
| List users | `cut -d: -f1 /etc/apache2/parasight.htpasswd` |
| Verify a password | `sudo htpasswd -v /etc/apache2/parasight.htpasswd lab1` |

- `-B` stores the password as bcrypt (recommended; the default is a weaker MD5 variant). `-c` **overwrites the whole file**, so use it only for the very first user.
- The command prompts for the password twice. For scripts, pipe it in so it doesn't end up in the shell history or `ps`: `printf '%s' "$PASS" | sudo htpasswd -iB /etc/apache2/parasight.htpasswd lab3`.
- If you create the file as root, make sure Apache can still read it: `sudo chown root:www-data ... && sudo chmod 640 ...` (see step 1). A wrong owner shows up as `401` for every user and a permission error in `/var/log/apache2/error.log`.
- Changes take effect on the next request. A removed user's open browser session stops working at its next request, because basic auth is re-sent every time.
- To restrict some pages to specific users, or to allow only some of the listed users, add a `Require user lab1 lab2` line instead of `Require valid-user` in the Apache config.
- Test a new login: `curl -sk -o /dev/null -w '%{http_code}\n' -u lab3:<password> https://<proxy>/` should give `200`.

**Caveats**
- **Use HTTPS on the public side.** Basic auth sends the password with every request. Traffic between the proxy and the app is plain HTTP, so it should stay on a trusted network (or use a VPN/SSH tunnel, or TLS in front of the app).
- **Own host name or port, no sub-path.** Pages link to `/upload`, `/jobs/...` and so on, so `https://host/parasight/` does not work.
- **Long uploads and timeouts.** Apache's default `mod_reqtimeout` and `ProxyTimeout` can cut off slow multi-GB uploads; the config raises them. Jobs themselves run in the background, so pages stay short.
- **Logins are shared.** The app doesn't record who uploaded a job; every logged-in user can open any job whose link they have. Two browsers can use the same username at once and share the same queue.
- **Client IPs in the app logs** will all be the proxy's. Apache adds `X-Forwarded-For`; the access log on the proxy has the real addresses.

### B. No proxy (trusted network only)

Open the port only to a trusted range. There is no login.

```bash
sudo ufw allow from <trusted-CIDR> to any port 8010 proto tcp comment 'parasight-AMA'
```

Users open `http://<server>:8010`.

### C. SSH tunnel

Set `APP_HOST=127.0.0.1`; users run `ssh -L 8010:127.0.0.1:8010 <user>@<server>` and open `http://127.0.0.1:8010`.

### D. Proxy on the same server

Keep `APP_HOST=127.0.0.1` and proxy to it from the same machine. Then the app isn't reachable from the network at all, so no firewall rule is needed for 8010. The Apache config works unchanged with `APP_SERVER` set to `127.0.0.1`.

## 7. Storage management

- Disk use per job ≈ upload (`uploads/`) + outputs, plus, while it runs, the spooled upload in `tmp/` and the extracted copy of a ZIP in `processing/`.
- The app deletes `processing/<job>` when a job ends, but never deletes uploads or outputs. Install the cleanup job, which deletes jobs (their `uploads/` and `outputs/` folders) after 30 days (adjust the number), plus anything left in `processing/` or `tmp/` for more than 2 days:

```bash
( sudo -u parasight crontab -l 2>/dev/null; \
  echo '30 3 * * * DATA_DIR=/srv/parasight/data /opt/parasight-AMA/deploy/parasight-cleanup.sh 30' ) \
  | sudo -u parasight crontab -
```

- Monitor it with `du -sh /srv/parasight/data/* /srv/parasight/tmp`.
- The script finds `tmp/` next to `data/` (override with `TMP_DIR=...`). It still cleans the old `data/temp` and `data/tmp` if they exist.
- If results must be kept, back up `data/outputs/`. Users already have their own copies of `uploads/`.

## 8. Customizing paths

The defaults appear in four places: `deploy/parasight.env.example`, `deploy/parasight.service` (`ExecStart`, `WorkingDirectory`, `RequiresMountsFor`, `ReadWritePaths`), `deploy/parasight-cleanup.sh`, and the commands in this file. To use another data root, for example `/data/parasight`:

```bash
sed 's#/srv/parasight#/data/parasight#g' deploy/parasight.service   | sudo tee /etc/systemd/system/parasight.service
sed 's#/srv/parasight#/data/parasight#g' deploy/parasight.env.example | sudo tee /etc/parasight/parasight.env
```

Then use the new path in the `useradd`/`mkdir`/`conda` commands above and in the cleanup cron line.

## 9. Known limitations

1. **Upload limit applies after spooling.** `MAX_UPLOAD_MB` is checked while the file is saved, but the whole body has already been spooled to `$TMPDIR`. To stop oversized uploads earlier, cap them at a reverse proxy (`client_max_body_size`).
2. **A restart interrupts the running job.** It is marked as failed and must be retried; queued jobs resume automatically.
3. **Authentication is done by the proxy, not the app.** Without the proxy login (section 6) nothing but the job UUIDs protects results. With it, all users see the same queue, and logins are shared, not per-person.
4. `app/core/config.py` reads environment variables only, not a `.env` file. Under systemd this is handled by `EnvironmentFile=`.

## 10. Quick reference

| What | Where / command |
|---|---|
| URL | `http://<server>:8010` |
| Code | `/opt/parasight-AMA` |
| Env | `/srv/parasight/env` |
| Data | `/srv/parasight/data/{uploads,outputs,processing}`, scratch in `/srv/parasight/tmp` |
| Config | `/etc/parasight/parasight.env` |
| Service | `sudo systemctl {status,restart,stop} parasight` |
| Logs | `sudo journalctl -u parasight -f` |
| Cleanup | `parasight` user's crontab → `deploy/parasight-cleanup.sh` |
