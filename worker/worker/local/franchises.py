"""Enseignes BANNIES : chaînes, franchises, réseaux et concessions. Leur site est fourni par l'enseigne : rien à leur vendre.

Écartées DÈS LA DÉCOUVERTE (jamais importées, jamais recherchées) : on gagne du temps et des requêtes de moteurs.
Liste par métier. Tes propres ajouts : Paramètres → « Enseignes bannies » (clé `local_banned_brands`).

Deux familles :
  BRANDS     nom distinctif : banni dès qu'il apparaît dans le nom ou l'enseigne (« Franck Provost Troyes », « SARL X - Basic-Fit »).
  AMBIGUOUS  aussi un prénom ou un mot courant (« Paul », « Casino », « Orange ») : banni seulement si le nom EST l'enseigne
             (hors forme juridique et ville), dans son métier (préfixes NAF) — « Chez Paul », « Garage Paul » restent des cibles.
"""
from __future__ import annotations

BRANDS: dict[str, str] = {
    "restauration": """
    paul le cafe | paul le café |
        mcdonald|mcdonalds|mc donald s|burger king|kfc|subway|dominos|domino s|domino s pizza|pizza hut|papa john s|speed rabbit|speed rabbit pizza|
        pizza sprint|la boite a pizza|le kiosque a pizzas|kiosque a pizzas|pizza cosy|la pizza de nico|pizza pai|del arte|pizza del arte|tablapizza|
        pizza bonici|buffalo grill|hippopotamus|courtepaille|flunch|la pataterie|leon de bruxelles|poivre rouge|les 3 brasseurs|3 brasseurs|
        trois brasseurs|indiana cafe|memphis coffee|bistro regent|le bistro regent|volfoni|o tacos|tacos avenue|nabab kebab|chamas tacos|
        five guys|big fernand|king marcel|le paradis du fruit|pitaya|sushi shop|planet sushi|eat sushi|sushi daily|cojean|exki|
        pomme de pain|class croute|la croissanterie|brioche doree|la mie caline|marie blachere|boulangerie marie blachere|boulangerie paul|
        boulangerie ange|boulangerie louise|maison pradier|le fournil de pierre|le pain quotidien|bagelstein|columbus cafe and co|starbucks|
        french coffee shop|coffee shop company|jeff de bruges|leonidas|de neuville|la cure gourmande|amorino|yogurt factory|the french bastards|
        bchef|black and white burger|factory and co|o sullivans|pub o sullivans|the frog|au pain de mon enfance|
        tommy s diner|holly s diner|pokawa|bioburger|la taverne de maitre kanter|maitre kanter|
        les fils a maman|pizza pino|ninkasi|la cantine du troquet|bistrot du boucher|le bistrot du boucher|
        la boulangerie ange|boulangerie feuillette|brasserie flo|
    """,
    "caves_epiceries": """
        cavavin|v and b|v b|le repaire de bacchus|intercaves|la grande epicerie|comptoir irlandais|nysa|
        carrefour city|carrefour express|carrefour market|carrefour contact|hyper u|super u|u express|intermarche|intermarche express|
        auchan|my auchan|auchan piéton|lidl|aldi|monoprix|monop|franprix|picard surgeles|petit casino|casino shop|spar|vival|coccinelle|
        coccimarket|netto|leader price|g20|grand frais|biocoop|naturalia|la vie claire|bio c bon|l eau vive|marcel et fils|
        cora|match|supermarche match|colruyt|8 a huit|huit a 8|proxi super|super proxi|epicery|
        maison de la presse|mag presse|tabac presse relay|relay h
    """,
    "coiffure_beaute": """
        jean louis david|franck provost|saint algue|dessange|jacques dessange|camille albane|coiff and co|coiff co|fabio salsa|pascal coste|
        maniatis|tony and guy|toni and guy|coiffirst|interview coiffure|jean claude biguine|biguine|mod s hair|mods hair|la barbe de papa|
        vog coiffure|coiffure tchip|tchip coiffure|hair coif|
        yves rocher|sephora|marionnaud|nocibe|esthetic center|body minute|l occitane|cinq mondes|beauty success|depil tech|centre depil tech|
        passage bleu|guinot institut|institut guinot|carlance|nail s bar|
        clarins |l appart beaute|
    """,
    "optique_audition_sante": """
        optic 2000|afflelou|alain afflelou|krys|grandoptical|grand optical|general d optique|lissac|optical center|vision plus|
        les opticiens mutualistes|ecouter voir|optique lafayette|acuitis|optical discount|keops|direct optic|audika|amplifon|audition sante|
        audilab|audition conseil|audio 2000|audition mutualiste|sonance audition|
        pharmacie lafayette|giphar|pharmabest|aprium|leadersante|pharmavie|univers pharmacie|
        biogroup|cerballiance|synlab|eurofins|eurofins biomnis|inovie|unilabs|dentego|addentis|centre dentaire dentego|
        mon veto|anicura|evidensia|sevetys|univet
    """,
    "auto_moto": """
        norauto|midas|speedy|feu vert|euromaster|vulco|point s|carglass|france pare brise|mondial pare brise|rapid pare brise|
        a plus pare brise|motrio|eurorepar|eurorepar car service|profil plus|first stop|bosch car service|ad expert|top carrosserie|
        dekra|autosur|securitest|auto securite|norisko|ucar|rent a car|hertz|europcar|sixt|elephant bleu|dafy moto|maxxess|
        renault|peugeot|citroen|dacia|toyota|volkswagen|mercedes|mercedes benz|bmw|nissan|hyundai|skoda|seat|cupra|suzuki|mazda|
        honda|volvo|tesla|mg motor|ds automobiles|jeep|alfa romeo|lexus|porsche|land rover|jaguar|mitsubishi|yamaha|kawasaki|
        harley davidson|triumph|ducati|aramis auto|autohero|distinxion|la centrale auto
    """,
    "immobilier_finance_assurance": """
        century 21|laforet|guy hoquet|stephane plaza|stephane plaza immobilier|foncia|nexity|era immobilier|square habitat|citya|safti|
        capifrance|optimhome|keller williams|propriete privee|proprietes privees|megagence|bsk immobilier|arthurimmo|arthur immo|
        immo de france|sergic|oralia|efficity|coldwell banker|sotheby s|sotheby s international realty|engel volkers|
        agence l adresse|vaneau|
        axa|allianz|groupama|maaf|macif|matmut|generali|aviva|abeille assurances|mutuelle de poitiers|thelem|swisslife|swiss life|
        credit agricole|caisse d epargne|banque populaire|bnp paribas|societe generale|lcl|credit mutuel|la banque postale|hsbc|
        meilleurtaux|cafpi|empruntis|ace credit|vousfinancer|in and fi credits|
        ex im|agenda diagnostics|diagamter|allo diagnostic|
        adecco|manpower|randstad|start people|proman|temporis|partnaire|interaction interim|
        selectour|havas voyages|marmara|leclerc voyages|carrefour voyages|look voyages|salaun holidays|
        pompes funebres generales|roc eclerc|le choix funeraire|funecap
    """,
    "fleurs_animaux_jardin": """
        interflora|monceau fleurs|rapid flore|au nom de la rose|emova|l agitateur floral|carrement fleurs|cash fleurs|
        animalis|maxi zoo|tom and co|jardiland|truffaut|gamm vert|point vert|magasin vert|delbard|
        desjoyaux|piscines desjoyaux|diffazur|magiline|everblue|aquilus|l esprit piscine|cash piscines|hydro sud
    """,
    "sport_loisirs": """
        basic fit|fitness park|l orange bleue|keep cool|magic form|neoness|wellness sport club|vita liberte|cmg sports club|gigagym|
        lappart fitness|l appart fitness|curves|anytime fitness|elancia|freeness|urban soccer|le five|
        ecf|stych|ornikar|en voiture simone|auto ecole cer|auto ecole ecf|lepermislibre|
        acadomia|completude|anacours|cours legendre|kumon|wall street english|berlitz|inlingua|
        les petits chaperons rouges|people and baby|babilou|la maison bleue|
        intersport|sport 2000|go sport|decathlon
    """,
    "hotels": """
        ibis|ibis budget|ibis styles|novotel|campanile|kyriad|premiere classe|b and b hotel|b b hotel|best western|holiday inn|
        fasthotel|brit hotel|hotel f1|appart city|zenitude|odalys|pierre et vacances|center parcs|marriott|hilton|radisson|sofitel|
        pullman|quick palace|balladins|eklo|hotel premiere classe|mercure hotel|hotel mercure
    """,
    "services": """
        5 a sec|5asec|mail boxes etc|mister minit|minit|cle minute|talon minute|cartridge world|bureau vallee|copy top|
        free dom|domidom|family sphere|kangourou kids|babychou|senior compagnie|ages et vie|home instead|vitalliance|
        domiserve|tout a dom services|o2 care services|age d or services|ouihelp|adenior|helpling|
        demeco|les demenageurs bretons|gentlemen du demenagement|demepool|kiloutou|loxam|speed queen|
        wefix|point service mobiles|boutique orange|espace sfr|bouygues telecom|
        illico travaux|homeserve|hellio
    """,
    "commerce_habitat": """
        leroy merlin|castorama|bricomarche|brico depot|mr bricolage|bricorama|weldom|tryba|art et fenetres|france fermetures|
        lapeyre|cuisinella|mobalpa|ixina|cuisine plus|socooc|arthur bonnet|k par k|kpark|monsieur store|franciaflex|
        maisons france confort|maisons pierre|maisons phenix|trecobat|babeau seguin|cheminees philippe|
        fnac|darty|kiabi|gifi|la foir fouille|centrakor|stokomani|hema|maisons du monde|conforama|ikea|cash converters|
        easy cash|cash express|troc de l ile|cultura|king jouet|la grande recre|joue club|okaidi|jacadi|du pareil au meme|
        sergent major|tape a l oeil|promod|camaieu|cache cache|jennyfer|pimkie|undiz|darjeeling|naf naf|devred|armand thiery|
        cyrillus|damart|gemo|chaussea|besson chaussures|la halle aux chaussures|eram|minelli|bata|foot locker|
        histoire d or|marc orian|cleor|julien d orcel|swarovski|nature et decouvertes|zodio|carre blanc|claire s|
        rougier et ple|le petit vapoteur|j well|clopinette|cigusto
    """,
}

