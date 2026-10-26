"""
DondeVer — Async PostgreSQL persistence layer.

Stores every game seen from ESPN so team / league / channel pages
always have historical data, even when today's API returns nothing.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column, String, DateTime, Text, Integer, Boolean,
    Index, text,
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base

from config import DATABASE_URL

log = logging.getLogger("dondever.db")

# ── Engine & session ────────────────────────────────────────
engine = create_async_engine(DATABASE_URL, echo=False, pool_size=5, max_overflow=5)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()


# ── Model ───────────────────────────────────────────────────
class GameRecord(Base):
    __tablename__ = "games"

    # ESPN event ID — unique per game
    id = Column(String(64), primary_key=True)
    # Date the game was played (YYYYMMDD)
    game_date = Column(String(8), nullable=False, index=True)
    # ISO timestamp from ESPN
    date_utc = Column(DateTime(timezone=True), nullable=True)
    # League
    league_slug = Column(String(64), nullable=False, index=True)
    league_name = Column(String(128), nullable=False, default="")
    sport = Column(String(32), nullable=False, default="")
    # Teams
    home_name = Column(String(128), nullable=False)
    away_name = Column(String(128), nullable=False)
    home_short = Column(String(16), default="")
    away_short = Column(String(16), default="")
    home_logo = Column(Text, default="")
    away_logo = Column(Text, default="")
    # Scores (nullable — pre-game has no score)
    home_score = Column(String(8), default="")
    away_score = Column(String(8), default="")
    # Status: pre / in / post
    state = Column(String(8), default="pre")
    # Venue
    venue = Column(String(256), default="")
    # Channels (JSON array of channel names)
    channels_json = Column(Text, default="[]")
    # Lo mismo, pero congelado: [{"canal","pais","tipo","origen"}] TAL COMO ERAN
    # el día que se guardó el partido.
    #
    # channels_json sólo tiene nombres sueltos; el país y el tipo se resuelven
    # al pintar, consultando CHANNEL_ALIASES *de hoy*. Eso significa que el
    # archivo se reinterpreta solo: el día que un canal cambie de manos, de país
    # o de tipo, todos los partidos viejos empiezan a describirse mal, hacia
    # atrás, sin que nadie se entere. Es la misma raíz del caso Fox Sports —
    # el canal caduco no estaba en el código, estaba congelado en la base.
    #
    # Esta columna todavía no la lee nadie, y es a propósito: para las filas
    # viejas está vacía y lo estará durante meses. Su valor es que a partir de
    # hoy el histórico de transmisiones queda bien guardado, y eso no se puede
    # reconstruir hacia atrás si no se empieza.
    channels_meta = Column(Text, default="[]")
    # Recap (JSON: headline, mvp, winner)
    recap_json = Column(Text, default="{}")
    # Winner name (denormalized for easy queries)
    winner = Column(String(128), default="")
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_games_home", "home_name"),
        Index("ix_games_away", "away_name"),
        Index("ix_games_league_date", "league_slug", "game_date"),
    )


# ── Init (create tables) ───────────────────────────────────
async def init_db():
    """Create tables if they don't exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all crea tablas, NO añade columnas a una tabla que ya existe.
        # Sin esto, una columna nueva sólo aparecería en una base vacía y en
        # producción el INSERT fallaría con "column does not exist".
        #
        # ADD COLUMN IF NOT EXISTS con default constante es instantáneo en
        # Postgres (no reescribe la tabla) y se puede correr en cada arranque.
        for columna, tipo in (("channels_meta", "TEXT DEFAULT '[]'"),):
            try:
                await conn.execute(
                    text(f"ALTER TABLE games ADD COLUMN IF NOT EXISTS {columna} {tipo}"))
            except Exception as e:
                log.warning("No se pudo asegurar la columna %s: %s", columna, e)
    log.info("DB tables ensured")


