"""
Patch: Improve TV channel data
1. Expand CHANNEL_ALIASES with ESPN truncated names + more channels
2. Add normalize_channel() function in sports_api.py
3. Update game.html to show channels by country (MX / USA)
4. Update index.html to show channels by country
"""
import re

# ── 1. Expand CHANNEL_ALIASES in config.py ──────────────────────

config_path = "config.py"
with open(config_path, "r") as f:
    config = f.read()

OLD_ALIASES = '''CHANNEL_ALIASES = {
    # Mexico
    "TUDN": {"name": "TUDN", "country": "MX", "type": "cable"},
    "Canal 5": {"name": "Canal 5", "country": "MX", "type": "broadcast"},
    "Azteca 7": {"name": "Azteca 7", "country": "MX", "type": "broadcast"},
    "Fox Sports MX": {"name": "Fox Sports Mexico", "country": "MX", "type": "cable"},
    "ViX": {"name": "ViX Premium", "country": "MX", "type": "streaming"},
    "ESPN MX": {"name": "ESPN Mexico", "country": "MX", "type": "cable"},
    "Claro Sports": {"name": "Claro Sports", "country": "MX", "type": "cable"},
    # USA
    "ESPN": {"name": "ESPN", "country": "US", "type": "cable"},
    "ESPN2": {"name": "ESPN2", "country": "US", "type": "cable"},
    "ESPN+": {"name": "ESPN+", "country": "US", "type": "streaming"},
    "FOX": {"name": "FOX", "country": "US", "type": "broadcast"},
    "FS1": {"name": "Fox Sports 1", "country": "US", "type": "cable"},
    "NBC": {"name": "NBC", "country": "US", "type": "broadcast"},
    "Peacock": {"name": "Peacock", "country": "US", "type": "streaming"},
    "CBS": {"name": "CBS", "country": "US", "type": "broadcast"},
    "Paramount+": {"name": "Paramount+", "country": "US", "type": "streaming"},
    "TNT": {"name": "TNT", "country": "US", "type": "cable"},
    "ABC": {"name": "ABC", "country": "US", "type": "broadcast"},
    "Amazon Prime": {"name": "Amazon Prime Video", "country": "US", "type": "streaming"},
    "Apple TV+": {"name": "Apple TV+", "country": "US", "type": "streaming"},
    "Univision": {"name": "Univision", "country": "US", "type": "broadcast"},
    "TUDN USA": {"name": "TUDN USA", "country": "US", "type": "cable"},
}'''

