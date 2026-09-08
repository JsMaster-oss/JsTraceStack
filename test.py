# -*- coding: utf-8 -*-
"""Vérification du graphique de cascade cyclée, sans sortir de données.

À lancer sur ta machine. Le rapport ne contient que des comptages, des
pourcentages et des quantiles — jamais une référence article, une désignation
ou un fournisseur. Il est fait pour être recopié tel quel dans la conversation.

    python3 verification.py --csv export_power_bi.csv
    python3 verification.py --csv export_power_bi.csv --graph graph.json
    python3 verification.py --csv export_power_bi.csv --designation "MON ENSEMBLE"
    python3 verification.py --csv export_power_bi.csv --excel export.xlsx
    python3 verification.py --csv apres.csv --avant avant.csv

Comment récupérer `graph.json` :

  1. ouvrir la page du graphique dans l'application ;
  2. F12, onglet Réseau, puis cocher « Conserver le journal » ;
  3. choisir la désignation pour que le graphique se trace ;
  4. repérer la requête `create_graph_analyse_CCC2` dans la liste ;
  5. Chrome / Edge : clic droit dessus -> Copier -> Copier la réponse.
     Firefox : onglet Réponse -> clic droit -> Copier tout.
  6. coller dans un éditeur, enregistrer sous `graph.json`.

Le script accepte la réponse brute `{"graph": "..."}` comme la figure Plotly
seule, et reconnaît tout seul la désignation représentée — inutile de préciser
`--designation`.

Ce que le script fait, section par section :

  1. contrôle que le CSV porte bien toutes les colonnes attendues ;
  2. rejoue les invariants qui doivent tenir à 100 % par construction ;
  3. décrit la structure de l'arbre article / article parent ;
  4. **recalcule le graphique par un second chemin de code**, écrit
     différemment de celui des routes, et compare ;
  5. si `--graph` est fourni, réconcilie barre par barre ce que le navigateur a
     réellement reçu avec ce que le CSV dit ;
  6. mesure l'écart entre le planning RG-038 et le compte à rebours SAP.

La section 4 est celle qui répond à « est-ce que ça correspond » : deux
implémentations indépendantes doivent tomber sur le même chiffre.
"""

import argparse
import base64
import json
import sys

import numpy as np
import pandas as pd

TOLERANCE = 0.011  # les colonnes du graphique sont arrondies au centième

COLONNES_CSV = [
    # MODIF VERIF-23 : "Type_appro" et "Appro_spec" ajoutées. Sans elles, la
    # règle GRAPH-23 ne peut pas s'appliquer et tout est planifié en silence.
    "Type_appro", "Appro_spec",
    "Article", "article parent", "Analysé", "Niveau", "désignation article de tête",
    "Cyc_Cum", "Délai_Sécu", "Tps_Recep", "ZPIF", "ZO1", "ZO2", "Delta SAP",
    "Démontré", "Risques", "Délai", "Durée restante", "MargeAppr",
    "Délais analysé (mois)", "Délais non analysé (mois)",
]

# poste tracé -> (colonne source du CSV, décimales d'arrondi, diviseur)
# MODIF VERIF-13 : "Marge appro (mois)" retirée de POSTES. Elle n'est plus
# tracée (MODIF GRAPH-13a) et n'entre plus dans la somme des postes : elle
# décale le t0. Elle garde sa propre définition juste en dessous.
POSTES = {
    "Tmps recep (mois)": ("Tps_Recep", 2, 20),
    "Délai sécu (mois)": ("Délai_Sécu", 1, 20),
    "Cycle Industriel (mois)": ("ZPIF", 1, 20),
    "Autres, Appros ou Semi-Finis (mois)": ("ZO2", 1, 20),
    "Appros Longs LLI (mois)": ("ZO1", 1, 20),
    "Somme des retards démontrés (mois)": ("Démontré", 1, 20),
    "Cycle SAP (mois)": ("Delta SAP", 1, 20),
    "Risque majorant (mois)": ("Risques", 1, 20),
    "Délais SAP non Analysé (mois)": ("Délais non analysé (mois)", None, 1),
}

# MODIF VERIF-13 : bloc ajouté. Le diviseur est négatif parce que RG-040
# SOUSTRAIT la marge du lien : une marge négative — le cas courant — éloigne
# l'enfant de la livraison, une marge positive le rapproche et le fait démarrer
# pendant le cycle de son parent.
POSTE_MARGE = "Marge appro (mois)"
SOURCE_MARGE = ("MargeAppr", 2, -20)

resultats = []


def verdict(nom, nb_ko, nb_total, detail="", critique=True):
    """Enregistre et affiche un contrôle. Aucune donnée nominative n'est émise."""
    if nb_total == 0:
        print(f"  --     {nom:52s} sans objet")
        return
    ok = nb_ko == 0
    marque = "OK    " if ok else ("ECHEC " if critique else "ALERTE")
    part = f"{nb_ko}/{nb_total}"
    print(f"  {marque} {nom:52s} {part:>12s}  {detail}")
    resultats.append((nom, ok, critique))


def quantiles(nom, serie, unite=""):
    serie = pd.Series(np.asarray(serie, dtype=float)).replace([np.inf, -np.inf], np.nan).dropna()
    if serie.empty:
        print(f"    {nom:46s} (vide)")
        return
    nuls = int((serie.abs() < 1e-9).sum())
    q = serie.quantile([0.05, 0.5, 0.95])
    print(f"    {nom:46s} n={len(serie):5d} nuls={nuls:5d} ({nuls / len(serie):5.1%})")
    print(f"    {'':46s} min={serie.min():9.2f}  p5={q.iloc[0]:9.2f}  "
          f"med={q.iloc[1]:9.2f}  p95={q.iloc[2]:9.2f}  max={serie.max():9.2f} {unite}")


# ---------------------------------------------------------------------------
# 4. Second chemin de code : récursion mémoïsée, volontairement écrite
#    autrement que la descente par profondeur des routes.
# ---------------------------------------------------------------------------

# MODIF VERIF-13 : signature élargie, `decalage` ajouté.
# MODIF VERIF-16 : `reductibles` et `analyse` ajoutés, et la fonction renvoie
# désormais un troisième élément, les postes réductibles après rognage.
# MODIF VERIF-19 : `protege` ajouté, la somme des postes du parent que le t0
# d'un enfant ne prend pas en compte.
# MODIF VERIF-23 : `planifie` ajouté. Un article non planifié — tout ce qui est
# sous un achat, cf. GRAPH-23 — n'a aucun cycle : sa barre est vide.
def cascade_independante(articles, parents, duree_propre, decalage=None,
                         reductibles=None, analyse=None, protege=None,
                         planifie=None):
    """Renvoie (t0, total). Rattachement au parent identique aux routes :
    dernière occurrence précédente, repli sur une occurrence suivante.

    MODIF VERIF-13 : `decalage` est la marge appro en mois, signe déjà inversé
    (-MargeAppr/20). Elle décale le t0 au lieu de s'ajouter aux postes tracés,
    comme le fait MODIF GRAPH-13b. Une racine n'en prend pas : pas de parent,
    pas de lien, pas de marge.
    """
    n = len(articles)
    positions = {}
    for i, art in enumerate(articles):
        positions.setdefault(art, []).append(i)

    def parent_de(i):
        ref = parents[i]
        if ref is None or (isinstance(ref, float) and np.isnan(ref)):
            return -1
        cands = positions.get(ref)
        if not cands:
            return -1
        avant = [c for c in cands if c < i]
        if avant:
            return avant[-1]
        apres = [c for c in cands if c > i]
        return apres[0] if apres else -1

    # MODIF VERIF-13 : trois lignes ajoutées, marge nulle si l'appelant n'en
    # fournit pas — le comportement d'avant GRAPH-13.
    if decalage is None:
        decalage = np.zeros(n)
    decalage = np.asarray(decalage, dtype=float)

    # MODIF VERIF-16 : bloc ajouté. `reductibles` est la matrice des postes que
    # la marge peut rogner sur un analysé, `analyse` le masque des lignes
    # concernées. Sans eux, on retombe sur le comportement d'avant GRAPH-16.
    if reductibles is None:
        reductibles = np.zeros((n, 0))
    reductibles = np.asarray(reductibles, dtype=float).copy()
    if analyse is None:
        analyse = np.zeros(n, dtype=bool)
    analyse = np.asarray(analyse, dtype=bool)

    # MODIF VERIF-19 : sans protégés, on retombe sur le comportement d'avant
    # GRAPH-19, où le t0 partait du Délai total entier du parent.
    if protege is None:
        protege = np.zeros(n)
    protege = np.asarray(protege, dtype=float)

    # MODIF VERIF-23 : sans le masque, tout est planifié — comportement d'avant
    # GRAPH-23. Les postes d'une ligne non planifiée sont annulés ici, ce qui
    # met sa durée propre et ses réductibles à zéro d'un coup.
    if planifie is None:
        planifie = np.ones(n, dtype=bool)
    planifie = np.asarray(planifie, dtype=bool)
    duree_propre = np.asarray(duree_propre, dtype=float).copy()
    duree_propre[~planifie] = 0.0
    reductibles[~planifie, :] = 0.0

    memo = {}
    valeurs_t0 = np.zeros(n)
    ronge = np.zeros(n)  # ce que la marge a effectivement pris sur les postes

    def total_de(i, pile):
        if i in memo:
            return memo[i]
        if i in pile:
            raise ValueError(f"boucle article/parent sur {len(pile)} lignes")
        p = parent_de(i)
        # MODIF VERIF-13 : la ligne valait
        # base = 0.0 if p == -1 else total_de(p, pile | {i})
        # MODIF VERIF-19 : « - protege[p] » ajouté. Un enfant est attendu au
        # début du TRAVAIL de son parent, retards démontrés et risque majorant
        # déduits, et non au tout début de sa barre.
        base = (0.0 if p == -1
                else total_de(p, pile | {i}) - protege[p] + decalage[i])

        # MODIF VERIF-16 : sur un analysé, une marge qui raccourcit ne pousse
        # pas le t0 sous zéro ; ce qui dépasse est pris sur les postes
        # réductibles, dans l'ordre, et ce qui reste au-delà est perdu.
        if p != -1 and analyse[i] and decalage[i] < 0:
            valeurs_t0[i] = max(base, 0.0)
            reste = -min(base, 0.0)
            for k in range(reductibles.shape[1]):
                if reste <= 0:
                    break
                pris = min(reste, max(reductibles[i, k], 0.0))
                reductibles[i, k] -= pris
                ronge[i] += pris
                reste -= pris
        else:
            valeurs_t0[i] = base

        memo[i] = valeurs_t0[i] + duree_propre[i] - ronge[i]
        return memo[i]

    sys.setrecursionlimit(max(10000, n * 4))
    total = np.array([total_de(i, frozenset()) for i in range(n)])
    # MODIF VERIF-13 : la ligne lisait memo[parent_de(i)] seul, sans la marge.
    # MODIF VERIF-16 : le t0 est désormais retenu pendant la descente.
    return np.round(valeurs_t0, 2), np.round(total, 2), np.round(reductibles, 2)


