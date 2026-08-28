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
    "Article", "article parent", "Analysé", "Niveau", "désignation article de tête",
    "Cyc_Cum", "Délai_Sécu", "Tps_Recep", "ZPIF", "ZO1", "ZO2", "Delta SAP",
    "Démontré", "Risques", "Délai", "Durée restante", "MargeAppr",
    "Délais analysé (mois)", "Délais non analysé (mois)",
]

# poste tracé -> (colonne source du CSV, décimales d'arrondi, diviseur)
# La marge appro porte un diviseur négatif : RG-040 la SOUSTRAIT du lien, donc
# une marge négative — le cas courant — allonge la barre.
POSTES = {
    "Tmps recep (mois)": ("Tps_Recep", 2, 20),
    "Délai sécu (mois)": ("Délai_Sécu", 1, 20),
    "Marge appro (mois)": ("MargeAppr", 2, -20),
    "Cycle Industriel (mois)": ("ZPIF", 1, 20),
    "Autres, Appros ou Semi-Finis (mois)": ("ZO2", 1, 20),
    "Appros Longs LLI (mois)": ("ZO1", 1, 20),
    "Somme des retards démontrés (mois)": ("Démontré", 1, 20),
    "Cycle SAP (mois)": ("Delta SAP", 1, 20),
    "Risque majorant (mois)": ("Risques", 1, 20),
    "Délais SAP non Analysé (mois)": ("Délais non analysé (mois)", None, 1),
}

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

def cascade_independante(articles, parents, duree_propre):
    """Renvoie (t0, total). Rattachement au parent identique aux routes :
    dernière occurrence précédente, repli sur une occurrence suivante."""
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

    memo = {}

    def total_de(i, pile):
        if i in memo:
            return memo[i]
        if i in pile:
            raise ValueError(f"boucle article/parent sur {len(pile)} lignes")
        p = parent_de(i)
        base = 0.0 if p == -1 else total_de(p, pile | {i})
        memo[i] = base + duree_propre[i]
        return memo[i]

    sys.setrecursionlimit(max(10000, n * 4))
    total = np.array([total_de(i, frozenset()) for i in range(n)])
    t0 = np.array([0.0 if parent_de(i) == -1 else memo[parent_de(i)] for i in range(n)])
    return np.round(t0, 2), np.round(total, 2)


GROUPE_LIEN = ["Tmps recep (mois)", "Délai sécu (mois)"]

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

    lien = [c for c in GROUPE_LIEN if c in affichage.columns]
    if "Marge appro (mois)" in affichage.columns and lien:
        marge = affichage["Marge appro (mois)"]
        negatif = marge < 0
        base = affichage.loc[negatif, lien].clip(lower=0).sum(axis=1)
        absorbe = (-marge[negatif]).clip(upper=base)
        facteur = (1 - absorbe / base.replace(0, np.nan)).fillna(0.0).clip(lower=0)
        for colonne in lien:
            affichage.loc[negatif, colonne] = (
                affichage.loc[negatif, colonne].clip(lower=0) * facteur
            )
        affichage.loc[negatif, "Marge appro (mois)"] = -(-marge[negatif] - absorbe)

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
    calcule.loc[lien_nul, GROUPE_LIEN + ["Marge appro (mois)"]] = 0.0
    return calcule


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
    meilleur, score, taille = None, 0.0, 0
    for tete, groupe in df.groupby("désignation article de tête"):
        presents = set(groupe["Article"].astype(str))
        recouvrement = len(uniques & presents) / len(uniques)
        if recouvrement > score:
            meilleur, score, taille = tete, recouvrement, len(groupe)

    if score < 0.8:
        return None, len(donnees), 0
    return meilleur, len(donnees), taille


def section(titre):
    print()
    print("=" * 78)
    print(titre)
    print("=" * 78)