NEW_ALIASES = '''CHANNEL_ALIASES = {
    # ── Mexico ──
    "TUDN": {"name": "TUDN", "country": "MX", "type": "cable"},
    "Canal 5": {"name": "Canal 5", "country": "MX", "type": "broadcast"},
    "Azteca 7": {"name": "Azteca 7", "country": "MX", "type": "broadcast"},
    "Azteca Deportes": {"name": "Azteca 7", "country": "MX", "type": "broadcast"},
    "TV Azteca": {"name": "TV Azteca", "country": "MX", "type": "broadcast"},
    "Fox Sports MX": {"name": "Fox Sports MX", "country": "MX", "type": "cable"},
    "FOX Sports MX": {"name": "Fox Sports MX", "country": "MX", "type": "cable"},
    "ViX": {"name": "ViX", "country": "MX", "type": "streaming"},
    "ViX Premium": {"name": "ViX", "country": "MX", "type": "streaming"},
    "VIX": {"name": "ViX", "country": "MX", "type": "streaming"},
    "ESPN MX": {"name": "ESPN MX", "country": "MX", "type": "cable"},
    "Claro Sports": {"name": "Claro Sports", "country": "MX", "type": "cable"},
    "Claro Video": {"name": "Claro Video", "country": "MX", "type": "streaming"},
    "Imagen TV": {"name": "Imagen TV", "country": "MX", "type": "broadcast"},
    "Las Estrellas": {"name": "Las Estrellas", "country": "MX", "type": "broadcast"},
    "Caliente TV": {"name": "Caliente TV", "country": "MX", "type": "streaming"},
    "Afizzionados": {"name": "Afizzionados", "country": "MX", "type": "cable"},
    "ESPN Deportes": {"name": "ESPN Deportes", "country": "MX", "type": "cable"},
    "Blue To Go": {"name": "Blue To Go", "country": "MX", "type": "streaming"},
    # ── USA ──
    "ESPN": {"name": "ESPN", "country": "US", "type": "cable"},
    "ESPN2": {"name": "ESPN2", "country": "US", "type": "cable"},
    "ESPN+": {"name": "ESPN+", "country": "US", "type": "streaming"},
    "ESPNU": {"name": "ESPNU", "country": "US", "type": "cable"},
    "ESPNews": {"name": "ESPNews", "country": "US", "type": "cable"},
    "FOX": {"name": "FOX", "country": "US", "type": "broadcast"},
    "FS1": {"name": "FS1", "country": "US", "type": "cable"},
    "FS2": {"name": "FS2", "country": "US", "type": "cable"},
    "Fox Sports 1": {"name": "FS1", "country": "US", "type": "cable"},
    "Fox Sports 2": {"name": "FS2", "country": "US", "type": "cable"},
    "FOX One": {"name": "FOX", "country": "US", "type": "broadcast"},
    "NBC": {"name": "NBC", "country": "US", "type": "broadcast"},
    "NBCSN": {"name": "NBC Sports", "country": "US", "type": "cable"},
    "USA Network": {"name": "USA Network", "country": "US", "type": "cable"},
    "USA": {"name": "USA Network", "country": "US", "type": "cable"},
    "Peacock": {"name": "Peacock", "country": "US", "type": "streaming"},
    "CBS": {"name": "CBS", "country": "US", "type": "broadcast"},
    "Paramount+": {"name": "Paramount+", "country": "US", "type": "streaming"},
    "CBS Sports": {"name": "CBS Sports", "country": "US", "type": "cable"},
    "CBSSN": {"name": "CBS Sports", "country": "US", "type": "cable"},
    "TNT": {"name": "TNT", "country": "US", "type": "cable"},
    "TBS": {"name": "TBS", "country": "US", "type": "cable"},
    "truTV": {"name": "truTV", "country": "US", "type": "cable"},
    "ABC": {"name": "ABC", "country": "US", "type": "broadcast"},
    "Amazon Prime": {"name": "Prime Video", "country": "US", "type": "streaming"},
    "Prime Video": {"name": "Prime Video", "country": "US", "type": "streaming"},
    "Apple TV+": {"name": "Apple TV+", "country": "US", "type": "streaming"},
    "Apple TV": {"name": "Apple TV+", "country": "US", "type": "streaming"},
    "MLS Season Pass": {"name": "MLS Season Pass", "country": "US", "type": "streaming"},
    "MLB.TV": {"name": "MLB.TV", "country": "US", "type": "streaming"},
    "NFL+": {"name": "NFL+", "country": "US", "type": "streaming"},
    "NFL Network": {"name": "NFL Network", "country": "US", "type": "cable"},
    "NFLN": {"name": "NFL Network", "country": "US", "type": "cable"},
    "NBA TV": {"name": "NBA TV", "country": "US", "type": "cable"},
    "MLB Network": {"name": "MLB Network", "country": "US", "type": "cable"},
    "MLBN": {"name": "MLB Network", "country": "US", "type": "cable"},
    "MAX": {"name": "Max", "country": "US", "type": "streaming"},
    "Max": {"name": "Max", "country": "US", "type": "streaming"},
    "HBO Max": {"name": "Max", "country": "US", "type": "streaming"},
    # ── Both / Spanish US (shown under MX for our audience) ──
    "Univision": {"name": "Univision", "country": "MX", "type": "broadcast"},
    "UniMas": {"name": "UniMas", "country": "MX", "type": "broadcast"},
    "UniMás": {"name": "UniMas", "country": "MX", "type": "broadcast"},
    "TUDN USA": {"name": "TUDN", "country": "MX", "type": "cable"},
    "Telemundo": {"name": "Telemundo", "country": "MX", "type": "broadcast"},
    "FOX Deportes": {"name": "FOX Deportes", "country": "MX", "type": "cable"},
    "FOXD": {"name": "FOX Deportes", "country": "MX", "type": "cable"},
}

# ESPN shortName often truncates channel names. This maps truncated → canonical.
ESPN_CHANNEL_NORMALIZE = {
    "Tele": "Telemundo",
    "Uni": "Univision",
    "UniMas": "UniMas",
    "UniMás": "UniMas",
    "FOXD": "FOX Deportes",
    "Fox Dep": "FOX Deportes",
    "FS1": "FS1",
    "FS2": "FS2",
    "FOX One": "FOX",
    "NBCSN": "NBC Sports",
    "CBSSN": "CBS Sports",
    "NFLN": "NFL Network",
    "MLBN": "MLB Network",
    "Paramount": "Paramount+",
    "Amazon": "Prime Video",
    "Apple": "Apple TV+",
    "VIX": "ViX",
    "TUDNxtra": "TUDN",
}'''

assert OLD_ALIASES in config, "Could not find CHANNEL_ALIASES block in config.py"
config = config.replace(OLD_ALIASES, NEW_ALIASES)
with open(config_path, "w") as f:
    f.write(config)
print("[OK] config.py — expanded CHANNEL_ALIASES + added ESPN_CHANNEL_NORMALIZE")