# ── Upsert games ────────────────────────────────────────────
async def persist_games(games: list[dict], date_str: str):
    """
    Save a batch of games to the database.
    Uses INSERT … ON CONFLICT UPDATE so we always keep the latest data.
    """
    if not games:
        return

    async with async_session() as session:
        async with session.begin():
            for g in games:
                emisiones = g.get("broadcasts", []) or []
                channels = [b.get("channel", "") for b in emisiones]
                # El país y el tipo salen del `info` que ya trae cada emisión
                # (resuelto contra CHANNEL_ALIASES al construir el partido), así
                # que aquí no se consulta nada: se guarda lo que era verdad hoy.
                meta = []
                for b in emisiones:
                    nombre = b.get("channel") or ""
                    if not nombre:
                        continue
                    info = b.get("info") or {}
                    meta.append({
                        "canal": nombre,
                        "pais": info.get("country") or "",
                        "tipo": info.get("type") or "",
                        # De dónde salió: espn, sportsdb_event, league_default
                        # o rights_estimate. Importa, porque un default de liga
                        # no es lo mismo que una transmisión confirmada, y al
                        # mirar el archivo dentro de un año no habrá forma de
                        # distinguirlos si no se guarda.
                        "origen": b.get("source") or "",
                    })
                recap = g.get("recap", {})
                winner = recap.get("winner", "")

                # Parse ISO date
                date_utc = None
                try:
                    date_utc = datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
                except Exception:
                    pass

                await session.execute(
                    text("""
                        INSERT INTO games (
                            id, game_date, date_utc, league_slug, league_name, sport,
                            home_name, away_name, home_short, away_short,
                            home_logo, away_logo,
                            home_score, away_score, state, venue,
                            channels_json, channels_meta, recap_json, winner,
                            created_at, updated_at
                        ) VALUES (
                            :id, :game_date, :date_utc, :league_slug, :league_name, :sport,
                            :home_name, :away_name, :home_short, :away_short,
                            :home_logo, :away_logo,
                            :home_score, :away_score, :state, :venue,
                            :channels_json, :channels_meta, :recap_json, :winner,
                            NOW(), NOW()
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            home_score = EXCLUDED.home_score,
                            away_score = EXCLUDED.away_score,
                            state = EXCLUDED.state,
                            channels_json = EXCLUDED.channels_json,
                            -- Sólo se pisa si lo nuevo trae algo. Un partido se
                            -- reescribe muchas veces el día que se juega, y si
                            -- una de esas pasadas viene sin emisiones (la API
                            -- ya no devuelve broadcast de un partido acabado)
                            -- borraría el mapa que sí habíamos capturado.
                            channels_meta = CASE
                                WHEN EXCLUDED.channels_meta IN ('[]', '') THEN games.channels_meta
                                ELSE EXCLUDED.channels_meta END,
                            recap_json = EXCLUDED.recap_json,
                            winner = EXCLUDED.winner,
                            updated_at = NOW()
                    """),
                    {
                        "id": g["id"],
                        "game_date": date_str,
                        "date_utc": date_utc,
                        "league_slug": g.get("league_slug", ""),
                        "league_name": g.get("league_name", ""),
                        "sport": g.get("sport", ""),
                        "home_name": g.get("home", {}).get("name", ""),
                        "away_name": g.get("away", {}).get("name", ""),
                        "home_short": g.get("home", {}).get("short", ""),
                        "away_short": g.get("away", {}).get("short", ""),
                        "home_logo": g.get("home", {}).get("logo", ""),
                        "away_logo": g.get("away", {}).get("logo", ""),
                        "home_score": g.get("home", {}).get("score", ""),
                        "away_score": g.get("away", {}).get("score", ""),
                        "state": g.get("status", {}).get("state", "pre"),
                        "venue": g.get("venue", ""),
                        "channels_json": json.dumps(channels, ensure_ascii=False),
                        "channels_meta": json.dumps(meta, ensure_ascii=False),
                        "recap_json": json.dumps(recap, ensure_ascii=False),
                        "winner": winner,
                    }
                )
    log.debug("Persisted %d games for %s", len(games), date_str)