# Enseigne ambiguë → métiers où le nom exact la désigne (préfixes NAF).
AMBIGUOUS: dict[str, tuple[str, ...]] = {
    "paul": ("10.71", "47.24", "56."), "ange": ("10.71", "47.24", "56."), "feuillette": ("10.71", "47.24", "56."),
    "louise": ("10.71", "47.24"), "la boucherie": ("56.",), "au bureau": ("56.",), "crescendo": ("56.",), "quick": ("56.",),
    "subway": ("56.",), "columbus cafe": ("56.",), "el rancho": ("56.",), "nicolas": ("47.25",), "v and b": ("47.25", "56.3"),
    "casino": ("47.1", "47.2"), "spar": ("47.1",), "vival": ("47.1",), "proxi": ("47.1",), "utile": ("47.1",), "coccinelle": ("47.1",),
    "netto": ("47.1",), "diagonal": ("47.1",), "carrefour": ("47.1",), "leclerc": ("47.1", "47.3"), "e leclerc": ("47.1", "47.3"),
    "picard": ("47.1", "47.2"), "match": ("47.1",), "cora": ("47.1",), "relay": ("47.62",), "norma": ("47.1",),
    "tchip": ("96.02",), "shampoo": ("96.02",), "intermede": ("96.02",), "guinot": ("96.02",), "vog": ("96.02",),
    "atol": ("47.78",), "entendre": ("47.74", "86."), "krys": ("47.78",),
    "midas": ("45.",), "roady": ("45.",), "ford": ("45.",), "opel": ("45.",), "fiat": ("45.",), "kia": ("45.",), "audi": ("45.",),
    "mini": ("45.",), "ada": ("77.1",), "avis": ("77.1",),
    "orpi": ("68.",), "era": ("68.",), "iad": ("68.",), "l adresse": ("68.",), "lamy": ("68.",),
    "gan": ("65.", "66."), "mma": ("65.", "66."), "cic": ("64.",), "crit": ("78.",), "synergie": ("78.",),
    "happy": ("47.76",), "aquarelle": ("47.76",), "botanic": ("47.76", "47.52"),
    "on air": ("93.1",), "episod": ("93.1",), "moving": ("93.1",), "ecf": ("85.53",), "cer": ("85.53",),
    "ibis": ("55.",), "mercure": ("55.",), "greet": ("55.",), "adagio": ("55.",),
    "o2": ("88.", "81.2", "97."), "shiva": ("81.2", "97."),
    "tui": ("79.",), "orange": ("47.42", "61."), "sfr": ("47.42", "61."), "free": ("47.42", "61."), "save": ("95.12", "47.42"),
    "schmidt": ("47.59", "43.32"), "perene": ("47.59", "43.32"), "boulanger": ("47.4", "47.5"), "point p": ("46.73", "47.52"),
    "action": ("47.19", "47.5", "47.7"), "noz": ("47.19",), "but": ("47.59",), "casa": ("47.59",), "normal": ("47.75",),
    "andre": ("47.72",), "brice": ("47.71",), "maty": ("47.77",), "celio": ("47.71",), "jules": ("47.71",), "etam": ("47.71",),
    "bonobo": ("47.71",), "troc": ("47.79",), "courir": ("47.7",), "pandora": ("47.77",), "la halle": ("47.7",), "orchestra": ("47.71",),
    "smart": ("45.",), "barnes": ("68.",), "tui": ("79.",),
}