# ── 2. Add normalize + country grouping in sports_api.py ──────────

api_path = "sports_api.py"
with open(api_path, "r") as f:
    api = f.read()

# 2a. Update import to include ESPN_CHANNEL_NORMALIZE
old_import = "from config import (\n    ESPN_BASE, SPORTSDB_BASE, SPORTSDB_KEY,\n    LEAGUES, ALL_LEAGUES, CHANNEL_ALIASES, TZ_MX, TEAM_ALIASES\n)"
new_import = "from config import (\n    ESPN_BASE, SPORTSDB_BASE, SPORTSDB_KEY,\n    LEAGUES, ALL_LEAGUES, CHANNEL_ALIASES, ESPN_CHANNEL_NORMALIZE, TZ_MX, TEAM_ALIASES\n)"
assert old_import in api, "Could not find import block in sports_api.py"
api = api.replace(old_import, new_import)

# 2b. Add normalize function after imports
normalize_func = '''

def _normalize_channel(raw: str) -> str:
    """Normalize ESPN's truncated channel names to canonical form."""
    raw = raw.strip()
    # Direct lookup in normalize map
    if raw in ESPN_CHANNEL_NORMALIZE:
        return ESPN_CHANNEL_NORMALIZE[raw]
    # Check if raw is a prefix of a known channel
    for truncated, canonical in ESPN_CHANNEL_NORMALIZE.items():
        if raw.lower() == truncated.lower():
            return canonical
    return raw

'''

# Insert after the logger line
old_logger = 'logger = logging.getLogger("dondever.sports")'
api = api.replace(old_logger, old_logger + normalize_func)

# 2c. Update the geoBroadcasts parsing to normalize channel names
old_parse = '''        # 1) Get ESPN broadcast info
        espn_broadcasts = []
        for geo_broadcast in comp.get("geoBroadcasts", []):
            market = geo_broadcast.get("market", {}).get("type", "")
            media = geo_broadcast.get("media", {})
            channel = media.get("shortName", "")
            if channel:
                espn_broadcasts.append({
                    "channel": channel,
                    "market": market,
                    "info": CHANNEL_ALIASES.get(channel, {}),
                })'''

new_parse = '''        # 1) Get ESPN broadcast info (normalize truncated names)
        espn_broadcasts = []
        seen_channels = set()
        for geo_broadcast in comp.get("geoBroadcasts", []):
            market = geo_broadcast.get("market", {}).get("type", "")
            media = geo_broadcast.get("media", {})
            raw_channel = media.get("shortName", "")
            if not raw_channel:
                continue
            channel = _normalize_channel(raw_channel)
            # Deduplicate (ESPN sometimes lists same channel twice)
            if channel.lower() in seen_channels:
                continue
            seen_channels.add(channel.lower())
            info = CHANNEL_ALIASES.get(channel, {})
            espn_broadcasts.append({
                "channel": info.get("name", channel),
                "market": market,
                "info": info,
            })'''

assert old_parse in api, "Could not find geoBroadcasts parsing block in sports_api.py"
api = api.replace(old_parse, new_parse)

with open(api_path, "w") as f:
    f.write(api)
print("[OK] sports_api.py — added normalize + dedup for channels")


# ── 3. Update game.html to show channels by country ──────────────

game_path = "templates/game.html"
with open(game_path, "r") as f:
    game = f.read()

old_channels = '''        <div class="channels-section">
            <h3>Donde verlo</h3>
            {% if game.broadcasts %}
                {% for b in game.broadcasts %}
                <span class="channel-pill {{ b.info.get('type', 'unknown') if b.info else 'unknown' }}">{{ b.channel }}</span>
                {% endfor %}
            {% else %}
                <span class="channel-pill unknown">Canales por confirmar</span>
            {% endif %}
        </div>'''

new_channels = '''        <div class="channels-section">
            <h3>DONDE VERLO</h3>
            {% if game.broadcasts %}
                {% set mx_channels = [] %}
                {% set us_channels = [] %}
                {% set other_channels = [] %}
                {% for b in game.broadcasts %}
                    {% if b.info and b.info.get('country') == 'MX' %}
                        {% if mx_channels.append(b) %}{% endif %}
                    {% elif b.info and b.info.get('country') == 'US' %}
                        {% if us_channels.append(b) %}{% endif %}
                    {% else %}
                        {% if other_channels.append(b) %}{% endif %}
                    {% endif %}
                {% endfor %}
                {% if mx_channels %}
                <div class="channel-group">
                    <span class="country-label">MX</span>
                    {% for b in mx_channels %}
                    <span class="channel-pill {{ b.info.get('type', 'unknown') }}">{{ b.channel }}</span>
                    {% endfor %}
                </div>
                {% endif %}
                {% if us_channels %}
                <div class="channel-group">
                    <span class="country-label">USA</span>
                    {% for b in us_channels %}
                    <span class="channel-pill {{ b.info.get('type', 'unknown') }}">{{ b.channel }}</span>
                    {% endfor %}
                </div>
                {% endif %}
                {% if other_channels %}
                <div class="channel-group">
                    {% for b in other_channels %}
                    <span class="channel-pill unknown">{{ b.channel }}</span>
                    {% endfor %}
                </div>
                {% endif %}
                {% if not mx_channels and not us_channels and not other_channels %}
                <span class="channel-pill unknown">Canales por confirmar</span>
                {% endif %}
            {% else %}
                <span class="channel-pill unknown">Canales por confirmar</span>
            {% endif %}
        </div>'''

