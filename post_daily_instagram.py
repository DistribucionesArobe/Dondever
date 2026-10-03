"""One Render cron, two independent Instagram formats at fixed UTC hours."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    hour = datetime.now(timezone.utc).hour
    scripts = {13: 'post_instagram.py', 18: 'post_reel.py'}
    script = scripts.get(hour)
    if script is None:
        raise SystemExit('Outside scheduled publication hours; nothing published')
    print(f'Instagram scheduled format: {script}',flush=True)
    subprocess.run([sys.executable, str(Path(__file__).with_name(script))],check=True)