# ── Queries ─────────────────────────────────────────────────
async def get_team_history(team_name: str, limit: int = 10, sport: str = "") -> list[dict]:
    """Partidos terminados de un equipo. `sport` acota por deporte.

    Mismo motivo que en get_team_upcoming: el nombre se busca con ILIKE, y
    "Caracas" (fútbol) casa con "Leones del Caracas" (béisbol).
    """
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT id, game_date, league_slug, league_name, sport,
                       home_name, away_name, home_short, away_short,
                       home_logo, away_logo,
                       home_score, away_score, state, venue,
                       channels_json, recap_json, winner
                FROM games
                WHERE state = 'post'
                  AND (home_name ILIKE :team OR away_name ILIKE :team)
                  AND (:sport = '' OR sport = :sport)
                ORDER BY game_date DESC, date_utc DESC
                LIMIT :lim
            """),
            {"team": f"%{team_name}%", "lim": limit, "sport": sport or ""}
        )
        rows = result.mappings().all()
        return [dict(r) for r in rows]


async def get_team_upcoming(team_name: str, limit: int = 5, sport: str = "") -> list[dict]:
    """Próximos partidos de un equipo. Próximo = EN EL FUTURO, no solo state='pre'.

    `sport` acota por deporte y no es opcional en la práctica: el nombre se
    busca con ILIKE '%nombre%', y hay clubes cuyo nombre está DENTRO del de
    otro de otro deporte. Visto en producción el 26/10/2026:

      /equipo/caracas-fc  (fútbol, Liga FUTVE)
        → "PRÓXIMO PARTIDO: Leones del Caracas vs Tigres de Aragua"  ← LVBP
        → canales: Meridiano TV, Televen, BeisbolPlay, 1 Baseball    ← béisbol
        → title: "Dónde ver Caracas vs Tigres de Aragua"

    "Caracas" casa con "Leones del Caracas". Se acota por DEPORTE y no por
    liga a propósito: filtrando por liga, un equipo de Liga MX perdería sus
    partidos de Concachampions y de Leagues Cup, que son otra liga pero el
    mismo deporte.

    El filtro de fecha faltaba y el daño era grande. Medido en producción el
    06/10/2026:

      /equipo/tigres  → "Próximos partidos" con juegos del 26 de AGOSTO
      /equipo/dodgers → "Próximos partidos" con juegos del 24 de SEPTIEMBRE
      /liga/liga-mx   → "Próxima jornada" con la jornada del 25 de septiembre

    Por qué pasaba: persist_games solo actualiza los partidos que trae
    get_todays_games ESE día. Si una fila se escribió como 'pre' y el día del
    partido nadie la volvió a escribir —el proceso se reinició, la escritura
    falló, nadie abrió la página—, se queda en 'pre' para siempre. Y como la
    consulta ordenaba por fecha ASCENDENTE, esas filas zombi salían PRIMERO.

    Y arrastraban su channels_json viejo. De ahí venía "Fox Sports MX" en las
    páginas de Tigres y de Liga MX después de que lo quitamos del código: el
    canal caduco no estaba en el código, estaba congelado en la base de datos.

    El arreglo va en la consulta y no en el pipeline a propósito: un partido
    que ya pasó no es un próximo partido, diga lo que diga su columna state.
    Así queda bien aunque la escritura vuelva a fallar algún día.

    Las 4 horas de gracia son para que un partido en curso siga apareciendo
    mientras se juega, en lugar de desaparecer al minuto de empezar.
    """
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT id, game_date, league_slug, league_name,
                       home_name, away_name, home_short, away_short,
                       home_logo, away_logo,
                       channels_json, date_utc
                FROM games
                WHERE state = 'pre'
                  AND date_utc IS NOT NULL
                  AND date_utc >= NOW() - INTERVAL '4 hours'
                  AND (home_name ILIKE :team OR away_name ILIKE :team)
                  AND (:sport = '' OR sport = :sport)
                ORDER BY date_utc ASC
                LIMIT :lim
            """),
            {"team": f"%{team_name}%", "lim": limit, "sport": sport or ""}
        )
        rows = result.mappings().all()
        return [dict(r) for r in rows]


