# Mahansco Uptime

External uptime monitoring for public service reachability.

The monitor runs through GitHub Actions and keeps operational alerts private. Secrets must be
provided through GitHub configuration and must never be committed.

## Main files

- `monitor.py` — monitoring and alerting logic
- `targets.json` — monitoring configuration
- `.github/workflows/monitor.yml` — scheduled execution
- `docs/index.html` — optional status page

## Local checks

```sh
python3 -m unittest discover -s tests
python3 -m py_compile monitor.py
```