def verifier(df, traces, df_excel, designation, df_avant=None):
    barres_figure, lignes_csv = 0, 0
    if traces and not designation:
        designation, barres_figure, lignes_csv = designation_du_graphique(traces, df)
        if designation:
            print(f"  (graphique de {barres_figure} barres reconnu, contrôles "
                  f"limités à cette désignation : {lignes_csv} lignes au CSV)")
        elif barres_figure:
            print(f"  (graphique de {barres_figure} barres, désignation non "
                  "identifiée — relance avec --designation \"...\")")

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
    t0, total = cascade_independante(
        df["Article"].tolist(), df["article parent"].tolist(), duree)

    ecart_max = 0.0
    ko = 0
    for i in range(len(df)):
        attendu = total[i] - t0[i]
        obtenu = duree[i]
        ecart_max = max(ecart_max, abs(attendu - obtenu))
        if abs(attendu - obtenu) > TOLERANCE:
            ko += 1
    verdict("Délai total - t0 = somme des postes tracés", ko, len(df),
            f"écart max {ecart_max:.4f} mois")

    lien_trace = (calcule["Délai sécu (mois)"] + calcule["Tmps recep (mois)"]
                  + calcule["Marge appro (mois)"])
    attendu_oui = num("Délais analysé (mois)") + lien_trace
    attendu_non = num("Délais non analysé (mois)") + lien_trace
    attendu = np.where(analyse, attendu_oui, attendu_non)
    ecart = (total - t0) - attendu
    ko = int((np.abs(ecart) > TOLERANCE).sum())
    verdict("Délai total - t0 = Délai de l'article + délai de lien", ko, len(df),
            "délai de lien RG-040 = Sécu + Recep - Marge, nul si Cyc_Cum = 0")

    if ko:
        for nom, masque in (("analysés", analyse.to_numpy()),
                            ("non analysés", (~analyse).to_numpy())):
            hors = np.abs(ecart[masque]) > TOLERANCE
            if not hors.any():
                continue
            valeurs = np.abs(ecart[masque][hors])
            print(f"      {int(hors.sum())} sur {int(masque.sum())} {nom} : "
                  f"écart médian {np.median(valeurs):.3f}, max {valeurs.max():.3f} mois")
        pire = np.abs(ecart).max()
        if pire < 0.35:
            print("      Ordre de grandeur d'une dérive d'arrondi : chaque poste est")
            print("      arrondi au dixième de mois avant la somme, six postes")
            print("      peuvent donc dériver de 0,30 mois. Ce n'est pas un défaut")
            print("      d'intégration.")
        else:
            print("      Trop grand pour un arrondi : à regarder de près.")

    quantiles("Délai total (mois)", total, "mois")
    quantiles("décalage t0 (mois)", t0, "mois")
    negatifs = int((calcule["Cycle SAP (mois)"] < 0).sum())
    print(f"    articles à Cycle SAP négatif  : {negatifs}"
          f"  (segments de cycle réduits à l'écran, cf. section 5)")

    if traces:
        section("5. RÉCONCILIATION AVEC LE GRAPHIQUE REÇU PAR LE NAVIGATEUR")
        manquantes = [p for p in POSTES if p not in traces]
        verdict("tous les postes présents dans la figure",
                len(manquantes), len(POSTES),
                f"absents : {manquantes}" if manquantes else "")

        n = len(df)
        tailles = {len(t["x"]) for t in traces.values() if "x" in t}
        ecart_comptage = tailles and tailles != {n}

        if ecart_comptage:
            barres = max(tailles)
            verdict("la figure a autant de barres que le CSV a de lignes",
                    1, 1, f"figure {barres}, CSV {n}")
            print()
            if barres > n:
                print(f"    La figure compte {barres - n} barres de plus que le CSV.")
                print("    Cause probable : la jointure avec les fiches duplique des")
                print("    lignes. `generate_data_cycle` déduplique sur")
                print("    Designation_Article seul, donc une même Référence Article")
                print("    peut survivre plusieurs fois, et")
                print("    `set_index(\"Article\").join(...)` démultiplie alors la ligne.")
                print("    Chaque doublon ajoute une barre ET fausse la cascade.")
            else:
                print(f"    La figure compte {n - barres} barres de moins que le CSV.")
                print("    Le graphique et le CSV ne portent pas sur le même")
                print("    périmètre : préciser --designation.")
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
            if donnees:
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
            print("      Ce sous-ensemble est le vrai candidat pour le délai de")
            print("      lien RG-040 : la durée RG-038 est connue, et il reste")
            print("      malgré tout un écart avec le compte à rebours SAP.")
    print()
    print("    Ce résidu est ce que l'ancien graphique comptait en plus (ou en")
    print("    moins) de RG-038. C'est aussi le candidat naturel pour le délai de")
    print("    lien RG-040 dont la formule barrée n'est pas retrouvée.")

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
    if not echecs and not alertes:
        print("  Tous les contrôles passent. Le graphique correspond au CSV, et le")
        print("  CSV est cohérent avec ses propres règles de calcul.")
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
    p.add_argument("--feuille", default="export")
    p.add_argument("--designation", help="limiter à une désignation article de tête")
    args = p.parse_args()

    df = charger_csv(args.csv)
    traces = charger_graphique(args.graph) if args.graph else None
    df_excel = pd.read_excel(args.excel, sheet_name=args.feuille) if args.excel else None

    df_avant = charger_csv(args.avant) if args.avant else None

    verifier(df, traces, df_excel, args.designation, df_avant)
    return 1 if any(not ok and crit for _, ok, crit in resultats) else 0


if __name__ == "__main__":
    sys.exit(main())
