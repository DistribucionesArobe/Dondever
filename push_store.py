"""
Suscripciones push por equipo / partido (OneSignal subscription ids).

OneSignal free limita los "tags" por usuario, así que el mapeo
suscripción → equipos seguidos vive aquí (JSON en el mismo disco que
subscribers.json) y al enviar se usa include_subscription_ids.

Estructura:
  {
    "<subscription_id>": {"teams": ["america", "dodgers"], "games": ["401772001"],
                          "updated": "2026-09-14T20:00:00", "ua": "..."}
  }
"""

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("dondever.push_store")

_BASE = Path(os.getenv("SUBSCRIBERS_FILE", "subscribers.json")).parent
_BASE.mkdir(parents=True, exist_ok=True)
PUSH_FILE = _BASE / "push_subs.json"
_lock = threading.Lock()
_MAX_TEAMS = 40
_MAX_GAMES = 60


def _load() -> dict:
    try:
        with open(PUSH_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    tmp = PUSH_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, PUSH_FILE)


def _clean_list(items, maxlen: int) -> list[str]:
    out = []
    for x in items or []:
        x = str(x).strip().lower()[:64]
        if x and x not in out:
            out.append(x)
    return out[:maxlen]


def upsert(sub_id: str, teams=None, games=None, add_games=None, ua: str = "") -> dict:
    """Guarda/actualiza la suscripción. `teams`/`games` reemplazan; `add_games` agrega."""
    sub_id = (sub_id or "").strip()
    if not sub_id or len(sub_id) > 80:
        raise ValueError("sub_id inválido")
    with _lock:
        data = _load()
        cur = data.get(sub_id) or {"teams": [], "games": []}
        if teams is not None:
            cur["teams"] = _clean_list(teams, _MAX_TEAMS)
        if games is not None:
            cur["games"] = _clean_list(games, _MAX_GAMES)
        if add_games:
            cur["games"] = _clean_list(list(cur.get("games", [])) + list(add_games), _MAX_GAMES)
        cur["updated"] = datetime.utcnow().isoformat(timespec="seconds")
        if ua:
            cur["ua"] = ua[:120]
        data[sub_id] = cur
        _save(data)
        return cur


def remove(sub_id: str) -> bool:
    with _lock:
        data = _load()
        if sub_id in data:
            del data[sub_id]
            _save(data)
            return True
        return False


def get(sub_id: str) -> dict | None:
    return _load().get(sub_id)


def subs_for(team_slugs=None, game_id: str = "") -> list[str]:
    """Subscription ids que siguen alguno de los equipos o el partido."""
    wanted = {str(t).lower() for t in (team_slugs or []) if t}
    gid = str(game_id or "").lower()
    out = []
    for sid, info in _load().items():
        if wanted & set(info.get("teams", [])) or (gid and gid in info.get("games", [])):
            out.append(sid)
    return out


def stats() -> dict:
    data = _load()
    teams: dict[str, int] = {}
    for info in data.values():
        for t in info.get("teams", []):
            teams[t] = teams.get(t, 0) + 1
    top = sorted(teams.items(), key=lambda kv: -kv[1])[:15]
    return {"subscriptions": len(data), "with_teams": sum(1 for i in data.values() if i.get("teams")),
            "top_teams": top}


def purge_stale(days: int = 120) -> int:
    """Quita suscripciones sin actividad en `days` (el cliente re-sincroniza al abrir el sitio)."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with _lock:
        data = _load()
        stale = [k for k, v in data.items() if (v.get("updated") or "") < cutoff]
        for k in stale:
            del data[k]
        if stale:
            _save(data)
    return len(stale)