assert old_channels in game, "Could not find channels section in game.html"
game = game.replace(old_channels, new_channels)

# Add CSS for country labels and channel groups
old_style_end = "</style>"
# Find the last </style> before body content
channel_css = """
    .channel-group { display:flex; flex-wrap:wrap; align-items:center; gap:0.4rem; margin-bottom:0.5rem; }
    .country-label { display:inline-flex; align-items:center; font-size:0.7rem; font-weight:700; color:#6b7280; background:#f3f4f6; border-radius:4px; padding:0.15rem 0.5rem; letter-spacing:0.05em; min-width:2.2rem; justify-content:center; }
"""

# Insert CSS before the last </style>
# Find the position to insert
style_positions = [m.start() for m in re.finditer(r'</style>', game)]
if style_positions:
    last_style = style_positions[-1]
    game = game[:last_style] + channel_css + game[last_style:]

with open(game_path, "w") as f:
    f.write(game)
print("[OK] game.html — channels now grouped by country (MX / USA)")


# ── 4. Update index.html homepage cards ──────────────────────────

index_path = "templates/index.html"
with open(index_path, "r") as f:
    index = f.read()

# index.html uses channel-tag (not channel-pill) and has Amazon affiliate logic
old_index_block = '''            <div class="game-channels">
                {% if game.broadcasts %}
                    {% for b in game.broadcasts %}
                    {% if 'Amazon' in b.channel %}
                    <a href="/go/amazon?s=channel" target="_blank" rel="noopener sponsored" data-affiliate="amazon" class="channel-tag streaming" style="text-decoration:none;font-weight:700;background:#00a8e1;color:white;" onclick="event.stopPropagation();">
                        {{ b.channel }} - 30 dias gratis
                    </a>
                    {% else %}
                    <span class="channel-tag {{ b.info.get('type', 'unknown') if b.info else 'unknown' }}">
                        {{ b.channel }}
                    </span>
                    {% endif %}
                    {% endfor %}
                {% else %}
                    <span class="channel-tag unknown">Por confirmar</span>
                {% endif %}
            </div>'''

new_index_block = '''            <div class="game-channels">
                {% if game.broadcasts %}
                    {% for b in game.broadcasts %}
                    {% if 'Amazon' in b.channel or 'Prime' in b.channel %}
                    <a href="/go/amazon?s=channel" target="_blank" rel="noopener sponsored" data-affiliate="amazon" class="channel-tag streaming" style="text-decoration:none;font-weight:700;background:#00a8e1;color:white;" onclick="event.stopPropagation();">
                        {{ b.channel }} - 30 dias gratis
                    </a>
                    {% else %}
                    <span class="channel-tag {{ b.info.get('type', 'unknown') if b.info else 'unknown' }}">
                        {% if b.info and b.info.get('country') %}<span style="font-size:0.5rem;opacity:0.6;">{{ b.info.country }}</span> {% endif %}{{ b.channel }}
                    </span>
                    {% endif %}
                    {% endfor %}
                {% else %}
                    <span class="channel-tag unknown">Por confirmar</span>
                {% endif %}
            </div>'''

if old_index_block in index:
    index = index.replace(old_index_block, new_index_block)
    with open(index_path, "w") as f:
        f.write(index)
    print("[OK] index.html — channel tags now show country prefix + Prime Video affiliate fix")
else:
    print("[SKIP] index.html — could not find channel block (may need manual update)")
    # Debug: show what's around game-channels
    idx = index.find("game-channels")
    if idx >= 0:
        print(f"  Found 'game-channels' at pos {idx}, nearby:")
        print(repr(index[idx:idx+300]))

print("\n=== PATCH COMPLETE ===")
print("Changes:")
print("  - config.py: 70+ channel aliases + ESPN normalization map")
print("  - sports_api.py: normalize_channel() + dedup")
print("  - game.html: channels grouped by MX / USA")
print("  - index.html: country prefix on pills")
print("\nDeploy: git add -A && git commit -m 'feat: improve TV channels — normalize names, group by country' && git push")
