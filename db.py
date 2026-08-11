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
                channels = [b.get("channel", "") for b in g.get("broadcasts", [])]
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
                            channels_json, recap_json, winner,
                            created_at, updated_at
                        ) VALUES (
                            :id, :game_date, :date_utc, :league_slug, :league_name, :sport,
                            :home_name, :away_name, :home_short, :away_short,
                            :home_logo, :away_logo,
                            :home_score, :away_score, :state, :venue,
                            :channels_json, :recap_json, :winner,
                            NOW(), NOW()
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            home_score = EXCLUDED.home_score,
                            away_score = EXCLUDED.away_score,
                            state = EXCLUDED.state,
                            channels_json = EXCLUDED.channels_json,
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
                        "recap_json": json.dumps(recap, ensure_ascii=False),
                        "winner": winner,
                    }
                )
    log.debug("Persisted %d games for %s", len(games), date_str)


# ── Queries ─────────────────────────────────────────────────
async def get_team_history(team_name: str, limit: int = 10) -> list[dict]:
    """Get recent finished games for a team (home or away)."""
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
                ORDER BY game_date DESC, date_utc DESC
                LIMIT :lim
            """),
            {"team": f"%{team_name}%", "lim": limit}
        )
        rows = result.mappings().all()
        return [dict(r) for r in rows]


async def get_team_upcoming(team_name: str, limit: int = 5) -> list[dict]:
    """Get upcoming (pre-state) games for a team."""
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT id, game_date, league_slug, league_name,
                       home_name, away_name, home_short, away_short,
                       home_logo, away_logo,
                       channels_json, date_utc
                FROM games
                WHERE state = 'pre'
                  AND (home_name ILIKE :team OR away_name ILIKE :team)
                ORDER BY date_utc ASC
                LIMIT :lim
            """),
            {"team": f"%{team_name}%", "lim": limit}
        )
        rows = result.mappings().all()
        return [dict(r) for r in rows]


async def get_team_channels(team_name: str) -> list[tuple[str, int]]:
    """Get most frequent channels for a team's games."""
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT ch, COUNT(*) as cnt
                FROM games,
                     LATERAL jsonb_array_elements_text(channels_json::jsonb) AS ch
                WHERE (home_name ILIKE :team OR away_name ILIKE :team)
                GROUP BY ch
                ORDER BY cnt DESC
                LIMIT 5
            """),
            {"team": f"%{team_name}%"}
        )
        return [(r[0], r[1]) for r in result.all()]


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
