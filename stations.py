"""Short station names -> (planner code, open-data label).

Codes and labels come from the `origine_iata` / `origine` values of the open-data
`tgvmax` dataset, which use the same codes as the planner.
A planner code of None means several stations (paris, lyon, lille): the planner
only answers per station, so the real pairs are looked up in the open data.
You can also skip this table: `/add FRPLY FRLPD 15/11` uses raw planner codes,
and any unknown name is passed through as-is.
"""

STATIONS: dict[str, tuple[str | None, str]] = {
    "paris": (None, "PARIS (intramuros)"),
    "paris-lyon": ("FRPLY", "PARIS (intramuros)"),
    "paris-montparnasse": ("FRPMO", "PARIS (intramuros)"),
    "paris-nord": ("FRPNO", "PARIS (intramuros)"),
    "paris-est": ("FRPST", "PARIS (intramuros)"),
    "lyon": (None, "LYON (intramuros)"),
    "marseille": ("FRMSC", "MARSEILLE ST CHARLES"),
    "bordeaux": ("FRBOJ", "BORDEAUX ST JEAN"),
    "lille": (None, "LILLE (intramuros)"),
    "nantes": ("FRNTE", "NANTES"),
    "rennes": ("FRRNS", "RENNES"),
    "strasbourg": ("FRAEG", "STRASBOURG"),
    "toulouse": ("FRXYT", "TOULOUSE MATABIAU"),
    "montpellier": ("FRMPL", "MONTPELLIER SAINT ROCH"),
    "montpellier-sud": ("FRSUF", "MONTPELLIER SUD DE FRANCE"),
    "nice": ("FRNIC", "NICE VILLE"),
    "avignon": ("FRAVG", "AVIGNON TGV"),
    "aix": ("FRAIE", "AIX EN PROVENCE TGV"),
    "dijon": ("FRABA", "DIJON VILLE"),
    "grenoble": ("FRGNB", "GRENOBLE"),
    "annecy": ("FRNCY", "ANNECY"),
    "tours": ("FRXSH", "ST PIERRE DES CORPS"),
    "le-mans": ("FRAET", "LE MANS"),
    "angers": ("FRACL", "ANGERS SAINT LAUD"),
    "brest": ("FRBES", "BREST"),
    "metz": ("FRADE", "METZ VILLE"),
    "nancy": ("FRENC", "NANCY"),
}


def _norm(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


def planner_code(name: str) -> str | None:
    entry = STATIONS.get(_norm(name))
    return entry[0] if entry else name.strip().upper()


def opendata_label(name: str) -> str:
    entry = STATIONS.get(_norm(name))
    return entry[1] if entry else name.strip().upper()


def is_known(name: str) -> bool:
    n = name.strip()
    return _norm(n) in STATIONS or (len(n) == 5 and n[:2].upper() == "FR")
