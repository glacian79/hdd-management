# hdd-management

Small web dashboard showing free space on this machine's drives: one pie chart per drive,
with a hover breakdown of what is using the space (videos, music, pictures, text, programs, archives).

- `server.py` - Python standard-library server (port 8085): `/`, `/api/storage`, `/api/breakdown`, `POST /api/rescan`
- `index.html` - the page (inline SVG pies, auto-refresh every 30s)
- `storage-dashboard.service` - systemd unit
- Breakdown is scanned in the background, cached in `breakdown-cache.json` (not committed), and rescanned daily at 02:00.

Install: `sudo cp storage-dashboard.service /etc/systemd/system/ && sudo systemctl enable --now storage-dashboard`