# MODIF VERIF-23 : fonction ajoutée, jumelle de la règle de GRAPH-23.
def articles_planifies(df):
    """Quelles lignes le calcul de besoins planifie-t-il ?

    RG-038 le dit : Type_appro "E" prend un cycle de fabrication, "F" un délai
    d'approvisionnement. Un article fabriqué éclate sa nomenclature ; un
    article acheté ne l'éclate pas, ce que le fournisseur a fait est déjà dans
    son délai. Rien n'est donc planifié sous un achat, à un cran près :
    l'appro confiée F/30, où c'est nous qui fournissons les composants.

    Le rattachement au parent reprend la règle du graphique : dernière
    occurrence précédente, repli sur une occurrence suivante.
    """
    n = len(df)
    if "Type_appro" not in df.columns:
        return np.ones(n, dtype=bool)

    articles = df["Article"].astype(str).tolist()
    refs = df["article parent"].astype(str).tolist()
    positions = {}
    for i, art in enumerate(articles):
        positions.setdefault(art, []).append(i)

    parent_pos = np.full(n, -1, dtype=int)
    for i, ref in enumerate(refs):
        cands = positions.get(ref, [])
        avant = [c for c in cands if c < i]
        apres = [c for c in cands if c > i]
        if avant:
            parent_pos[i] = avant[-1]
        elif apres:
            parent_pos[i] = apres[0]

    profondeur = np.zeros(n, dtype=int)
    for depart in range(n):
        courant, d, vus = parent_pos[depart], 0, set()
        while courant != -1 and courant not in vus:
            vus.add(courant)
            d += 1
            courant = parent_pos[courant]
        profondeur[depart] = d

    type_appro = df["Type_appro"].astype(str).str.strip().str.upper().to_numpy()
    if "Appro_spec" in df.columns:
        spec = pd.to_numeric(df["Appro_spec"], errors="coerce").fillna(0).to_numpy()
    else:
        spec = np.zeros(n)

    planifie = np.ones(n, dtype=bool)
    for ligne in np.argsort(profondeur, kind="stable"):
        parent = parent_pos[ligne]
        if parent == -1:
            continue
        if not planifie[parent]:
            planifie[ligne] = False
        elif type_appro[parent] == "F":
            planifie[ligne] = spec[parent] == 30
    return planifie


GROUPE_LIEN = ["Tmps recep (mois)", "Délai sécu (mois)"]

# MODIF VERIF-16 : bloc ajouté, jumeau de BLOC_REDUCTIBLE dans les routes. Sur
# un ANALYSÉ, une marge qui raccourcit consomme le t0 puis ces postes, dans cet
# ordre, et s'arrête là. Cycle SAP, retards démontrés et risque majorant sont
# protégés. Les non analysés gardent le décalage de t0 sans borne.
BLOC_REDUCTIBLE = [
    "Tmps recep (mois)",
    "Délai sécu (mois)",
    "Cycle Industriel (mois)",
    "Autres, Appros ou Semi-Finis (mois)",
    "Appros Longs LLI (mois)",
    # MODIF VERIF-18 : "Cycle SAP (mois)" ajoutée au bloc réductible. Le bloc
    # protégé se réduit donc à deux postes : Somme des retards démontrés et
    # Risque majorant.
    "Cycle SAP (mois)",
]

# MODIF VERIF-19 : bloc ajouté. Les deux seuls postes qu'une marge ne peut pas
# ronger — et, surtout, ceux que le t0 d'un enfant ne prend PAS en compte.
#
# Un enfant n'est pas attendu au tout début de la barre de son parent, mais au
# début du TRAVAIL de son parent. Les retards démontrés et le risque majorant
# sont du rembourrage posé en bout de barre, du côté le plus éloigné de la
# livraison : le composant n'a pas à être là avant que ce rembourrage soit
# écoulé. D'où :
#
#     point de départ(parent) = Délai total(parent) - retards - risque
#     t0(enfant)              = point de départ(parent) - marge(enfant)
#
# Cela vaut pour TOUS les enfants, avec ou sans marge. Sur un parent non
# analysé les deux postes valent zéro, le point de départ égale donc son Délai
# total et rien ne change pour lui — aucun cas particulier à écrire.
GROUPE_PROTEGE = [
    "Somme des retards démontrés (mois)",
    "Risque majorant (mois)",
]

GROUPE_CYCLE_SAP = [
    "Cycle Industriel (mois)",
    "Autres, Appros ou Semi-Finis (mois)",
    "Appros Longs LLI (mois)",
]


def ajuster_affichage(calcule):
    """Reproduit la règle d'affichage des routes : un segment négatif n'est pas
    écrêté mais reporté sur ses voisins, pour que la barre garde sa longueur.

    Écrit avec pandas là où la route travaille en numpy : deux formulations
    différentes de la même règle, c'est le but.
    """
    affichage = calcule.copy()
    groupe = [c for c in GROUPE_CYCLE_SAP if c in affichage.columns]

    if "Cycle SAP (mois)" in affichage.columns and groupe:
        sap = affichage["Cycle SAP (mois)"]
        negatif = sap < 0
        base = affichage.loc[negatif, groupe].clip(lower=0).sum(axis=1)
        absorbe = (-sap[negatif]).clip(upper=base)
        facteur = (1 - absorbe / base.replace(0, np.nan)).fillna(0.0).clip(lower=0)
        for colonne in groupe:
            affichage.loc[negatif, colonne] = (
                affichage.loc[negatif, colonne].clip(lower=0) * facteur
            )
        affichage.loc[negatif, "Cycle SAP (mois)"] = -(-sap[negatif] - absorbe)

    # MODIF VERIF-13 : le bloc qui répartissait une marge négative sur Tmps
    # recep et Délai sécu est supprimé, comme dans les routes (MODIF GRAPH-13c).
    # La marge n'est plus une colonne de `calcule`.

    autres = [c for c in affichage.columns if c != "date début t0 (mois)"]
    reste = affichage[autres]
    a_negatif = (reste < 0).any(axis=1)
    if a_negatif.any():
        deficit = -reste[reste < 0].sum(axis=1)[a_negatif]
        positifs = reste.clip(lower=0)
        base = positifs.loc[a_negatif].sum(axis=1)
        facteur = (1 - (deficit.clip(upper=base) / base.replace(0, np.nan))
                   ).fillna(0.0).clip(lower=0)
        for colonne in autres:
            affichage.loc[a_negatif, colonne] = (
                positifs.loc[a_negatif, colonne] * facteur
            )

    return affichage.clip(lower=0).round(2)


def postes_depuis_csv(df):
    """Reconstruit les colonnes tracées à partir du CSV, comme le font les routes."""
    calcule = {}
    for poste, (source, decimales, diviseur) in POSTES.items():
        valeurs = pd.to_numeric(df[source], errors="coerce").fillna(0.0) / diviseur
        calcule[poste] = valeurs.round(decimales) if decimales is not None else valeurs

    calcule = pd.DataFrame(calcule, index=df.index)

    # RG-040 : le délai du lien est nul dès que Cyc_Cum = 0.
    lien_nul = pd.to_numeric(df["Cyc_Cum"], errors="coerce").fillna(0) == 0
    # MODIF VERIF-13 : la ligne valait
    # calcule.loc[lien_nul, GROUPE_LIEN + ["Marge appro (mois)"]] = 0.0
    # La marge n'est plus une colonne de `calcule`, elle est mise à zéro dans
    # marge_depuis_csv.
    calcule.loc[lien_nul, GROUPE_LIEN] = 0.0
    return calcule


# MODIF VERIF-13 : fonction ajoutée. La marge suit le même chemin que les
# postes — même arrondi, même mise à zéro sur Cyc_Cum = 0 — mais sort à part
# puisqu'elle décale le t0 au lieu de s'ajouter à la barre.
def marge_depuis_csv(df):
    """Marge appro en mois, signe déjà inversé, prête à décaler le t0."""
    source, decimales, diviseur = SOURCE_MARGE
    marge = pd.to_numeric(df[source], errors="coerce").fillna(0.0) / diviseur
    marge = marge.round(decimales)
    lien_nul = pd.to_numeric(df["Cyc_Cum"], errors="coerce").fillna(0) == 0
    marge.loc[lien_nul] = 0.0
    return marge


# ---------------------------------------------------------------------------

def tableau_plotly(valeur):
    if isinstance(valeur, dict) and "bdata" in valeur:
        return np.frombuffer(base64.b64decode(valeur["bdata"]),
                             dtype=np.dtype(valeur["dtype"])).tolist()
    return valeur


SEPARATEURS = [",", ";", "\t", "|"]


