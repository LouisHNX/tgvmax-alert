"""Short station names -> (planner code, open-data label).

The planner codes and open-data labels below are best guesses and MUST be
checked once:
  - planner code: DevTools -> Network on the max-planner page, run a search,
    look at the "origin"/"destination" values in the request body.
  - open-data label: values of the `origine` / `destination` fields in the
    data.sncf.com `tgvmax` dataset.
You can also skip this table entirely: `/add FRPLY FRLPD 15/11` uses raw
planner codes, and any unknown name is passed through as-is.
"""

STATIONS: dict[str, tuple[str, str]] = {
    "paris": ("FRPAR", "PARIS (intramuros)"),
    "paris-lyon": ("FRPLY", "PARIS (intramuros)"),
    "paris-montparnasse": ("FRPMO", "PARIS (intramuros)"),
    "paris-nord": ("FRPNO", "PARIS (intramuros)"),
    "paris-est": ("FRPST", "PARIS (intramuros)"),
    "lyon": ("FRLYS", "LYON (intramuros)"),
    "marseille": ("FRMSC", "MARSEILLE ST CHARLES"),
    "bordeaux": ("FRBOJ", "BORDEAUX ST JEAN"),
    "lille": ("FRLLE", "LILLE (intramuros)"),
    "nantes": ("FRNTE", "NANTES"),
    "rennes": ("FRRNS", "RENNES"),
    "strasbourg": ("FRSXB", "STRASBOURG"),
    "toulouse": ("FRTLS", "TOULOUSE MATABIAU"),
    "montpellier": ("FRMPL", "MONTPELLIER SAINT ROCH"),
    "nice": ("FRNIC", "NICE VILLE"),
    "avignon": ("FRAVG", "AVIGNON TGV"),
    "aix": ("FRAIE", "AIX EN PROVENCE TGV"),
    "dijon": ("FRDJV", "DIJON VILLE"),
    "grenoble": ("FRGNB", "GRENOBLE"),
    "annecy": ("FRNCY", "ANNECY"),
    "tours": ("FRXSH", "ST PIERRE DES CORPS"),
    "le-mans": ("FRLEM", "LE MANS"),
    "angers": ("FRANE", "ANGERS SAINT LAUD"),
    "brest": ("FRBES", "BREST"),
    "metz": ("FRMZM", "METZ VILLE"),
    "nancy": ("FRNCY", "NANCY"),
}


def _norm(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


def planner_code(name: str) -> str:
    entry = STATIONS.get(_norm(name))
    return entry[0] if entry else name.strip().upper()


def opendata_label(name: str) -> str:
    entry = STATIONS.get(_norm(name))
    return entry[1] if entry else name.strip().upper()


def is_known(name: str) -> bool:
    n = name.strip()
    return _norm(n) in STATIONS or (len(n) == 5 and n[:2].upper() == "FR")
