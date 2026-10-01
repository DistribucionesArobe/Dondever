"""Render local visual QA samples for the Instagram templates (no API calls)."""
import asyncio
from pathlib import Path
from generate_instagram import generate_images

OUT = Path(__file__).parent / "previews" / "instagram"
OUT.mkdir(parents=True, exist_ok=True)

def game(away, home, league="Liga MX", slug="liga-mx", time="19:00", channels=None, **kw):
    channels = channels or {}
    return {
        "league": league, "league_slug": slug,
        "away": {"name": away, "short": "VIS", "logo": ""},
        "home": {"name": home, "short": "LOC", "logo": ""},
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
    for name, games in SAMPLES.items():
        paths = await generate_images(games, date, str(OUT / f"{name}.png"),
                                      headline="Dónde ver a Chivas" if name == "01_partido" else "")
        print(f"{name}: {', '.join(Path(p).name for p in paths)}")

if __name__ == "__main__":
    asyncio.run(main())