def charger_csv(chemin):
    """Lit le CSV sans présumer du séparateur ni du format décimal.

    `to_csv` écrit des virgules et des points décimaux, mais un aller-retour par
    Excel en français rend le fichier en points-virgules avec des virgules
    décimales. Les deux doivent passer, sinon les contrôles numériques tombent
    en silence sur des colonnes lues comme du texte.
    """
    with open(chemin, encoding="utf-8-sig", errors="replace") as f:
        entete = f.readline()

    # Le bon séparateur est celui qui découpe l'en-tête en le plus de colonnes.
    scores = {sep: entete.count(sep) for sep in SEPARATEURS}
    separateur = max(scores, key=lambda sep: scores[sep])

    if scores[separateur] == 0:
        raise SystemExit(
            "Le fichier « {} » n'a qu'une seule colonne dans son en-tête.\n"
            "Première ligne lue :\n  {}\n"
            "Vérifie que c'est bien export_power_bi.csv, et que le chemin est "
            "entre guillemets s'il contient des espaces.".format(
                chemin, entete.strip()[:200])
        )

    lecture = dict(sep=separateur, dtype={"Article": str, "article parent": str,
                                          "Niveau": str},
                   encoding="utf-8-sig")
    try:
        df = pd.read_csv(chemin, **lecture)
    except pd.errors.ParserError as erreur:
        raise SystemExit(
            "Lecture impossible de « {} » avec le séparateur « {} » :\n  {}\n"
            "Si le fichier est passé par Excel, réexporte-le depuis MinIO plutôt "
            "que de l'enregistrer depuis Excel.".format(
                chemin, separateur, erreur)
        )

    # Décimales à la française : une colonne censée être numérique arrive en
    # texte du type "3,5". On relit en le disant à pandas.
    temoins = [c for c in ("Délai", "Cyc_Cum", "Délais non analysé (mois)")
               if c in df.columns]
    virgule = any(
        df[c].dtype == object
        and df[c].astype(str).str.match(r"^\s*-?\d+,\d+\s*$").any()
        for c in temoins
    )
    if virgule:
        df = pd.read_csv(chemin, decimal=",", **lecture)

    if separateur != "," or virgule:
        print("  (fichier lu avec le séparateur « {} »{})".format(
            separateur, ", décimales à la virgule" if virgule else ""))

    return df


def charger_graphique(chemin):
    contenu = json.load(open(chemin, encoding="utf-8"))
    if isinstance(contenu, dict) and "graph" in contenu:
        contenu = json.loads(contenu["graph"])
    traces = {}
    for trace in contenu["data"]:
        traces[trace["name"]] = {
            cle: tableau_plotly(trace[cle]) for cle in ("x", "y", "customdata")
            if cle in trace
        }
    # MODIF VERIF-14 : bloc ajouté. Les postes sont retrouvés par leur nom exact.
    # Une seule majuscule d'écart — « date début T0 » contre « date début t0 » —
    # et la trace passait pour absente : le segment n'était pas comparé, en
    # silence. On réaligne sur l'orthographe de ce fichier quand les deux noms
    # ne diffèrent que par la casse, et on le dit.
    canoniques = list(POSTES) + ["date début t0 (mois)"]
    par_casse = {nom.casefold(): nom for nom in canoniques}
    realignes = 0
    for nom in list(traces):
        if nom not in canoniques and nom.casefold() in par_casse:
            traces[par_casse[nom.casefold()]] = traces.pop(nom)
            realignes += 1
    if realignes:
        print(f"  ({realignes} nom(s) de trace réaligné(s) à la casse près : la "
              f"figure et ce fichier ne les écrivent pas pareil)")
    return traces


# ---------------------------------------------------------------------------

# Colonnes attendues comme modifiées par la bascule RG-038 + gains ZO1/ZO2.
# Tout ce qui bouge en dehors de cette liste est une surprise à expliquer.
BOUGENT = {
    "Délai", "Délai non analysé", "Délais analysé (mois)",
    "Délais non analysé (mois)", "Date début", "date début TO",
    "date début To (mois)", "Durée restante",
}


def comparer_avant_apres(avant, apres, designation):
    """Non-régression : ce qui a bougé entre l'ancien CSV et le nouveau.

    L'appariement se fait sur (désignation, Article, rang d'occurrence), pour
    ne pas confondre deux montages d'un même composant.
    """
    section("7. COMPARAISON AVANT / APRÈS")
    print("  Les trois modifications changent des valeurs : le compare ne sera")
    print("  pas vide, c'est normal. Ce qu'il faut vérifier, c'est que les lignes")
    print("  qui bougent soient bien celles attendues, et rien d'autre.")
    print()

    if designation:
        avant = avant[avant["désignation article de tête"] == designation]

    def cle(df):
        rang = df.groupby(["désignation article de tête", "Article"]).cumcount()
        return list(zip(df["désignation article de tête"], df["Article"], rang))

    avant = avant.copy()
    apres = apres.copy()
    avant.index = cle(avant)
    apres.index = cle(apres)

    communes = avant.index.intersection(apres.index)
    verdict("mêmes lignes avant et après",
            len(avant) + len(apres) - 2 * len(communes), len(apres),
            f"avant {len(avant)}, après {len(apres)}, communes {len(communes)}")
    if len(communes) == 0:
        return

    avant, apres = avant.loc[communes], apres.loc[communes]
    analyse = apres["Analysé"].astype(str).str.upper() == "OUI"

    surprises = []
    print(f"    {'colonne':34s} {'lignes modifiées':>18s}   écarts")
    for colonne in apres.columns:
        if colonne not in avant.columns:
            continue
        a = pd.to_numeric(avant[colonne], errors="coerce")
        b = pd.to_numeric(apres[colonne], errors="coerce")
        if a.isna().all() or b.isna().all():
            differe = (avant[colonne].astype(str) != apres[colonne].astype(str))
            n = int(differe.sum())
            if n:
                print(f"    {colonne:34s} {n:8d}/{len(apres):<9d}  (non numérique)")
                if colonne not in BOUGENT:
                    surprises.append(colonne)
            continue
        ecart = (b - a).fillna(0.0)
        n = int((ecart.abs() > 1e-6).sum())
        if not n:
            continue
        q = ecart[ecart.abs() > 1e-6]
        print(f"    {colonne:34s} {n:8d}/{len(apres):<9d}  "
              f"med={q.median():9.2f}  min={q.min():9.2f}  max={q.max():9.2f}")
        if colonne not in BOUGENT:
            surprises.append(colonne)

    print()
    verdict("aucune colonne inattendue n'a bougé", len(surprises), len(apres.columns),
            f"inattendues : {surprises}" if surprises else "")

    # Les populations qui doivent bouger, et elles seules
    for nom, masque, colonne in [
        ("articles analysés", analyse, "Délai"),
        ("articles non analysés", ~analyse, "Délai"),
    ]:
        a = pd.to_numeric(avant.loc[masque, colonne], errors="coerce").fillna(0.0)
        b = pd.to_numeric(apres.loc[masque, colonne], errors="coerce").fillna(0.0)
        bouge = int(((b - a).abs() > 1e-6).sum())
        total = int(masque.sum())
        print(f"    Délai modifié sur {nom:24s} : {bouge}/{total}"
              + (f" ({bouge / total:.0%})" if total else ""))

    quantiles("Délai analysé : après - avant (jours)",
              pd.to_numeric(apres.loc[analyse, "Délai"], errors="coerce").fillna(0.0)
              - pd.to_numeric(avant.loc[analyse, "Délai"], errors="coerce").fillna(0.0),
              "jours")
    quantiles("Délai non analysé : après - avant (jours)",
              pd.to_numeric(apres.loc[~analyse, "Délai"], errors="coerce").fillna(0.0)
              - pd.to_numeric(avant.loc[~analyse, "Délai"], errors="coerce").fillna(0.0),
              "jours")
    print()
    print("    Attendu : les analysés gagnent (ZO1 + ZO2) x 20 jours, jamais")
    print("    négatif. Les non analysés passent de l'écart de Cyc_Cum à la durée")
    print("    RG-038, l'écart peut aller dans les deux sens.")


def _normaliser(valeur):
    """Forme canonique d'une référence, pour repérer un écart de format."""
    return str(valeur).strip().upper().lstrip("0")


def detailler_orphelins(df):
    """Pourquoi un parent est-il introuvable ? Quatre causes possibles, et une
    cinquième — l'écart de format — qui est la plus facile à corriger."""
    tous = set(df["Article"].astype(str))
    normalises = {}
    for article in tous:
        normalises.setdefault(_normaliser(article), []).append(article)

    par_designation = {}
    for tete, groupe in df.groupby("désignation article de tête"):
        par_designation[tete] = set(groupe["Article"].astype(str))

    compte = {"vide": 0, "sentinelle": 0, "autre désignation": 0,
              "absent du fichier": 0}
    format_seul = 0

    for _, ligne in df.iterrows():
        parent = ligne["article parent"]
        tete = ligne["désignation article de tête"]
        if pd.isna(parent):
            compte["vide"] += 1
            continue
        parent = str(parent)
        if parent in par_designation.get(tete, set()):
            continue
        if parent == "SP00035899":
            compte["sentinelle"] += 1
        elif parent in tous:
            compte["autre désignation"] += 1
        else:
            compte["absent du fichier"] += 1
            if _normaliser(parent) in normalises:
                format_seul += 1

    print()
    print("    Pourquoi le parent est-il introuvable :")
    for cause, n in compte.items():
        if n:
            print(f"      {cause:34s} {n:5d}")
    if format_seul:
        print(f"      dont un Article existe au format près {format_seul:5d}"
              "   <-- zéros de tête, espaces ou casse")
        print("      C'est la cause la plus simple à corriger : la référence")
        print("      existe, elle n'est pas écrite pareil des deux côtés.")
    if compte["autre désignation"]:
        print("      Un parent situé dans une autre désignation article de tête")
        print("      rend la ligne racine ici : son sous-arbre repart de t0 = 0.")


