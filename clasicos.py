"""Catálogo de rivalidades con nombre propio.

Vive aparte de server.py porque son datos, no lógica: 50+ entradas de texto y
color no tienen por qué engordar un módulo que ya pasa de 9.000 líneas.

QUÉ SIGNIFICA CADA CAMPO
------------------------
nombre      Cómo se le dice al partido. Si la rivalidad no tiene un apodo de
            uso común, va "Equipo A vs Equipo B" y ya. Inventar un apodo sería
            publicar algo falso, y el sitio se sostiene en lo contrario.
liga        Nombre visible de la liga. liga_slug: la clave en config.LEAGUES.
equipos     Los nombres con los que se compara contra lo que devuelve la API.
            Se buscan como SUBCADENA y sin acentos, así que deben ser el trozo
            estable del nombre: "Guadalajara" acierta con "CD Guadalajara" y
            con "Guadalajara"; "Chivas" no acierta con ninguno de los dos.
slugs       Las claves en config.POPULAR_TEAMS, para enlazar a /equipo/.
            El orden de `slugs` corresponde al de `equipos`.
tagline     Una línea para las tarjetas.
contexto    Un párrafo que debe ser cierto sin consultar nada. Nada de números
            de partidos ni de títulos: eso cambia y aquí nadie lo actualiza.
color_a/b   Colores de club para los degradados. Son decorativos. Se eligen
            oscuros a propósito: encima va texto blanco.

CUIDADO AL AÑADIR UNA ENTRADA
-----------------------------
El emparejamiento es por subcadena dentro de la misma liga. Dos entradas de la
misma liga no pueden ser ambiguas entre sí. El caso real a vigilar es el de
nombres contenidos en otros: en Serie A "Milan" está dentro de "Inter Milan",
así que la entrada del Derbi de la Madonnina solo funciona porque la otra mitad
del par ("Inter") no aparece en el nombre del AC Milan. Hay una prueba que
recorre todos los pares y avisa de colisiones; córrela al añadir.
"""

