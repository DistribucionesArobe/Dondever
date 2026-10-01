"""Render local visual QA samples for the Instagram templates (no API calls)."""
import asyncio
import os
import subprocess
import tempfile
import time
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
import generate_instagram as generator

OUT = Path(__file__).parent / "previews" / "instagram"
OUT.mkdir(parents=True, exist_ok=True)

def game(away, home, league="Liga MX", slug="liga-mx", time="19:00", channels=None, **kw):
    channels = channels or {}
    def badge(name):
        words = [word for word in name.replace("-", " ").split() if word.lower() not in {"fc", "cf", "club", "de", "la", "los", "the"}]
        return "".join(word[0] for word in words[:3]).upper() or name[:3].upper()
    return {
        "league": league, "league_slug": slug,
        "away": {"name": away, "short": badge(away), "logo": ""},
        "home": {"name": home, "short": badge(home), "logo": ""},
        "time": time, "date_label": "SÁB 03", "preview": True,
        "mx_channels": channels.get("mx", []), "us_channels": channels.get("us", []),
        "open_channels": channels.get("open", []),
        **kw,
    }

SAMPLES = {
    "01_partido": [game("América", "Chivas", channels={"mx": ["Canal 5", "TUDN"], "open": ["Canal 5"], "us": ["TUDN USA"]})],
    "02_tres_partidos": [
        game("Tigres", "Pumas", channels={"mx": ["TUDN"]}),
        game("Atlas", "León", time="21:00", channels={"mx": ["ViX"]}),
        game("Pachuca", "Toluca", time="21:10"),
    ],
    "03_agenda_extensa": [
        game(a, h, time=f"{16+i:02d}:00") for i, (a, h) in enumerate([
            ("América", "Chivas"), ("Tigres", "Pumas"), ("Atlas", "León"),
            ("Pachuca", "Toluca"), ("Monterrey", "Santos"), ("Cruz Azul", "Puebla"),
            ("Necaxa", "Querétaro"), ("San Luis", "Mazatlán"), ("Tijuana", "Juárez"),
        ])
    ],
    "04_nombres_largos": [
        game("Club Universidad Nacional", "Club Deportivo Guadalajara", time="20:45"),
        game("Los Angeles Football Club", "Inter Miami CF", league="MLS", slug="mls", time="21:30"),
    ],
    "05_datos_incompletos": [game("Equipo por confirmar", "Chivas", time="", channels={})],
}

async def main():
    date = "20261003"
    use_playwright = generator.async_playwright is not None
    chrome = Path(os.getenv("CHROME_BIN", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    if not use_playwright and not chrome.exists():
        raise RuntimeError("Install Playwright or set CHROME_BIN to a local Chrome executable.")
    template = Environment(
        loader=FileSystemLoader(str(Path(__file__).parent / "templates")), autoescape=True
    ).get_template("instagram_image.html")
    for name, games in SAMPLES.items():
        headline = "Dónde ver a Chivas" if name == "01_partido" else ""
        if use_playwright:
            paths = await generator.generate_images(
                games, date, str(OUT / f"{name}.png"), headline=headline
            )
        else:
            # Use the installed Chrome app as a temporary local renderer when the
            # optional Playwright package is unavailable on a developer machine.
            leagues = {}
            for item in games:
                leagues.setdefault(item.get("league_slug", item.get("league", "")), []).append(item)
            pages = [page for items in leagues.values()
                     for start in range(0, len(items), 3)
                     for page in [items[start:start + 3]]] or [[]]
            paths = []
            with tempfile.TemporaryDirectory(prefix="dondever-preview-") as tmp:
                profile = Path(tmp) / "profile"
                for index, page_games in enumerate(pages, 1):
                    path = OUT / f"{name}{'_' + str(index) if index > 1 else ''}.png"
                    data = generator.prepare_template_data(
                        page_games, date, headline=headline,
                        page_index=index, page_total=len(pages)
                    )
                    html_path = Path(tmp) / f"{name}-{index}.html"
                    html_path.write_text(template.render(**data), encoding="utf-8")
                    if path.exists():
                        path.unlink()
                    process = subprocess.Popen([
                        str(chrome), "--headless=new", "--no-sandbox", "--disable-gpu",
                        "--hide-scrollbars", "--no-first-run", "--disable-extensions",
                        "--disable-background-mode", "--disable-background-networking",
                        f"--user-data-dir={profile}", "--window-size=1080,1350",
                        "--force-device-scale-factor=1", "--virtual-time-budget=2500",
                        f"--screenshot={path}", html_path.as_uri(),
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    deadline = time.monotonic() + 25
                    while time.monotonic() < deadline:
                        if path.exists() and path.stat().st_size > 0:
                            break
                        if process.poll() is not None:
                            raise RuntimeError(f"Chrome preview failed for {name} (exit {process.returncode})")
                        time.sleep(0.25)
                    else:
                        process.terminate()
                        process.wait(timeout=5)
                        raise TimeoutError(f"Chrome did not render {name}")
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                    paths.append(str(path))
        print(f"{name}: {', '.join(Path(p).name for p in paths)}")

if __name__ == "__main__":
    asyncio.run(main())