def designation_du_graphique(traces, df):
    """Retrouve la désignation que le graphique représente.

    Une figure ne couvre qu'une désignation article de tête, alors que le CSV
    les contient toutes. On identifie la bonne par recouvrement des articles du
    survol, et non par égalité stricte du nombre de lignes : la figure peut en
    compter davantage que le CSV, ce qui est en soi un signal.

    Renvoie (désignation, nombre de barres, nombre de lignes du CSV).
    """
    donnees = None
    for trace in traces.values():
        if trace.get("customdata"):
            donnees = trace["customdata"]
            break
    if not donnees:
        return None, 0, 0

    articles = [str(c[1]) for c in donnees if len(c) > 1 and str(c[1]).strip()]
    if not articles:
        return None, len(donnees), 0

    uniques = set(articles)
    # MODIF VERIF-25 : le choix se faisait sur le seul recouvrement, et le
    # premier arrivé au maximum gagnait. Dans une famille de produits, deux
    # désignations partagent l'essentiel de leurs composants : on pouvait
    # retenir la voisine, comparer la figure au mauvais sous-ensemble du CSV,
    # et conclure à tort que les deux fichiers ne vont pas ensemble.
    #
    # À recouvrement égal, on départage maintenant par le nombre de lignes :
    # celle qui en a autant que la figure a de barres est la bonne.
    candidats = []
    for tete, groupe in df.groupby("désignation article de tête", dropna=False):
        presents = set(groupe["Article"].astype(str))
        candidats.append((len(uniques & presents) / len(uniques), len(groupe)))
    candidats.sort(key=lambda c: (-c[0], abs(c[1] - len(donnees))))

    if not candidats or candidats[0][0] < 0.8:
        return None, len(donnees), 0

    meilleur_score = candidats[0][0]
    ex_aequo = [c for c in candidats if meilleur_score - c[0] < 0.02]
    for tete, groupe in df.groupby("désignation article de tête", dropna=False):
        presents = set(groupe["Article"].astype(str))
        if (abs(len(uniques & presents) / len(uniques) - ex_aequo[0][0]) < 1e-9
                and len(groupe) == ex_aequo[0][1]):
            meilleur, taille = tete, len(groupe)
            break

    if len(ex_aequo) > 1:
        print("  (plusieurs désignations collent au graphique, départagées par")
        print("   le nombre de lignes — recouvrement puis lignes :")
        for sc, n in ex_aequo[:4]:
            print(f"     {sc:6.1%}   {n:5d} lignes"
                  + ("   <- retenue" if (sc, n) == ex_aequo[0] else ""))
        print("   si ce n'est pas la bonne, relancer avec --designation \"...\")")

    return meilleur, len(donnees), taille


# MODIF VERIF-14 : deux fonctions ajoutées. La première ne regarde que la
# figure, la seconde compare les références de la figure à celles du CSV. Aucune
# des deux n'émet de référence ni de désignation : uniquement des comptages.

def controles_internes_figure(traces):
    """La figure est-elle cohérente avec elle-même ?

    La somme des segments dessinés, t0 compris, doit valoir le Délai total que
    le survol annonce. C'est vérifiable sans le CSV, donc même quand la figure
    et le CSV n'ont pas le même nombre de lignes.

    Si ce contrôle passe alors que le total paraît trop grand à l'écran, la
    barre ne ment pas : c'est la cascade qui est fausse en amont, et le nombre
    de barres est le premier endroit où regarder.
    """
    tailles = {len(t["x"]) for t in traces.values() if "x" in t}
    if len(tailles) != 1:
        return
    n_barres = tailles.pop()

    donnees = None
    for trace in traces.values():
        if trace.get("customdata"):
            donnees = trace["customdata"]
            break
    if not donnees or len(donnees) != n_barres:
        return

    dessine = np.zeros(n_barres)
    for nom, trace in traces.items():
        if nom in POSTES or nom == "date début t0 (mois)":
            x = np.asarray(trace["x"], dtype=float)
            if len(x) == n_barres:
                dessine += x

    survol = np.array([float(c[-1]) if c[-1] not in ("", None) else np.nan
                       for c in donnees])
    # Un Délai total négatif est une anomalie de donnée déjà signalée en
    # section 4 : la barre ne descend pas sous zéro, l'écart y est normal et
    # noierait le contrôle. Ces lignes sont écartées, et comptées.
    absurdes = survol < 0
    comparables = ~absurdes & ~np.isnan(survol)
    ecart = np.abs(dessine - survol)
    rate = comparables & (ecart > 0.05)
    ko = int(rate.sum())

    # MODIF VERIF-17 : un t0 négatif est écrêté à zéro au tracé — une barre ne
    # démarre pas après la livraison. Elle mesure alors PLUS que son Délai
    # total, et l'écart vaut exactement le t0 manquant. Ce n'est pas un défaut
    # de tracé, c'est une anomalie de donnée déjà signalée en section 4 sous
    # « aucun décalage t0 négatif » : on la compte à part.
    t0x = traces.get("date début t0 (mois)", {}).get("x")
    if t0x is not None and len(t0x) == n_barres:
        ecretees = rate & (np.asarray(t0x, dtype=float) <= 1e-9) & (dessine > survol)
    else:
        ecretees = np.zeros(n_barres, dtype=bool)
    n_ecretees = int(ecretees.sum())

    verdict("figure seule : longueur dessinée = Délai total du survol",
            ko, int(comparables.sum()),
            f"écart max {ecart[comparables].max():.2f} mois"
            if comparables.any() else "",
            critique=not (n_ecretees and n_ecretees == ko))
    if n_ecretees:
        print(f"      dont {n_ecretees} barre(s) au t0 dessiné nul et plus longues")
        print("      que leur total : t0 négatif écrêté à zéro, cf. section 4.")
        if n_ecretees == ko:
            print("      Ce sont les seules : le tracé lui-même est cohérent.")
    if absurdes.any():
        print(f"      {int(absurdes.sum())} barre(s) à Délai total négatif "
              f"écartée(s) : cf. section 4.")
    # MODIF VERIF-17 : « and n_ecretees != ko ». Quand tout l'écart s'explique
    # par l'écrêtage d'un t0 négatif, ce bloc alarmiste n'a plus lieu d'être :
    # il contredirait la ligne qui vient de dire que le tracé est cohérent.
    if ko and n_ecretees != ko:
        print("      La barre et son survol ne racontent pas la même chose :")
        print("      le défaut est dans le tracé, pas dans les données amont.")
        # MODIF VERIF-15 : le sens de l'écart, puis le profil du défaut.
        signe = dessine[comparables] - survol[comparables]
        if (signe <= 0.05).all():
            print("      Le survol annonce TOUJOURS plus que la barre ne mesure :")
            print("      le total compte quelque chose que le tracé ne dessine pas.")
        elif (signe >= -0.05).all():
            print("      La barre mesure TOUJOURS plus que le survol n'annonce :")
            print("      le tracé dessine quelque chose que le total ne compte pas.")

        if ko >= int(comparables.sum()) - 1:
            print()
            print("      Presque toutes les barres échouent, et une seule passe.")
            print("      C'est le profil d'un poste compté dans le Délai total mais")
            print("      pas dessiné — ou l'inverse : la barre qui passe est celle")
            print("      où ce poste vaut zéro. Le suspect n°1 est le poste t0, qui")
            print("      vaut 0 sur les racines et sur elles seules, et dont le nom")
            print("      doit être identique aux quatre endroits qui le citent.")
            print("      Lancer, sur le fichier des routes :")
            print()
            print("          python3 verifier_hovertemplate.py <tes_routes>.py")
            print()
            print("      La section « NOM DU POSTE t0 » donne les orthographes")
            print("      trouvées et combien de fois chacune.")
        quantiles("écart longueur - survol", np.where(comparables, ecart, np.nan),
                  "mois")
    else:
        print("      La barre mesure bien ce que le survol annonce. Un total qui")
        print("      paraît trop grand vient donc de la cascade, en amont — voir")
        print("      le nombre de barres juste en dessous.")
    quantiles("Délai total lu dans la figure", survol, "mois")