# Métier (préfixes NAF) de chaque famille d'enseignes : une enseigne n'est rapprochée que d'un établissement du même métier.
CATEGORY_NAF: dict[str, tuple[str, ...]] = {
    "restauration": ("56.", "10.71", "10.13", "47.24"), "caves_epiceries": ("47.1", "47.2"), "coiffure_beaute": ("96.0",),
    "optique_audition_sante": ("47.74", "47.78", "86.", "75."), "auto_moto": ("45.",), "immobilier_finance_assurance": ("68.", "64.", "65.", "66."),
    "fleurs_animaux_jardin": ("47.76", "96.09", "81.30"), "sport_loisirs": ("93.", "85.51", "47.64"), "hotels": ("55.",),
    "services": (), "commerce_habitat": ("47.",),
}


def category_of(phrase: str) -> str | None:
    """Famille (clé de BRANDS) d'une enseigne déjà repliée (`fold`), ou None."""
    from .textmatch import fold
    for cat, block in BRANDS.items():
        if phrase in {fold(p) for p in block.replace("\n", "|").split("|")}:
            return cat
    return None


def sector_matches(phrase: str, naf: str | None) -> bool:
    """L'enseigne `phrase` est-elle du même métier que ce code NAF ? (enseigne ambiguë : ses secteurs ; famille « services » : jamais)."""
    from .textmatch import fold
    if not naf:
        return False
    sectors = next((v for k, v in AMBIGUOUS.items() if fold(k) == phrase), None) or CATEGORY_NAF.get(category_of(phrase) or "", ())
    return bool(sectors) and naf.upper().startswith(tuple(x.upper() for x in sectors))


def phrases() -> list[str]:
    from .textmatch import fold
    out = {fold(p) for block in BRANDS.values() for p in block.replace("\n", "|").split("|") if fold(p)}
    return sorted(out | {fold(a) for a in AMBIGUOUS})