CLASICOS = {

    # ── Liga MX ───────────────────────────────────────────────────────────
    "america-vs-chivas": {
        "nombre": "Clásico Nacional", "liga": "Liga MX", "liga_slug": "liga-mx",
        "equipos": ("América", "Guadalajara"),
        "slugs": ("america", "chivas"),
        "tagline": "América vs Chivas",
        "contexto": "El partido más visto del futbol mexicano. Enfrenta a los dos clubes "
                    "con más campeonatos de Liga MX y es el único que suele transmitirse "
                    "por televisión abierta en todo el país.",
        "color_a": "#00205b", "color_b": "#c8102e",
    },
    "america-vs-cruz-azul": {
        "nombre": "Clásico Joven", "liga": "Liga MX", "liga_slug": "liga-mx",
        "equipos": ("América", "Cruz Azul"),
        "slugs": ("america", "cruz-azul"),
        "tagline": "América vs Cruz Azul",
        "contexto": "Clásico capitalino entre dos de los equipos más ganadores de México. "
                    "Se juega desde los años setenta y ha definido varias finales.",
        "color_a": "#00205b", "color_b": "#003da5",
    },
    "america-vs-pumas": {
        "nombre": "Clásico Capitalino", "liga": "Liga MX", "liga_slug": "liga-mx",
        "equipos": ("América", "Pumas"),
        "slugs": ("america", "pumas"),
        "tagline": "América vs Pumas",
        "contexto": "Los dos clubes de la Ciudad de México con más historia en la máxima "
                    "categoría. El contraste entre un club de empresa y uno universitario "
                    "es parte de lo que le da sentido al duelo.",
        "color_a": "#00205b", "color_b": "#1c3c6e",
    },
    "monterrey-vs-tigres": {
        "nombre": "Clásico Regio", "liga": "Liga MX", "liga_slug": "liga-mx",
        "equipos": ("Monterrey", "Tigres"),
        "slugs": ("monterrey", "tigres"),
        "tagline": "Rayados vs Tigres",
        "contexto": "El clásico de Nuevo León. Los dos equipos comparten ciudad y se han "
                    "enfrentado en finales de Liga MX y de Concacaf.",
        "color_a": "#003da5", "color_b": "#8a6a00",
    },
    "atlas-vs-chivas": {
        "nombre": "Clásico Tapatío", "liga": "Liga MX", "liga_slug": "liga-mx",
        "equipos": ("Atlas", "Guadalajara"),
        "slugs": ("atlas", "chivas"),
        "tagline": "Atlas vs Chivas",
        "contexto": "El clásico de Guadalajara. Los dos clubes son de la misma ciudad y "
                    "la rivalidad es una de las más antiguas del futbol mexicano.",
        "color_a": "#1a1a1a", "color_b": "#c8102e",
    },
    "leon-vs-pachuca": {
        "nombre": "Clásico del Bajío", "liga": "Liga MX", "liga_slug": "liga-mx",
        "equipos": ("León", "Pachuca"),
        "slugs": ("leon", "pachuca"),
        "tagline": "León vs Pachuca",
        "contexto": "Duelo entre dos clubes que durante años compartieron dueño, lo que "
                    "le dio al partido un carácter propio dentro del calendario.",
        "color_a": "#006847", "color_b": "#1e3a8a",
    },
    "pumas-vs-cruz-azul": {
        "nombre": "Pumas vs Cruz Azul", "liga": "Liga MX", "liga_slug": "liga-mx",
        "equipos": ("Pumas", "Cruz Azul"),
        "slugs": ("pumas", "cruz-azul"),
        "tagline": "El otro duelo de la capital",
        "contexto": "Dos clubes de la Ciudad de México que se han cruzado en finales y en "
                    "liguillas. No tiene un apodo de uso general, pero sí historia.",
        "color_a": "#1c3c6e", "color_b": "#003da5",
    },

    # ── La Liga ───────────────────────────────────────────────────────────
    "real-madrid-vs-barcelona": {
        "nombre": "El Clásico", "liga": "La Liga", "liga_slug": "la-liga",
        "equipos": ("Real Madrid", "Barcelona"),
        "slugs": ("real-madrid", "barcelona"),
        "tagline": "Real Madrid vs Barcelona",
        "contexto": "El partido de clubes más visto del mundo. Enfrenta a los dos equipos "
                    "más laureados del futbol español y se juega al menos dos veces por "
                    "temporada en Liga, más los cruces de copa y de Europa.",
        "color_a": "#00529f", "color_b": "#a50044",
    },
    "real-madrid-vs-atletico": {
        "nombre": "Derbi madrileño", "liga": "La Liga", "liga_slug": "la-liga",
        "equipos": ("Real Madrid", "Atlético"),
        "slugs": ("real-madrid", "atletico-madrid"),
        "tagline": "Real Madrid vs Atlético de Madrid",
        "contexto": "El derbi de la capital española. Los dos clubes se han enfrentado en "
                    "finales de la Champions League, algo que ningún otro derbi urbano "
                    "puede decir.",
        "color_a": "#00529f", "color_b": "#a51f1f",
    },
    "barcelona-vs-espanyol": {
        "nombre": "Derbi barcelonés", "liga": "La Liga", "liga_slug": "la-liga",
        "equipos": ("Barcelona", "Espanyol"),
        "slugs": ("barcelona", "espanyol"),
        "tagline": "Barcelona vs Espanyol",
        "contexto": "El derbi de la ciudad de Barcelona, entre los dos clubes históricos "
                    "de la capital catalana.",
        "color_a": "#a50044", "color_b": "#004b9e",
    },
    "sevilla-vs-betis": {
        "nombre": "Derbi sevillano", "liga": "La Liga", "liga_slug": "la-liga",
        "equipos": ("Sevilla", "Betis"),
        "slugs": ("sevilla", "real-betis"),
        "tagline": "Sevilla vs Real Betis",
        "contexto": "El derbi de Sevilla, uno de los de mayor ambiente de España. Los dos "
                    "clubes comparten ciudad y reparten la afición casi por mitades.",
        "color_a": "#a3001e", "color_b": "#006b3c",
    },
    "athletic-vs-real-sociedad": {
        "nombre": "Derbi vasco", "liga": "La Liga", "liga_slug": "la-liga",
        "equipos": ("Athletic", "Real Sociedad"),
        "slugs": ("athletic-club", "real-sociedad"),
        "tagline": "Athletic Club vs Real Sociedad",
        "contexto": "El derbi del País Vasco, entre Bilbao y San Sebastián. El Athletic "
                    "mantiene su política de cantera, lo que le da al duelo un carácter "
                    "distinto al de otros derbis españoles.",
        "color_a": "#a3001e", "color_b": "#004b9e",
    },

    # ── Premier League ────────────────────────────────────────────────────
    "liverpool-vs-manchester-united": {
        "nombre": "Liverpool vs Manchester United", "liga": "Premier League",
        "liga_slug": "premier-league",
        "equipos": ("Liverpool", "Manchester United"),
        "slugs": ("liverpool", "manchester-united"),
        "tagline": "El duelo del noroeste de Inglaterra",
        "contexto": "Los dos clubes más laureados del futbol inglés. La rivalidad va más "
                    "allá del campo: nace de la competencia histórica entre las ciudades "
                    "de Liverpool y Mánchester.",
        "color_a": "#8f0c21", "color_b": "#1a1a1a",
    },
    "manchester-city-vs-manchester-united": {
        "nombre": "Derbi de Mánchester", "liga": "Premier League",
        "liga_slug": "premier-league",
        "equipos": ("Manchester City", "Manchester United"),
        "slugs": ("manchester-city", "manchester-united"),
        "tagline": "Manchester City vs Manchester United",
        "contexto": "El derbi de la ciudad de Mánchester. Durante décadas fue un duelo "
                    "desigual; desde la llegada de la propiedad actual al City, decide "
                    "títulos con regularidad.",
        "color_a": "#2a6b94", "color_b": "#a3251c",
    },
    "arsenal-vs-tottenham": {
        "nombre": "Derbi del norte de Londres", "liga": "Premier League",
        "liga_slug": "premier-league",
        "equipos": ("Arsenal", "Tottenham"),
        "slugs": ("arsenal", "tottenham"),
        "tagline": "Arsenal vs Tottenham",
        "contexto": "El derbi más caliente de Londres. Los dos estadios están a pocos "
                    "kilómetros y la rivalidad viene del traslado del Arsenal al norte "
                    "de la ciudad a principios del siglo XX.",
        "color_a": "#9c0511", "color_b": "#132257",
    },
    "liverpool-vs-everton": {
        "nombre": "Derbi de Merseyside", "liga": "Premier League",
        "liga_slug": "premier-league",
        "equipos": ("Liverpool", "Everton"),
        "slugs": ("liverpool", "everton"),
        "tagline": "Liverpool vs Everton",
        "contexto": "El derbi de la ciudad de Liverpool. Es el que más veces se ha jugado "
                    "en la primera división inglesa, porque ambos clubes casi nunca han "
                    "estado fuera de ella al mismo tiempo.",
        "color_a": "#8f0c21", "color_b": "#003399",
    },

    # ── Serie A ───────────────────────────────────────────────────────────
    "inter-vs-milan": {
        "nombre": "Derbi de la Madonnina", "liga": "Serie A", "liga_slug": "serie-a",
        "equipos": ("Inter", "Milan"),
        "slugs": ("inter-milan", "ac-milan"),
        "tagline": "Inter vs AC Milan",
        "contexto": "El derbi de Milán. Los dos clubes comparten estadio, San Siro, así "
                    "que el partido se juega siempre en casa de los dos.",
        "color_a": "#0b3c7a", "color_b": "#a3251c",
    },
    "juventus-vs-inter": {
        "nombre": "Derby d'Italia", "liga": "Serie A", "liga_slug": "serie-a",
        "equipos": ("Juventus", "Inter"),
        "slugs": ("juventus", "inter-milan"),
        "tagline": "Juventus vs Inter",
        "contexto": "El nombre se lo puso el periodista Gianni Brera cuando los dos eran "
                    "los únicos clubes italianos que nunca habían descendido. Siguen "
                    "siendo el duelo que más veces ha decidido el Scudetto.",
        "color_a": "#1a1a1a", "color_b": "#0b3c7a",
    },
    "roma-vs-lazio": {
        "nombre": "Derbi della Capitale", "liga": "Serie A", "liga_slug": "serie-a",
        "equipos": ("Roma", "Lazio"),
        "slugs": ("as-roma", "lazio"),
        "tagline": "AS Roma vs Lazio",
        "contexto": "El derbi de Roma. Los dos clubes comparten el Estadio Olímpico y "
                    "dividen la ciudad; es uno de los partidos con más despliegue de "
                    "grada de Europa.",
        "color_a": "#8e1f2f", "color_b": "#1b6ca8",
    },

    # ── Bundesliga ────────────────────────────────────────────────────────
    "bayern-vs-dortmund": {
        "nombre": "Der Klassiker", "liga": "Bundesliga", "liga_slug": "bundesliga",
        "equipos": ("Bayern", "Dortmund"),
        "slugs": ("bayern", "borussia-dortmund"),
        "tagline": "Bayern Múnich vs Borussia Dortmund",
        "contexto": "El partido grande del futbol alemán. No es un derbi de ciudad: lo "
                    "que lo define es que son los dos clubes que más veces han peleado "
                    "la Bundesliga en las últimas décadas.",
        "color_a": "#9c0511", "color_b": "#8a7500",
    },

    # ── Ligue 1 ───────────────────────────────────────────────────────────
    "psg-vs-marsella": {
        "nombre": "Le Classique", "liga": "Ligue 1", "liga_slug": "ligue-1",
        "equipos": ("Paris", "Marseille"),
        "slugs": ("psg", "marseille"),
        "tagline": "PSG vs Olympique de Marsella",
        "contexto": "El partido más importante de Francia. Enfrenta a la capital con "
                    "Marsella, y es el único club francés que ha ganado la Champions "
                    "contra el que más dinero ha invertido en intentarlo.",
        "color_a": "#004170", "color_b": "#1a7fa3",
    },

    # ── Liga Portugal ─────────────────────────────────────────────────────
    "benfica-vs-porto": {
        "nombre": "O Clássico", "liga": "Liga Portugal", "liga_slug": "liga-portugal",
        "equipos": ("Benfica", "Porto"),
        "slugs": ("benfica", "porto"),
        "tagline": "Benfica vs FC Porto",
        "contexto": "El duelo entre Lisboa y Oporto, y entre los dos clubes que más veces "
                    "han ganado la liga portuguesa.",
        "color_a": "#a3151e", "color_b": "#00428c",
    },
    "benfica-vs-sporting": {
        "nombre": "Derbi de Lisboa", "liga": "Liga Portugal", "liga_slug": "liga-portugal",
        "equipos": ("Benfica", "Sporting"),
        "slugs": ("benfica", "sporting-cp"),
        "tagline": "Benfica vs Sporting CP",
        "contexto": "El derbi de la capital portuguesa, entre los dos grandes de Lisboa.",
        "color_a": "#a3151e", "color_b": "#00653e",
    },

    # ── Eredivisie ────────────────────────────────────────────────────────
    "ajax-vs-feyenoord": {
        "nombre": "De Klassieker", "liga": "Eredivisie", "liga_slug": "eredivisie",
        "equipos": ("Ajax", "Feyenoord"),
        "slugs": ("ajax", "feyenoord"),
        "tagline": "Ajax vs Feyenoord",
        "contexto": "Ámsterdam contra Róterdam: el partido grande de Países Bajos, con "
                    "una rivalidad que durante años obligó a jugarlo sin afición visitante.",
        "color_a": "#a3151e", "color_b": "#1a1a1a",
    },

    # ── Liga Argentina ────────────────────────────────────────────────────
    "boca-vs-river": {
        "nombre": "Superclásico", "liga": "Liga Argentina", "liga_slug": "liga-argentina",
        "equipos": ("Boca", "River"),
        "slugs": ("boca-juniors", "river-plate"),
        "tagline": "Boca Juniors vs River Plate",
        "contexto": "El partido más importante del futbol argentino y uno de los más "
                    "seguidos del mundo. Los dos clubes nacieron en el mismo barrio, La "
                    "Boca, antes de que River se mudara al norte de Buenos Aires.",
        "color_a": "#0d2b6b", "color_b": "#a3151e",
    },
    "racing-vs-independiente": {
        "nombre": "Clásico de Avellaneda", "liga": "Liga Argentina",
        "liga_slug": "liga-argentina",
        "equipos": ("Racing", "Independiente"),
        "slugs": ("racing-club", "independiente-arg"),
        "tagline": "Racing Club vs Independiente",
        "contexto": "Los dos estadios están a unas cuadras de distancia, en Avellaneda. "
                    "Es de los pocos clásicos del mundo donde se puede caminar de una "
                    "cancha a la otra.",
        "color_a": "#2a6b94", "color_b": "#a3151e",
    },
    "newells-vs-central": {
        "nombre": "Clásico rosarino", "liga": "Liga Argentina",
        "liga_slug": "liga-argentina",
        "equipos": ("Newell", "Rosario Central"),
        "slugs": ("newells", "rosario-central"),
        "tagline": "Newell's Old Boys vs Rosario Central",
        "contexto": "El clásico de Rosario. Divide a la ciudad en dos y es uno de los de "
                    "mayor temperatura del interior argentino.",
        "color_a": "#a3151e", "color_b": "#0b3c7a",
    },
    "san-lorenzo-vs-huracan": {
        "nombre": "Clásico de Boedo", "liga": "Liga Argentina",
        "liga_slug": "liga-argentina",
        "equipos": ("San Lorenzo", "Huracán"),
        "slugs": ("san-lorenzo", "huracan"),
        "tagline": "San Lorenzo vs Huracán",
        "contexto": "El clásico del sur de Buenos Aires, entre los barrios de Boedo y "
                    "Parque Patricios.",
        "color_a": "#0b3c7a", "color_b": "#a3151e",
    },
    "estudiantes-vs-gimnasia": {
        "nombre": "Clásico platense", "liga": "Liga Argentina",
        "liga_slug": "liga-argentina",
        "equipos": ("Estudiantes", "Gimnasia"),
        "slugs": ("estudiantes", "gimnasia-lp"),
        "tagline": "Estudiantes vs Gimnasia",
        "contexto": "El clásico de La Plata. Los dos clubes son de la misma ciudad y el "
                    "partido paraliza la capital bonaerense.",
        "color_a": "#a3151e", "color_b": "#0b5c3c",
    },

    # ── Liga BetPlay (Colombia) ───────────────────────────────────────────
    "nacional-vs-medellin": {
        "nombre": "Clásico paisa", "liga": "Liga BetPlay", "liga_slug": "liga-colombia",
        "equipos": ("Nacional", "Medellín"),
        "slugs": ("atletico-nacional", "independiente-medellin"),
        "tagline": "Atlético Nacional vs Independiente Medellín",
        "contexto": "El clásico de Medellín. Los dos equipos comparten el estadio "
                    "Atanasio Girardot, así que siempre juegan en casa.",
        "color_a": "#0b5c3c", "color_b": "#a3151e",
    },
    "millonarios-vs-santa-fe": {
        "nombre": "Clásico bogotano", "liga": "Liga BetPlay", "liga_slug": "liga-colombia",
        "equipos": ("Millonarios", "Santa Fe"),
        "slugs": ("millonarios", "independiente-santa-fe"),
        "tagline": "Millonarios vs Santa Fe",
        "contexto": "El clásico de Bogotá, entre los dos equipos tradicionales de la "
                    "capital colombiana. También comparten estadio, El Campín.",
        "color_a": "#0b3c7a", "color_b": "#a3151e",
    },
    "america-cali-vs-deportivo-cali": {
        "nombre": "Clásico vallecaucano", "liga": "Liga BetPlay",
        "liga_slug": "liga-colombia",
        "equipos": ("América", "Deportivo Cali"),
        "slugs": ("america-cali", "deportivo-cali"),
        "tagline": "América de Cali vs Deportivo Cali",
        "contexto": "El clásico del Valle del Cauca, entre los dos grandes de la ciudad "
                    "de Cali.",
        "color_a": "#a3151e", "color_b": "#0b5c3c",
    },

    # ── Primera Chile ─────────────────────────────────────────────────────
    "colo-colo-vs-u-de-chile": {
        "nombre": "Superclásico chileno", "liga": "Primera Chile",
        "liga_slug": "liga-chile",
        "equipos": ("Colo", "Universidad de Chile"),
        "slugs": ("colo-colo", "universidad-chile"),
        "tagline": "Colo-Colo vs Universidad de Chile",
        "contexto": "El partido más importante del futbol chileno, entre los dos clubes "
                    "con más seguidores del país.",
        "color_a": "#1a1a1a", "color_b": "#0b3c7a",
    },
    "catolica-vs-u-de-chile": {
        "nombre": "Clásico universitario", "liga": "Primera Chile",
        "liga_slug": "liga-chile",
        "equipos": ("Universidad Católica", "Universidad de Chile"),
        "slugs": ("universidad-catolica", "universidad-chile"),
        "tagline": "Universidad Católica vs Universidad de Chile",
        "contexto": "El duelo entre los dos clubes nacidos de universidades de Santiago.",
        "color_a": "#8a1420", "color_b": "#0b3c7a",
    },

    # ── Liga 1 Perú ───────────────────────────────────────────────────────
    "alianza-vs-universitario": {
        "nombre": "Clásico peruano", "liga": "Liga 1 Perú", "liga_slug": "liga-peru",
        "equipos": ("Alianza", "Universitario"),
        "slugs": ("alianza-lima", "universitario"),
        "tagline": "Alianza Lima vs Universitario",
        "contexto": "El partido más importante del Perú, entre los dos clubes con más "
                    "títulos nacionales y más hinchada del país.",
        "color_a": "#1a1a1a", "color_b": "#8a1420",
    },

    # ── LigaPro Ecuador ───────────────────────────────────────────────────
    "barcelona-sc-vs-emelec": {
        "nombre": "Clásico del Astillero", "liga": "LigaPro Ecuador",
        "liga_slug": "liga-ecuador",
        "equipos": ("Barcelona", "Emelec"),
        "slugs": ("barcelona-sc", "emelec"),
        "tagline": "Barcelona SC vs Emelec",
        "contexto": "El clásico de Guayaquil. El nombre viene del barrio del Astillero, "
                    "donde nacieron los dos clubes.",
        "color_a": "#8a6a00", "color_b": "#0b3c7a",
    },

    # ── MLS ───────────────────────────────────────────────────────────────
    "lafc-vs-galaxy": {
        "nombre": "El Tráfico", "liga": "MLS", "liga_slug": "mls",
        # OJO: es la única entrada cuyo nombre en la API no está comprobado.
        # Nosotros llamamos al club "LAFC"; ESPN puede mandarlo como "Los
        # Angeles FC", y entonces ni el aviso de clásico ni el historial
        # saldrían (la página sigue funcionando, solo queda sin datos).
        # Hay que verlo contra un partido real de MLS y ajustar si hace falta.
        "equipos": ("LAFC", "Galaxy"),
        "slugs": ("lafc", "la-galaxy"),
        "tagline": "LAFC vs LA Galaxy",
        "contexto": "El derbi de Los Ángeles. El nombre es un juego de palabras con el "
                    "tráfico de la ciudad y con el Clásico, y lo eligieron las aficiones.",
        "color_a": "#1a1a1a", "color_b": "#0b3c7a",
    },

    # ── MLB ───────────────────────────────────────────────────────────────
    "yankees-vs-red-sox": {
        "nombre": "Yankees vs Red Sox", "liga": "MLB", "liga_slug": "mlb",
        "equipos": ("Yankees", "Red Sox"),
        "slugs": ("yankees", "red-sox"),
        "tagline": "La rivalidad más antigua de las Grandes Ligas",
        "contexto": "Rivalidad de división en la Liga Americana del Este. Se enfrentan "
                    "varias veces cada temporada regular y han definido series de "
                    "postemporada memorables.",
        "color_a": "#132448", "color_b": "#8a1420",
    },
    "dodgers-vs-giants": {
        "nombre": "Dodgers vs Giants", "liga": "MLB", "liga_slug": "mlb",
        "equipos": ("Dodgers", "Giants"),
        "slugs": ("dodgers", "giants"),
        "tagline": "El clásico de la Costa Oeste",
        "contexto": "Rivalidad que nació en Nueva York y se mudó completa a California. "
                    "Ambos están en la División Oeste de la Liga Nacional.",
        "color_a": "#005a9c", "color_b": "#a8431a",
    },
    "cubs-vs-cardinals": {
        "nombre": "Cubs vs Cardinals", "liga": "MLB", "liga_slug": "mlb",
        "equipos": ("Cubs", "Cardinals"),
        "slugs": ("cubs", "cardinals"),
        "tagline": "La rivalidad del Centro",
        "contexto": "Chicago contra San Luis, en la División Central de la Liga Nacional. "
                    "Las dos ciudades están conectadas por la Ruta 66 y la afición "
                    "visitante suele llenar media grada.",
        "color_a": "#0e3386", "color_b": "#8a1420",
    },
    "yankees-vs-mets": {
        "nombre": "Serie del Metro", "liga": "MLB", "liga_slug": "mlb",
        "equipos": ("Yankees", "Mets"),
        "slugs": ("yankees", "mets"),
        "tagline": "Yankees vs Mets",
        "contexto": "El duelo entre los dos equipos de Nueva York. Se llama así porque se "
                    "puede ir de un estadio al otro en metro.",
        "color_a": "#132448", "color_b": "#a8431a",
    },

    # ── NFL ───────────────────────────────────────────────────────────────
    "cowboys-vs-eagles": {
        "nombre": "Cowboys vs Eagles", "liga": "NFL", "liga_slug": "nfl",
        "equipos": ("Cowboys", "Eagles"),
        "slugs": ("cowboys", "eagles"),
        "tagline": "El clásico de la NFC Este",
        "contexto": "Rivalidad de división: se enfrentan dos veces cada temporada regular "
                    "y el resultado suele decidir quién gana la NFC Este.",
        "color_a": "#041e42", "color_b": "#004c54",
    },
    "packers-vs-bears": {
        "nombre": "Packers vs Bears", "liga": "NFL", "liga_slug": "nfl",
        "equipos": ("Packers", "Bears"),
        "slugs": ("packers", "bears"),
        "tagline": "La rivalidad más antigua de la NFL",
        "contexto": "Green Bay contra Chicago: el duelo que más veces se ha jugado en la "
                    "historia de la liga. Los dos equipos están en la NFC Norte.",
        "color_a": "#203731", "color_b": "#0b162a",
    },
    "steelers-vs-ravens": {
        "nombre": "Steelers vs Ravens", "liga": "NFL", "liga_slug": "nfl",
        "equipos": ("Steelers", "Ravens"),
        "slugs": ("steelers", "ravens"),
        "tagline": "El duelo más físico de la AFC Norte",
        "contexto": "Pittsburgh contra Baltimore. Es la rivalidad que se asocia con el "
                    "juego defensivo y los partidos de marcador bajo.",
        "color_a": "#8a6a00", "color_b": "#241773",
    },
    "chiefs-vs-raiders": {
        "nombre": "Chiefs vs Raiders", "liga": "NFL", "liga_slug": "nfl",
        "equipos": ("Chiefs", "Raiders"),
        "slugs": ("chiefs", "raiders"),
        "tagline": "El clásico de la AFC Oeste",
        "contexto": "Una rivalidad que viene de la antigua AFL, anterior a la fusión con "
                    "la NFL, y que sobrevivió a las dos mudanzas de los Raiders.",
        "color_a": "#a3151e", "color_b": "#1a1a1a",
    },
    "49ers-vs-seahawks": {
        "nombre": "49ers vs Seahawks", "liga": "NFL", "liga_slug": "nfl",
        "equipos": ("49ers", "Seahawks"),
        "slugs": ("49ers", "seahawks"),
        "tagline": "El duelo del Pacífico",
        "contexto": "San Francisco contra Seattle, en la NFC Oeste. La rivalidad se "
                    "encendió en los años en que ambos peleaban por llegar al Super Bowl.",
        "color_a": "#8a1420", "color_b": "#002244",
    },

    # ── NBA ───────────────────────────────────────────────────────────────
    "lakers-vs-celtics": {
        "nombre": "Lakers vs Celtics", "liga": "NBA", "liga_slug": "nba",
        "equipos": ("Lakers", "Celtics"),
        "slugs": ("lakers", "celtics"),
        "tagline": "La rivalidad con más finales de la NBA",
        "contexto": "Los dos equipos con más campeonatos de la NBA. Se han enfrentado en "
                    "múltiples Finales desde los años sesenta.",
        "color_a": "#552583", "color_b": "#00703c",
    },
    "lakers-vs-clippers": {
        "nombre": "Batalla de Los Ángeles", "liga": "NBA", "liga_slug": "nba",
        "equipos": ("Lakers", "Clippers"),
        "slugs": ("lakers", "clippers"),
        "tagline": "Lakers vs Clippers",
        "contexto": "Los dos equipos de Los Ángeles. Durante años compartieron el mismo "
                    "pabellón, lo que convertía el duelo en un partido sin visitante real.",
        "color_a": "#552583", "color_b": "#8a1420",
    },
    "celtics-vs-76ers": {
        "nombre": "Celtics vs 76ers", "liga": "NBA", "liga_slug": "nba",
        "equipos": ("Celtics", "76ers"),
        "slugs": ("celtics", "76ers"),
        "tagline": "El clásico del Atlántico",
        "contexto": "Boston contra Filadelfia, en la División Atlántico. Es una de las "
                    "rivalidades más antiguas de la liga.",
        "color_a": "#00703c", "color_b": "#0b4f8a",
    },

    # ── Béisbol de invierno ───────────────────────────────────────────────
    "magallanes-vs-caracas": {
        "nombre": "El Clásico venezolano", "liga": "LVBP", "liga_slug": "lvbp",
        "equipos": ("Magallanes", "Caracas"),
        "slugs": ("navegantes-del-magallanes", "leones-del-caracas"),
        "tagline": "Navegantes del Magallanes vs Leones del Caracas",
        "contexto": "El partido más seguido del béisbol venezolano. Divide al país en dos "
                    "y se juega varias veces durante la temporada de invierno.",
        "color_a": "#0b3c7a", "color_b": "#8a1420",
    },
    "licey-vs-escogido": {
        "nombre": "Clásico dominicano", "liga": "LIDOM", "liga_slug": "lidom",
        "equipos": ("Licey", "Escogido"),
        "slugs": ("tigres-del-licey", "leones-del-escogido"),
        "tagline": "Tigres del Licey vs Leones del Escogido",
        "contexto": "La rivalidad central del béisbol dominicano. Los dos equipos son de "
                    "Santo Domingo y comparten el Estadio Quisqueya.",
        "color_a": "#0b3c7a", "color_b": "#8a1420",
    },
    "tomateros-vs-naranjeros": {
        "nombre": "Tomateros vs Naranjeros", "liga": "Liga Mexicana del Pacífico",
        "liga_slug": "lmp",
        "equipos": ("Tomateros", "Naranjeros"),
        "slugs": ("tomateros-culiacan", "naranjeros-hermosillo"),
        "tagline": "Culiacán vs Hermosillo",
        "contexto": "El duelo entre los dos equipos más ganadores de la Liga Mexicana del "
                    "Pacífico, y entre Sinaloa y Sonora.",
        "color_a": "#8a1420", "color_b": "#a8431a",
    },
}