def diagnostiquer_ecart_comptage(traces, df, barres, n, df_complet=None,
                                 ecarts=None):
    """Pourquoi la figure et le CSV n'ont pas le même nombre de lignes.

    Deux causes possibles, qui ne se soignent pas pareil :

      - la jointure avec les fiches duplique des lignes : la figure porte alors
        des références que le CSV a aussi, mais plus de fois ;
      - le CSV n'est pas celui qu'a servi l'application : la figure porte alors
        des références que le CSV ne contient pas du tout.

    Le survol garde la référence article en position 1 du customdata : elle
    suffit à trancher. Rien n'est affiché d'autre que des comptages.
    """
    donnees = None
    for trace in traces.values():
        if trace.get("customdata"):
            donnees = trace["customdata"]
            break
    if not donnees:
        print()
        print(f"    Écart de {abs(barres - n)} barres, cause non mesurable :")
        print("    la figure ne porte pas de customdata exploitable.")
        return

    from collections import Counter
    figure = Counter(str(c[1]).strip() for c in donnees if len(c) > 1)
    csv = Counter(df["Article"].astype(str).str.strip())

    inconnues = {a: k for a, k in figure.items() if a not in csv}
    en_trop = {a: k - csv[a] for a, k in figure.items()
               if a in csv and k > csv[a]}
    manquantes = {a: csv[a] - figure.get(a, 0) for a in csv
                  if csv[a] > figure.get(a, 0)}

    # MODIF VERIF-26 : la liste nominative part dans un fichier LOCAL, jamais
    # dans le rapport. C'est elle qui permet d'aller voir ce qui manque.
    if ecarts:
        with open(ecarts, "w", encoding="utf-8") as f:
            f.write("# références de la figure absentes du sous-ensemble comparé\n")
            for a_, k in sorted(inconnues.items()):
                f.write(f"{a_}\t{k} barre(s)\n")
            f.write("\n# références en double dans la figure\n")
            for a_, k in sorted(en_trop.items()):
                f.write(f"{a_}\t{k} de trop\n")
            f.write("\n# lignes du CSV absentes de la figure\n")
            for a_, k in sorted(manquantes.items()):
                f.write(f"{a_}\t{k} ligne(s)\n")
        print()
        print(f"    Liste nominative écrite dans « {ecarts} » — elle reste sur ta")
        print("    machine, elle n'est pas dans ce rapport.")

    print()
    print(f"    Écart de {barres - n:+d} barres, décomposé par référence article :")
    print(f"      références présentes dans la figure, absentes du CSV : "
          f"{len(inconnues):4d}  ({sum(inconnues.values())} barres)")
    print(f"      références en double dans la figure                  : "
          f"{len(en_trop):4d}  ({sum(en_trop.values())} barres de trop)")
    print(f"      références du CSV absentes de la figure              : "
          f"{len(manquantes):4d}  ({sum(manquantes.values())} lignes)")
    print()

    if sum(en_trop.values()) and not inconnues:
        print("    --> Jointure. Les références existent bien au CSV, la figure les")
        print("        porte simplement plusieurs fois. `generate_data_cycle`")
        print("        déduplique sur Designation_Article seul : une même Référence")
        print("        Article peut survivre plusieurs fois, et")
        print("        `set_index(\"Article\").join(...)` démultiplie alors la ligne.")
        print("        Chaque doublon ajoute une barre ET fausse la cascade, donc")
        print("        les Délais totaux affichés au survol.")
        print("        Vérifier que MODIF GRAPH-11 est bien appliquée aux DEUX")
        print("        routes : drop_duplicates(subset=[\"Référence Article\"])")
        print("        avant le set_index, dans create ET dans update.")
    elif inconnues:
        # MODIF VERIF-25 : contre-épreuve. Ces références sont absentes du
        # sous-ensemble comparé — mais existent-elles ailleurs dans le fichier ?
        # Si oui, ce n'est pas un CSV périmé : c'est la désignation retenue qui
        # est la mauvaise, et c'est mon outil qui se trompe, pas les données.
        ailleurs = 0
        if df_complet is not None:
            tout = set(df_complet["Article"].astype(str).str.strip())
            ailleurs = sum(1 for a in inconnues if a in tout)
            print(f"    dont présentes ailleurs dans le fichier, sous une autre")
        print(f"    désignation article de tête : {ailleurs} sur {len(inconnues)}")

        # MODIF VERIF-26 : les parents introuvables de la section 3 sont-ils
        # parmi ces références ? Si oui, une seule cause explique les deux
        # symptômes : le CSV a perdu des lignes que l'application, elle, avait.
        presents = set(df["Article"].astype(str).str.strip())
        parents_manquants = {p for p in df["article parent"].astype(str).str.strip()
                             if p and p not in presents}
        croisement = parents_manquants & set(inconnues)
        if parents_manquants:
            print(f"    dont parents introuvables de la section 3 : "
                  f"{len(croisement)} sur {len(parents_manquants)}")
            if croisement:
                print()
                print("    --> UNE SEULE CAUSE pour les deux symptômes. Les lignes")
                print("        que la figure a en plus sont exactement celles qui")
                print("        manquent au CSV, et leur absence orpheline les")
                print("        composants restés dessous. Ce n'est pas deux")
                print("        problèmes, c'est un CSV amputé.")
        print()
        if ailleurs == len(inconnues):
            print("    --> Mauvaise désignation retenue, pas un mauvais fichier.")
            print("        Toutes ces références sont bien dans le CSV, mais sous")
            print("        une autre tête : la comparaison porte sur le mauvais")
            print("        sous-ensemble. Deux désignations d'une même famille")
            print("        partagent l'essentiel de leurs composants, et")
            print("        l'identification automatique a pris la voisine.")
            print("        Relancer en imposant la bonne :")
            print()
            print("            python3 verification.py --csv ... --graph ... \\")
            print("                --designation \"LA DÉSIGNATION TRACÉE\"")
        elif ailleurs:
            print("    --> Cas mixte : une partie de l'écart vient de la")
            print("        désignation retenue, le reste d'un fichier périmé.")
            print("        Relancer avec --designation, puis réexporter si l'écart")
            print("        persiste.")
        else:
            print("    --> Périmètres différents. Aucune de ces références n'est")
            print("        dans le CSV, sous aucune désignation : les deux fichiers")
            print("        ne viennent pas du même export. Réexporter")
            print("        export_power_bi.csv depuis MinIO APRÈS avoir retracé le")
            print("        graphique, puis relancer.")
    else:
        print("    --> Ni doublon ni référence inconnue : l'écart vient d'un autre")
        print("        endroit. Envoyer ce bloc tel quel.")


def section(titre):
    print()
    print("=" * 78)
    print(titre)
    print("=" * 78)