async def get_team_channels(team_name: str, sport: str = "") -> list[tuple[str, int]]:
    """Canales en los que más se ha transmitido un equipo, del último año.

    La ventana de 12 meses faltaba, y sin ella el bloque "Canales donde más se
    transmite" cuenta toda la historia — incluidos canales que ya perdieron los
    derechos. Visto en producción el 06/10/2026:

      /equipo/tigres    → "Fox Sports MX (42)" en PRIMER LUGAR
      /equipo/dodgers   → "Fox Sports MX (60)"
      /equipo/cruz-azul → "Fox Sports MX (15)"

    Fox Sports México perdió Liga MX y MLB; para el lector de hoy ese primer
    lugar es una recomendación equivocada, aunque históricamente sea cierto
    que ahí se vieron esos partidos.

    Doce meses es el compromiso: suficiente para que el conteo signifique algo
    y poco para que un cambio de derechos salga de la lista en una temporada.
    """
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT ch, COUNT(*) as cnt
                FROM games,
                     LATERAL jsonb_array_elements_text(channels_json::jsonb) AS ch
                WHERE (home_name ILIKE :team OR away_name ILIKE :team)
                  AND date_utc IS NOT NULL
                  AND date_utc >= NOW() - INTERVAL '12 months'
                  AND (:sport = '' OR sport = :sport)
                GROUP BY ch
                ORDER BY cnt DESC
                LIMIT 5
            """),
            {"team": f"%{team_name}%", "sport": sport or ""}
        )
        return [(r[0], r[1]) for r in result.all()]


async def get_h2h(team_a: str, team_b: str, limit: int = 30) -> list[dict]:
    """Enfrentamientos entre dos equipos, del más reciente al más viejo.

    Sale de nuestra propia tabla `games`, no de una API. Eso tiene una
    consecuencia que la página TIENE que decir: el historial empieza el día que
    empezamos a guardar partidos, no en la fundación del club. Un "Clásico
    Nacional: 412 juegos" sería mentira; "18 desde que lo registramos, con la
    fecha del primero" es verdad y además se vuelve más valioso cada temporada.

    Se consulta en los dos sentidos porque el local cambia cada vez.
    """
    if not team_a or not team_b:
        return []
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT id, game_date, date_utc, league_slug, league_name, sport,
                       home_name, away_name, home_short, away_short,
                       home_logo, away_logo, home_score, away_score,
                       venue, winner
                FROM games
                WHERE state = 'post'
                  AND home_score IS NOT NULL AND away_score IS NOT NULL
                  AND ((home_name ILIKE :a AND away_name ILIKE :b)
                    OR (home_name ILIKE :b AND away_name ILIKE :a))
                ORDER BY game_date DESC, date_utc DESC
                LIMIT :lim
            """),
            {"a": f"%{team_a}%", "b": f"%{team_b}%", "lim": limit},
        )
        return [dict(r) for r in result.mappings().all()]


async def get_league_history(league_slug: str, limit: int = 20) -> list[dict]:
    """Get recent finished games for a league."""
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT id, game_date, league_slug, league_name,
                       home_name, away_name, home_short, away_short,
                       home_logo, away_logo,
                       home_score, away_score, venue, winner, recap_json
                FROM games
                WHERE state = 'post' AND league_slug = :slug
                ORDER BY game_date DESC, date_utc DESC
                LIMIT :lim
            """),
            {"slug": league_slug, "lim": limit}
        )
        rows = result.mappings().all()
        return [dict(r) for r in rows]