def verifier(df, traces, df_excel, designation, df_avant=None, ecarts=None):
    barres_figure, lignes_csv = 0, 0
    if traces and not designation:
        designation, barres_figure, lignes_csv = designation_du_graphique(traces, df)
        if designation:
            print(f"  (graphique de {barres_figure} barres reconnu, contrôles "
                  f"limités à cette désignation : {lignes_csv} lignes au CSV)")
        elif barres_figure:
            print(f"  (graphique de {barres_figure} barres, désignation non "
                  "identifiée — relance avec --designation \"...\")")

    # MODIF VERIF-25 : l'export entier est conservé. Il sert de contre-épreuve
    # en section 5 : une référence absente du sous-ensemble mais présente
    # ailleurs dans le fichier ne dit pas du tout la même chose qu'une
    # référence introuvable partout.
    df_complet = df
    if designation:
        df = df[df["désignation article de tête"] == designation]
    df = df.reset_index(drop=True)

    section("1. CONTRAT DE COLONNES")
    absentes = [c for c in COLONNES_CSV if c not in df.columns]
    verdict("colonnes attendues présentes", len(absentes), len(COLONNES_CSV),
            f"manquantes : {absentes}" if absentes else "")
    if absentes:
        print("\n  Arrêt : le CSV ne porte pas le schéma attendu.")
        return
    print(f"    {len(df)} lignes, "
          f"{df['désignation article de tête'].nunique()} désignation(s)")

    # MODIF VERIF-27 : bloc ajouté. Les références SAP sont zéro-paddées, et
    # `pd.read_csv` sans `dtype` transforme une colonne entièrement numérique en
    # entiers — les zéros de tête disparaissent. La colonne « article parent »,
    # elle, contient la sentinelle SP00035899, qui n'est pas un nombre : pandas
    # la garde en texte, zéros compris.
    #
    # Les deux colonnes cessent alors de parler la même langue, et plus aucun
    # parent zéro-paddé n'est retrouvé. Ce contrôle le voit AVANT que ça casse,
    # sans dépendre du nombre d'orphelins.
    def _zeros(colonne):
        v = df_complet[colonne].dropna().astype(str).str.strip()
        return int(v.str.match(r"^0\d").sum()), len(v)

    z_art, n_art = _zeros("Article")
    z_par, n_par = _zeros("article parent")
    desaccord = (z_art == 0) != (z_par == 0)
    verdict("Article et article parent au même format",
            1 if desaccord else 0, 1,
            f"zéros de tête : {z_art}/{n_art} et {z_par}/{n_par}")
    if desaccord:
        print("      Une colonne porte des zéros de tête, l'autre non. Aucun")
        print("      parent zéro-paddé ne peut plus être retrouvé : chaque")
        print("      enfant concerné devient une racine à t0 = 0.")
        print("      Cause connue : `pd.read_csv` sans `dtype` convertit en")
        print("      entiers une colonne entièrement numérique. « article")
        print("      parent » y échappe grâce à la sentinelle SP00035899, qui")
        print("      n'est pas un nombre — d'où l'asymétrie.")
        print("      À corriger dans generateData.get_donnee_power_bi.")

    # MODIF VERIF-25 : deux désignations qui ne diffèrent que par une espace,
    # une casse ou un accent forment deux groupes distincts. La figure en couvre
    # alors une, le CSV comparé l'autre, et tout le reste du rapport part de
    # travers sans que rien ne le dise. Contrôle fait sur l'export ENTIER.
    tetes = df_complet["désignation article de tête"].dropna().astype(str)
    normalisees = (tetes.str.strip().str.upper()
                   .str.replace(r"\s+", " ", regex=True))
    ecritures = tetes.groupby(normalisees).nunique()
    doublons = ecritures[ecritures > 1]
    verdict("aucune désignation en double à l'espace près",
            len(doublons), tetes.nunique(),
            "sinon la figure et le CSV portent sur des groupes différents")
    if len(doublons):
        print("      Ces désignations existent en plusieurs écritures :")
        for forme, nb in doublons.items():
            lignes = int((normalisees == forme).sum())
            print(f"        {nb} écritures, {lignes} lignes au total")
        print("      Le graphique en trace une, ce rapport en compare une autre.")
        print("      Corriger l'export, ou imposer la bonne avec --designation.")

    num = lambda c: pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    analyse = df["Analysé"].astype(str).str.upper() == "OUI"
    n_oui, n_non = int(analyse.sum()), int((~analyse).sum())
    print(f"    {n_oui} analysés, {n_non} non analysés")

    section("2. INVARIANTS DE CONSTRUCTION  (tout doit être à 0)")

    six = num("ZPIF") + num("ZO2") + num("ZO1") + num("Delta SAP") \
        + num("Démontré") + num("Risques")
    ko = (analyse & ((num("Délai") - six).abs() > 0.01))
    verdict("analysé : Délai = somme des six composantes", int(ko.sum()), n_oui,
            "sinon les gains ZO1/ZO2 sont perdus")

    ko = analyse & ((num("Délais analysé (mois)") - num("Délai") / 20).abs() > 1e-6)
    verdict("analysé : Délais analysé (mois) = Délai / 20", int(ko.sum()), n_oui)

    ko = analyse & (num("Délais non analysé (mois)").abs() > 1e-9)
    verdict("analysé : Délais non analysé (mois) = 0", int(ko.sum()), n_oui,
            "sinon la durée est comptée deux fois")

    ko = analyse & (num("Durée restante").abs() > 1e-9)
    verdict("analysé : Durée restante = 0", int(ko.sum()), n_oui)

    ko = (~analyse) & ((num("Délai") - num("Durée restante")).abs() > 1e-6)
    verdict("non analysé : Délai = Durée restante  (RG-038)", int(ko.sum()), n_non,
            "sinon RG-038 n'est pas appliquée")

    ko = (~analyse) & ((num("Délais non analysé (mois)") - num("Délai") / 20).abs() > 1e-6)
    verdict("non analysé : Délais non analysé (mois) = Délai / 20",
            int(ko.sum()), n_non)

    ko = (~analyse) & (six.abs() > 1e-9)
    verdict("non analysé : six composantes à 0", int(ko.sum()), n_non)

    ko = (~analyse) & (num("Cyc_Cum") == 0) & (num("Délai").abs() > 1e-9)
    verdict("non analysé : Cyc_Cum = 0 -> Délai = 0", int(ko.sum()), n_non)

    # La boucle métier ne renseigne Analysé que pour les lignes rattachées à une
    # désignation de tête. Une valeur vide signale une ligne jamais traitée.
    statut = df["Analysé"].astype(str).str.strip().str.upper()
    jamais = ~statut.isin(["OUI", "NON"])
    verdict("toute ligne porte Analysé = OUI ou NON", int(jamais.sum()), len(df),
            "sinon la boucle métier ne l'a jamais vue")

    section("3. STRUCTURE DE L'ARBRE")

    # dropna=False : sans ça, les lignes dont la désignation de tête est vide
    # disparaissent silencieusement du décompte des racines, alors qu'elles
    # comptent dans le total. C'est ce qui faisait diverger le nombre de
    # racines du nombre de lignes à t0 = 0.
    sans_designation = int(df["désignation article de tête"].isna().sum())
    sans_parent = int(df["article parent"].isna().sum())

    par_designation = df.groupby("désignation article de tête", dropna=False)
    racines, orphelins, doublons, profondeurs = [], 0, 0, []
    for _, groupe in par_designation:
        arts = groupe["Article"].tolist()
        parents = groupe["article parent"].tolist()
        racines.append(sum(1 for p in parents if pd.isna(p) or p not in arts))
        orphelins += sum(1 for p in parents
                         if not pd.isna(p) and p not in arts and str(p) != "SP00035899")
        doublons += len(arts) - len(set(arts))
        pos = {}
        for i, a in enumerate(arts):
            pos.setdefault(a, []).append(i)
        prof = {}
        for i in range(len(arts)):
            d, chaine = i, []
            while d is not None and d not in prof:
                chaine.append(d)
                ref = parents[d]
                cands = pos.get(ref, []) if not pd.isna(ref) else []
                avant = [c for c in cands if c < d]
                apres = [c for c in cands if c > d]
                d = avant[-1] if avant else (apres[0] if apres else None)
                if d in chaine:
                    d = None
            base = 0 if d is None else prof[d] + 1
            for k in reversed(chaine):
                prof[k] = base
                base += 1
        profondeurs.append(max(prof.values()) if prof else 0)

    verdict("une seule racine par désignation",
            sum(1 for r in racines if r != 1), len(racines),
            f"racines par désignation : {sorted(racines)}")
    print(f"    racines au total                : {sum(racines)}"
          "   (autant de lignes à t0 = 0, cf. section 4)")
    if sans_designation:
        print(f"    lignes sans désignation de tête : {sans_designation}"
              "   <-- exclues de TOUS les graphiques")
    print(f"    lignes sans article parent      : {sans_parent}")
    verdict("aucun parent introuvable", orphelins, len(df),
            "un parent absent rend la ligne racine et remet son t0 à 0")
    print(f"    articles montés plusieurs fois : {doublons}")
    print(f"    profondeur maximale             : {max(profondeurs) if profondeurs else 0}")

    if doublons:
        # Restreint aux lignes réellement tracées : celles qui portent une
        # désignation de tête. Le reste n'apparaît dans aucun graphique.
        tracees = df[df["désignation article de tête"].notna()]
        multiples = set(
            tracees["Article"][tracees["Article"].duplicated(keep=False)].astype(str)
        )
        exposees = int(tracees["article parent"].astype(str).isin(multiples).sum())
        print(f"    lignes tracées dont le parent est monté plusieurs fois : {exposees}")
        print("      Ce sont celles dont le t0 dépend de l'occurrence choisie.")
        print("      Le graphique prend la précédente, ce qui est correct ;")
        print("      generateData.py prend la première (.iloc[0]).")

    if orphelins or sans_designation:
        detailler_orphelins(df)

    section("4. RECALCUL INDÉPENDANT DU GRAPHIQUE")
    print("  Les postes sont reconstruits depuis le CSV, puis la cascade est")
    print("  recalculée par récursion mémoïsée — un chemin de code différent de")
    print("  la descente par profondeur des routes. Les deux doivent coïncider.")
    print()

    calcule = postes_depuis_csv(df)
    duree = calcule.sum(axis=1).to_numpy(dtype=float)
    # MODIF VERIF-13 : la marge est passée en quatrième argument. Elle ne fait
    # plus partie de `duree` — elle décale le t0, cf. MODIF GRAPH-13b.
    marge = marge_depuis_csv(df)
    # MODIF VERIF-16 : les postes réductibles et le masque des analysés sont
    # passés à leur tour, et les postes rognés reviennent dans `calcule` — sans
    # quoi la barre dessinée ne vaudrait plus Délai total - t0.
    # MODIF VERIF-23 : les lignes non planifiées voient tous leurs postes mis
    # à zéro AVANT toute mesure, comme le fait le graphique.
    planifie = articles_planifies(df)
    if not planifie.all():
        calcule.loc[~planifie, :] = 0.0
        duree = calcule.sum(axis=1).to_numpy(dtype=float)

    reductibles = [c for c in BLOC_REDUCTIBLE if c in calcule.columns]
    avant_rognage = calcule[reductibles].copy()
    t0, total, rognes = cascade_independante(
        df["Article"].tolist(), df["article parent"].tolist(), duree,
        marge.to_numpy(dtype=float),
        calcule[reductibles].to_numpy(dtype=float),
        analyse.to_numpy(),
        # MODIF VERIF-19 : les postes protégés du parent, déduits du point de
        # départ du t0 de chaque enfant.
        calcule[[c for c in GROUPE_PROTEGE if c in calcule.columns]]
        .sum(axis=1).to_numpy(dtype=float),
        planifie)
    for k, colonne in enumerate(reductibles):
        calcule[colonne] = rognes[:, k]

    # MODIF VERIF-16 : ce que la marge a effectivement pris, poste par poste.
    # `duree` a été calculée AVANT le rognage : elle ne vaut plus la somme des
    # postes tracés, il faut la reprendre après.
    rogne = (avant_rognage - calcule[reductibles]).sum(axis=1).to_numpy(dtype=float)
    duree_tracee = calcule.sum(axis=1).to_numpy(dtype=float)
    quantiles("rogné par la marge sur les postes (mois)",
              np.where(rogne > 1e-9, rogne, np.nan), "mois")

    ecart_max = 0.0
    ko = 0
    for i in range(len(df)):
        attendu = total[i] - t0[i]
        # MODIF VERIF-16 : `duree[i]` -> `duree_tracee[i]`, la somme après
        # rognage. C'est elle que la barre dessine.
        obtenu = duree_tracee[i]
        ecart_max = max(ecart_max, abs(attendu - obtenu))
        if abs(attendu - obtenu) > TOLERANCE:
            ko += 1
    verdict("Délai total - t0 = somme des postes tracés", ko, len(df),
            f"écart max {ecart_max:.4f} mois")

    # MODIF VERIF-13 : « + calcule["Marge appro (mois)"] » retiré de la somme.
    # L'identité vérifiée change de forme : la marge n'est plus dans
    # `total - t0`, elle est dans le t0. Ce que la barre mesure vaut donc
    # maintenant Délai de l'article + Sécu + Recep, sans la marge.
    #
    # MODIF VERIF-16 : `calcule` porte désormais les postes APRÈS rognage, donc
    # Sécu et Recep sont lus rognés, et l'identité tient toujours. En revanche
    # le Délai de l'article, lui, vient du CSV et n'est pas rogné : les lignes
    # où la marge a mordu dans le cycle sortent de l'identité, c'est attendu et
    # c'est compté juste en dessous.
    lien_trace = (calcule["Délai sécu (mois)"] + calcule["Tmps recep (mois)"])
    # MODIF VERIF-16 : `- rogne_cycle`. Sécu et Recep sont lus rognés dans
    # `lien_trace`, mais le Délai de l'article vient du CSV et ignore le
    # rognage : ce que la marge a pris sur Cycle Industriel, Autres et Appros
    # Longs doit donc être retranché ici, sinon l'identité tombe à faux sur les
    # seules lignes où la règle GRAPH-16 s'est appliquée.
    cycle_rognable = [c for c in reductibles if c not in GROUPE_LIEN]
    rogne_cycle = (avant_rognage[cycle_rognable]
                   - calcule[cycle_rognable]).sum(axis=1)
    attendu_oui = num("Délais analysé (mois)") + lien_trace - rogne_cycle
    attendu_non = num("Délais non analysé (mois)") + lien_trace - rogne_cycle
    attendu = np.where(analyse, attendu_oui, attendu_non)
    # MODIF VERIF-23 : une ligne non planifiée n'a aucun cycle, sa barre est
    # vide. Le Délai de l'article vient du CSV et ignore la règle : sans cette
    # remise à zéro, l'identité tombe à faux sur toutes ces lignes.
    attendu = np.where(planifie, attendu, 0.0)
    ecart = (total - t0) - attendu
    ko = int((np.abs(ecart) > TOLERANCE).sum())

    # Chaque poste est arrondi au dixième de mois avant la somme : six postes
    # peuvent donc dériver de 0,30 mois sans que rien ne soit faux. Sous ce
    # seuil c'est une alerte, pas un échec — sinon le rapport ne peut jamais
    # être entièrement vert et on finit par ne plus le lire.
    BORNE_ARRONDI = 0.30
    dans_l_arrondi = ko and np.abs(ecart).max() < BORNE_ARRONDI

    # MODIF VERIF-13 : libellé et détail réécrits, la marge n'est plus dans
    # cette somme.
    verdict("Délai total - t0 = Délai de l'article + Sécu + Recep", ko, len(df),
            "la marge appro est dans le t0, pas dans la barre",
            critique=not dans_l_arrondi)

    if ko:
        for nom, masque in (("analysés", analyse.to_numpy()),
                            ("non analysés", (~analyse).to_numpy())):
            hors = np.abs(ecart[masque]) > TOLERANCE
            if not hors.any():
                continue
            valeurs = np.abs(ecart[masque][hors])
            print(f"      {int(hors.sum())} sur {int(masque.sum())} {nom} : "
                  f"écart médian {np.median(valeurs):.3f}, max {valeurs.max():.3f} mois")
        if dans_l_arrondi:
            print("      Sous la borne d'arrondi de 0,30 mois : chaque poste est")
            print("      arrondi au dixième avant la somme. Ce n'est pas un défaut")
            print("      d'intégration, et c'est classé en alerte à ce titre.")
            print("      Pour l'annuler il faudrait sommer en jours et n'arrondir")
            print("      qu'à l'affichage — ce qui change tous les chiffres portés")
            print("      dans les segments. Pas rentable pour 0,7 jour.")
        else:
            print("      Au-delà de la borne d'arrondi : à regarder de près.")

    # MODIF VERIF-13 : bloc ajouté. Un t0 vaut « total du parent + marge » : si
    # la marge dépasse le total du parent, le t0 devient négatif et le Délai
    # total peut l'être aussi — l'article serait livré après la livraison
    # finale. C'est une anomalie de donnée, pas un défaut de tracé, et elle
    # existait déjà avant GRAPH-13 ; elle devient simplement visible.
    # MODIF VERIF-24 : le détail de ce verdict annonçait « une marge supérieure
    # au total du parent » comme si c'était la seule cause. Il y en a deux, et
    # elles ne se soignent pas pareil. On les sépare au lieu de les confondre.
    negatifs = total < -TOLERANCE
    verdict("aucun Délai total négatif", int(negatifs.sum()), len(df))
    if negatifs.any():
        par_t0 = negatifs & (t0 < -TOLERANCE)
        par_poste = negatifs & ~par_t0
        print(f"      dont t0 déjà négatif, la marge dépasse le point de départ "
              f"du parent : {int(par_t0.sum())}")
        print(f"      dont t0 positif mais un poste négatif, presque toujours le "
              f"Cycle SAP : {int(par_poste.sum())}")
        if par_poste.any():
            sap = calcule["Cycle SAP (mois)"].to_numpy(dtype=float)
            quantiles("Cycle SAP des lignes concernées",
                      np.where(par_poste, sap, np.nan), "mois")
            print("      Un Cycle SAP très négatif veut dire que les gains ZO1 et")
            print("      ZO2 dépassent le cycle SAP réel. Le Délai de l'article en")
            print("      devient négatif, et son Délai total avec. Ce n'est pas un")
            print("      défaut de calcul : c'est ce que dit la fiche. À vérifier")
            print("      côté métier sur ces lignes-là.")

    verdict("aucun décalage t0 négatif", int((t0 < -TOLERANCE).sum()), len(df),
            "la barre démarrerait après la livraison", critique=False)

    quantiles("Délai total (mois)", total, "mois")
    quantiles("décalage t0 (mois)", t0, "mois")
    # MODIF VERIF-13 : quantiles ajoutés. Une marge POSITIVE en jours donne un
    # poste NÉGATIF ici : ce sont ces lignes-là qui font démarrer un enfant
    # pendant le cycle de son parent, le cas F/30.
    quantiles("marge appro portée par le t0 (mois)", marge, "mois")
    print(f"    articles dont la marge avance le démarrage : "
          f"{int((marge < 0).sum())}  (MargeAppr positive dans SAP)")
    negatifs = int((calcule["Cycle SAP (mois)"] < 0).sum())
    print(f"    articles à Cycle SAP négatif  : {negatifs}"
          f"  (segments de cycle réduits à l'écran, cf. section 5)")

    if traces:
        section("5. RÉCONCILIATION AVEC LE GRAPHIQUE REÇU PAR LE NAVIGATEUR")
        manquantes = [p for p in POSTES if p not in traces]
        verdict("tous les postes présents dans la figure",
                len(manquantes), len(POSTES),
                f"absents : {manquantes}" if manquantes else "")
        if manquantes:
            # Un poste « absent » est presque toujours une différence
            # d'orthographe entre ce fichier et celui des routes, pas une trace
            # réellement manquante. Le nom doit être identique au caractère près.
            print()
            print("    Noms présents dans la figure :")
            for nom in sorted(traces):
                print(f"      {nom}")
            print("    Comparer avec les clés de POSTES ci-dessus : espace contre")
            print("    souligné, accent, majuscule. Un écart suffit à fausser la")
            print("    longueur de barre sur toutes les lignes concernées.")

        n = len(df)
        tailles = {len(t["x"]) for t in traces.values() if "x" in t}
        ecart_comptage = tailles and tailles != {n}

        # MODIF VERIF-14 : bloc ajouté. Deux contrôles internes à la figure,
        # faits AVANT toute comparaison avec le CSV : ils tiennent même quand
        # les deux n'ont pas le même nombre de lignes, et ils répondent seuls
        # à « le survol annonce un total que la barre ne fait pas ».
        controles_internes_figure(traces)

        if ecart_comptage:
            barres = max(tailles)
            verdict("la figure a autant de barres que le CSV a de lignes",
                    1, 1, f"figure {barres}, CSV {n}")
            # MODIF VERIF-14 : l'ancien texte énonçait une cause probable. Elle
            # est maintenant mesurée : doublons de références d'un côté,
            # références inconnues de l'autre, ça ne dit pas la même chose.
            diagnostiquer_ecart_comptage(traces, df, barres, n, df_complet,
                                         ecarts)
            print()
            print("    Les comparaisons poste par poste sont sautées : elles")
            print("    n'auraient aucun sens sur des tailles différentes.")
            return

        affiche = ajuster_affichage(calcule)

        # Longueur réellement dessinée : c'est elle qui doit valoir le Délai
        # total, sinon la barre ment sur l'échelle des mois.
        longueur = np.zeros(n)
        for poste in POSTES:
            if poste in traces and len(traces[poste]["x"]) == n:
                longueur += np.asarray(traces[poste]["x"], dtype=float)
        # Une barre ne descend pas sous zéro : quand le délai de lien est
        # négatif au point de dépasser la durée, la contribution de l'article
        # l'est aussi et la barre est vide.
        attendu_longueur = np.maximum(total - t0, 0.0)
        ko = int((np.abs(longueur - attendu_longueur) > 0.05).sum())
        verdict("longueur de barre = Délai total - t0", ko, n,
                "un segment négatif ne doit pas allonger la barre")

        negatifs = int((calcule["Cycle SAP (mois)"] < 0).sum())
        if negatifs:
            print(f"    {negatifs} article(s) à Cycle SAP négatif : leurs trois")
            print(f"    postes de cycle sont réduits à l'écran, le survol garde")
            print(f"    les valeurs réelles.")

        for poste in POSTES:
            if poste not in traces:
                continue
            x = np.asarray(traces[poste]["x"], dtype=float)
            if len(x) != n:
                verdict(f"[{poste[:34]}] nombre de barres", 1, 1,
                        f"figure {len(x)} vs CSV {n}")
                continue
            attendu_poste = affiche[poste].to_numpy(dtype=float)
            ko = int((np.abs(x - attendu_poste) > TOLERANCE).sum())
            verdict(f"segment {poste[:38]}", ko, n)

        if "date début t0 (mois)" in traces:
            x = np.asarray(traces["date début t0 (mois)"]["x"], dtype=float)
            if len(x) == n:
                ko = int((np.abs(x - np.maximum(t0, 0.0)) > TOLERANCE).sum())
                verdict("segment date début t0 (mois)", ko, n)

            donnees = traces["date début t0 (mois)"].get("customdata")
            # MODIF VERIF-14 : « and len(donnees) == n » ajouté. Une figure dont
            # le customdata et les x n'ont pas la même longueur faisait tomber
            # le script sur un broadcast numpy au lieu de le dire.
            if donnees and len(donnees) == n:
                totaux_hover = np.array([float(c[-1]) if c[-1] not in ("", None) else np.nan
                                         for c in donnees])
                ko = int((np.abs(totaux_hover - total) > TOLERANCE).sum())
                verdict("Délai total du survol = recalcul", ko, n,
                        "c'est le chiffre que lit l'utilisateur")

    section("6. ÉCART ENTRE LE PLANNING RG-038 ET LE COMPTE À REBOURS SAP")
    print("  RG-038 lit une durée propre, SAP cumule un compte à rebours. Les deux")
    print("  ne coïncident que si SAP a construit Cyc_Cum en accumulant ces mêmes")
    print("  durées. Cette section mesure l'écart ; elle ne dit pas qui a raison.")
    print()

    arts = df["Article"].tolist()
    parents = df["article parent"].tolist()
    pos = {}
    for i, a in enumerate(arts):
        pos.setdefault(a, []).append(i)
    parent_idx = np.full(len(df), -1, dtype=int)
    for i, ref in enumerate(parents):
        if pd.isna(ref):
            continue
        cands = pos.get(ref, [])
        avant = [c for c in cands if c < i]
        apres = [c for c in cands if c > i]
        if avant:
            parent_idx[i] = avant[-1]
        elif apres:
            parent_idx[i] = apres[0]

    cyc = num("Cyc_Cum").to_numpy(dtype=float)
    # Cyc_Cum = 0 est un marqueur « pas de cycle », pas une position dans le
    # compte à rebours : l'écart avec le parent n'a alors aucun sens.
    #
    # Les articles analysés sont écartés eux aussi. `Durée restante` leur est
    # forcée à 0 par construction — leur durée vient de leur fiche Mongo, pas de
    # SAP — donc les comparer à RG-038 fait apparaître tout leur écart de
    # Cyc_Cum comme un résidu, ce qui n'a aucun sens.
    a_parent = (parent_idx >= 0) & (cyc > 0) & (~analyse).to_numpy()
    a_parent[a_parent] &= cyc[parent_idx[a_parent]] > 0

    ecart_cyc = np.full(len(df), np.nan)
    ecart_cyc[a_parent] = cyc[a_parent] - cyc[parent_idx[a_parent]]

    print(f"    lignes à Cyc_Cum = 0 (marqueur, écartées)  : {int((cyc == 0).sum())} "
          f"sur {len(df)}")
    print(f"    articles analysés (Durée restante forcée)  : {int(analyse.sum())}")
    print(f"    liens réellement comparables               : {int(a_parent.sum())}")
    incoherents = int(np.nansum(ecart_cyc < 0))
    verdict("aucun enfant avec Cyc_Cum inférieur à son parent",
            incoherents, int(a_parent.sum()),
            "l'arbre SAP doit être croissant vers le bas", critique=False)

    duree_propre_j = num("Durée restante").to_numpy(dtype=float)
    # RG-040 : Délai_Sécu + Tps_Recep - MargeAppr. Les lignes à Cyc_Cum = 0 sont
    # déjà écartées de cette section, la mise à zéro du lien ne joue donc pas.
    lien_j = (num("Délai_Sécu").to_numpy(dtype=float)
              + num("Tps_Recep").to_numpy(dtype=float)
              - num("MargeAppr").to_numpy(dtype=float))
    residu = ecart_cyc - duree_propre_j - lien_j

    quantiles("écart Cyc_Cum(i) - Cyc_Cum(parent)", ecart_cyc, "jours")
    quantiles("écart résiduel (RG-038 + RG-040 vs SAP)", residu, "jours")

    # D'où viennent les résidus non nuls ? RG-038 met la durée à 0 quand
    # Appro_spec ou TyApproSpe vaut 50, alors que SAP garde son écart de
    # Cyc_Cum : ces lignes doivent ressortir en tête.
    non_nul = ~np.isnan(residu) & (np.abs(residu) > 1e-9)
    if non_nul.any():
        est_50 = pd.Series(False, index=df.index)
        for colonne in ("Appro_spec", "TyApproSpe"):
            if colonne in df.columns:
                est_50 |= pd.to_numeric(df[colonne], errors="coerce").fillna(0) == 50
        est_50 = est_50.to_numpy()
        sans_cycle = num("Durée restante").to_numpy(dtype=float) == 0

        print()
        print(f"    résidus non nuls                       : {int(non_nul.sum())}")
        print(f"      dont Appro_spec ou TyApproSpe = 50   : {int((non_nul & est_50).sum())}")
        print(f"      dont Durée restante = 0 (autre motif): "
              f"{int((non_nul & sans_cycle & ~est_50).sum())}")
        print(f"      dont durée non nulle                 : "
              f"{int((non_nul & ~sans_cycle).sum())}")
        # Les durées nulles hors appro 50 viennent de colonnes de cycle vides.
        # Si l'autre colonne, elle, est renseignée, c'est la branche E/F de
        # RG-038 qui écarte une valeur que `max(AS, AT)` aurait gardée.
        muettes = non_nul & sans_cycle & ~est_50
        if muettes.any() and {"Cyc_Fab.", "Delai_appr"} <= set(df.columns):
            fab = num("Cyc_Fab.").to_numpy(dtype=float)
            appr = num("Delai_appr").to_numpy(dtype=float)
            recuperable = muettes & (np.maximum(fab, appr) > 0)
            print(f"      parmi ces {int(muettes.sum())} à durée nulle hors appro 50 :")
            print(f"        les deux colonnes de cycle sont vides : "
                  f"{int((muettes & ~recuperable).sum())}")
            print(f"        l'une des deux est renseignée         : "
                  f"{int(recuperable.sum())}"
                  "   <-- la branche E/F écarte une valeur non nulle")
            if recuperable.any():
                quantiles("valeur écartée par la branche E/F",
                          np.where(recuperable, np.maximum(fab, appr), np.nan),
                          "jours")

                # Si le résidu vaut exactement la valeur écartée, c'est que SAP
                # a bâti son Cyc_Cum avec max(AS, AT) et non avec la colonne
                # choisie par la branche E/F.
                maxi = np.maximum(fab, appr)
                colle = recuperable & (np.abs(residu - maxi) < 0.5)
                print(f"        dont résidu = max(Cyc_Fab., Delai_appr) au jour "
                      f"près : {int(colle.sum())}/{int(recuperable.sum())}")
                if colle.sum() == recuperable.sum():
                    print("        --> SAP accumule max(AS, AT). Remplacer la")
                    print("            branche E/F par le max réconcilierait ces")
                    print("            lignes exactement.")

                types = df["Type_appro"].astype(str).str.strip().str.upper()
                print("        répartition de Type_appro sur ces lignes :")
                for valeur in sorted(set(types[recuperable])):
                    masque = recuperable & (types == valeur).to_numpy()
                    fab_vide = int((masque & (fab == 0)).sum())
                    appr_vide = int((masque & (appr == 0)).sum())
                    print(f"          {valeur:8s} {int(masque.sum()):4d}  "
                          f"Cyc_Fab. vide {fab_vide:4d}  Delai_appr vide {appr_vide:4d}")

        reste = non_nul & ~sans_cycle
        if reste.any():
            quantiles("résidu hors durée nulle", np.where(reste, residu, np.nan),
                      "jours")
            print("      Sur ces lignes la durée RG-038 est connue : le résidu est")
            print("      donc le seul fait du terme - MargeAppr de RG-040.")
            if "MargeAppr" in df.columns:
                marge = num("MargeAppr").to_numpy(dtype=float)
                colle = reste & (np.abs(residu - marge) < 0.5)
                print(f"      dont résidu = MargeAppr au jour près : "
                      f"{int(colle.sum())}/{int(reste.sum())}")
                if colle.sum() == reste.sum():
                    print("      --> l'écart est exactement la marge. RG-040 est")
                    print("          appliquée, et le planning s'allonge d'autant")
                    print("          par rapport au compte à rebours SAP.")
    print()
    print("    Ce résidu est l'écart entre le planning calculé (RG-038 + RG-040)")
    print("    et le compte à rebours de SAP. Il n'a pas vocation à être nul :")
    print("    SAP bâtit Cyc_Cum en cumulant durée + Délai_Sécu + Tps_Recep, sans")
    print("    le terme - MargeAppr que RG-040 ajoute. Un résidu égal à MargeAppr")
    print("    sur une ligne est donc le comportement attendu, pas une anomalie.")
    print("    Ce qu'il faut regarder, c'est l'ampleur : elle dit de combien le")
    print("    planning s'écarte volontairement de SAP.")

    if df_excel is not None:
        colonne_marge = next(
            (c for c in df_excel.columns
             if c.replace(" ", "").replace("_", "").lower() in ("margeappr", "marge")),
            None)
        print()
        if colonne_marge is None:
            print("    MargeAppr introuvable dans l'export : comparaison impossible.")
        else:
            marge = pd.to_numeric(df_excel[colonne_marge], errors="coerce").fillna(0.0)
            marge = marge.reindex(range(len(df))).fillna(0.0).to_numpy(dtype=float)
            valides = ~np.isnan(residu)
            proches = int(np.isclose(residu[valides], marge[valides], atol=0.5).sum())
            n_val = int(valides.sum())
            if n_val:
                print(f"    résidu = MargeAppr sur {proches}/{n_val} lignes "
                      f"({proches / n_val:.1%})")
                quantiles("résidu - MargeAppr", np.where(valides, residu - marge, np.nan),
                          "jours")

    if df_avant is not None:
        comparer_avant_apres(df_avant, df, designation)

    section("SYNTHÈSE")
    echecs = [n for n, ok, crit in resultats if not ok and crit]
    alertes = [n for n, ok, crit in resultats if not ok and not crit]
    if not echecs:
        print("  Aucun échec. Le graphique correspond au CSV, et le CSV est")
        print("  cohérent avec ses propres règles de calcul.")
        for nom in alertes:
            print(f"  ALERTE {nom}  (connue et documentée)")
    else:
        for nom in echecs:
            print(f"  ECHEC  {nom}")
        for nom in alertes:
            print(f"  ALERTE {nom}")
    print()
    print("  Ce rapport ne contient aucune référence article ni désignation :")
    print("  il peut être recopié tel quel.")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", required=True, help="export_power_bi.csv")
    p.add_argument("--graph", help="réponse JSON d'une des deux routes de tracé")
    p.add_argument("--excel", help="export SAP brut, pour la colonne MargeAppr")
    p.add_argument("--avant", help="CSV d'avant la bascule, pour la non-régression")
    # MODIF VERIF-26 : la seule sortie nominative du script, et elle va dans un
    # fichier local, jamais à l'écran.
    p.add_argument("--ecarts", metavar="CHEMIN",
                   help="écrit dans ce fichier les références qui expliquent "
                        "l'écart de comptage entre la figure et le CSV")
    p.add_argument("--feuille", default="export")
    p.add_argument("--designation", help="limiter à une désignation article de tête")
    args = p.parse_args()

    df = charger_csv(args.csv)
    traces = charger_graphique(args.graph) if args.graph else None
    df_excel = pd.read_excel(args.excel, sheet_name=args.feuille) if args.excel else None

    df_avant = charger_csv(args.avant) if args.avant else None

    verifier(df, traces, df_excel, args.designation, df_avant, args.ecarts)
    return 1 if any(not ok and crit for _, ok, crit in resultats) else 0


if __name__ == "__main__":
    sys.exit(main())
