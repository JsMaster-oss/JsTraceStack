# -*- coding: utf-8 -*-
"""Contrôle statique des index du survol dans les routes de tracé.

Aucune donnée, aucune exécution : le script lit le fichier des routes et vérifie
que chaque `%{customdata[N]}` pointe bien la colonne que son libellé annonce.

Un index décalé d'un cran — parce qu'une entrée a été insérée au mauvais endroit
dans `liste_hover_template_analyse` — donne un survol qui affiche des chiffres
justes mais mal étiquetés. C'est indétectable à l'œil et ça ne lève aucune
erreur.

    python3 verifier_hovertemplate.py routes_graphique.py
"""

import ast
import re
import sys
import unicodedata


def normaliser(texte):
    sans_accent = unicodedata.normalize("NFKD", str(texte))
    sans_accent = "".join(c for c in sans_accent if not unicodedata.combining(c))
    return "".join(c for c in sans_accent.lower() if c.isalnum())


# libellé du survol -> colonne attendue, quand les deux ne sont pas identiques
SYNONYMES = {
    "designationarticle": "designation",
    "referencearticle": "article",
    "datededernieremiseajour": "datedelavaliditeducyclecontractuelequipementiers",
    "risquemajorant": "risquemajorantmois",
    "sommedesretardsdemontres": "sommedesretardsdemontresmois",
    "cyclesapvsretarddemontre": "cyclesapmois",
    "approslongslli": "approslongsllimois",
    "autresapprosousemifinis": "autresapprosousemifinismois",
    "cycleindustrielfournisseur": "cycleindustrielmois",
    "delaidesecurite": "delaisecumois",
    "tempsdereception": "tmpsrecepmois",
    "decalagedet0": "datedebutt0mois",
    "delaisapnonanalyse": "delaissapnonanalysemois",
    "delaitotal": "delaitotalmois",
    "margeappro": "margeappromois",
    "fournisseur": "fournisseur",
}


def routes(source):
    """Découpe le fichier par route de tracé."""
    bornes = [(m.group(1), m.start()) for m in re.finditer(r"\ndef (\w+)\(\):", source)]
    bornes.append(("<fin>", len(source)))
    for (nom, debut), (_, fin) in zip(bornes, bornes[1:]):
        bloc = source[debut:fin]
        if "liste_hover_template_analyse" in bloc and "customdata" in bloc:
            yield nom, bloc


def colonnes_du_survol(bloc):
    m = re.search(r"liste_hover_template_analyse = (\[.*?\n    \])", bloc, re.S)
    if not m:
        return None
    return ast.literal_eval(m.group(1))


def liste_module(source, nom):
    m = re.search(nom + r" = (\[.*?\n\])", source, re.S)
    return ast.literal_eval(m.group(1)) if m else None


def dico_module(source, nom):
    m = re.search(nom + r" = (\{.*?\n\})", source, re.S)
    return ast.literal_eval(m.group(1)) if m else None


def coherence_des_postes(source):
    """Un poste doit exister aux quatre endroits, sinon il disparaît en silence.

    C'est le piège principal : un poste présent dans le survol mais absent de
    LISTE_COL_INTERET_ANALYSE n'est pas tracé — et surtout il ne compte pas dans
    le Délai total, puisque c'est cette liste que somme calculer_delai_total_et_t0.
    Le survol annonce alors un total qui ne correspond à rien de dessiné.

    MODIF GRAPH-13 : une seule colonne échappe à cette règle, « Marge appro
    (mois) ». Elle est dans le survol, elle n'est pas tracée, et elle compte
    quand même — par le décalage t0 et non par la somme des postes, parce que
    c'est une position dans le temps et non une quantité de travail. Elle est
    donc citée en exception aux deux endroits ci-dessous.
    """
    print()
    print("=" * 78)
    print("COHÉRENCE DES POSTES")
    print("=" * 78)

    postes = liste_module(source, "LISTE_COL_INTERET_ANALYSE")
    couleurs = dico_module(source, "DICO_COLOR")
    if postes is None or couleurs is None:
        print("  LISTE_COL_INTERET_ANALYSE ou DICO_COLOR introuvable")
        return 1

    anomalies = 0
    print("  LISTE_COL_INTERET_ANALYSE ({} postes) :".format(len(postes)))
    for poste in postes:
        print("    {}".format(poste))

    sans_couleur = [p for p in postes if p not in couleurs]
    if sans_couleur:
        print("\n  ECHEC  postes sans couleur (KeyError au tracé) : {}".format(
            sans_couleur))
        anomalies += len(sans_couleur)

    # colonnes calculées qui ressemblent à un poste mais ne sont pas tracées
    calculees = set(re.findall(r'df_power_bi\["([^"]+ \(mois\))"\]\s*=', source))
    calculees = {c for c in calculees if not c.endswith(" raw")}
    orphelines = sorted(c for c in calculees
                        if c not in postes and c not in
                        ("Cycle Industriel Optimal (mois)",
                         "Complément au cycle industriel (mois)",
                         # MODIF GRAPH-13 : "Marge appro (mois)" ajoutée aux
                         # exceptions. La règle de ce contrôle — une colonne
                         # absente de LISTE_COL_INTERET_ANALYSE ne compte pas
                         # dans le Délai total — cesse d'être vraie pour elle :
                         # elle y entre par le décalage t0, pas par la somme
                         # des postes. Ne pas la remettre dans la liste tracée,
                         # c'est le bug que GRAPH-13 corrige.
                         "Marge appro (mois)",
                         "Appros Longs (mois)", "Delta SAP (mois)"))
    if orphelines:
        print("\n  ECHEC  calculées mais absentes de LISTE_COL_INTERET_ANALYSE :")
        for c in orphelines:
            print("           {}".format(c))
        print("         Elles ne sont ni tracées ni comptées dans le Délai total.")
        anomalies += len(orphelines)

    # postes présents dans le survol mais pas tracés
    for nom, bloc in routes(source):
        survol = colonnes_du_survol(bloc) or []
        manquants = [c for c in survol
                     if c.endswith("(mois)") and c not in postes
                     # MODIF GRAPH-13 : "Marge appro (mois)" exclue, comme le
                     # Délai total. Les deux sont dans le survol sans être
                     # tracés, et les deux comptent bien dans le total.
                     and c not in ("Délai total (mois)", "Marge appro (mois)")]
        if manquants:
            print("\n  ECHEC  [{}] dans le survol mais pas tracés : {}".format(
                nom, manquants))
            anomalies += len(manquants)

    if not anomalies:
        print("\n  OK     chaque poste est tracé, coloré et compté dans le total.")
    return anomalies


def main():
    chemin = sys.argv[1] if len(sys.argv) > 1 else "routes_graphique.py"
    source = open(chemin, encoding="utf-8").read()

    anomalies = coherence_des_postes(source)
    for nom, bloc in routes(source):
        colonnes = colonnes_du_survol(bloc)
        print()
        print("=" * 78)
        print("{}  —  {} colonnes dans liste_hover_template_analyse".format(
            nom, len(colonnes) if colonnes else 0))
        print("=" * 78)
        if not colonnes:
            print("  liste introuvable")
            anomalies += 1
            continue

        if colonnes[-1] != "Délai total (mois)":
            print("  ALERTE  la dernière entrée est « {} » et non "
                  "« Délai total (mois) ».".format(colonnes[-1]))
            print("          Plusieurs contrôles lisent le total par l'index -1.")
            anomalies += 1

        # chaque ligne de survol : un libellé, puis un ou plusieurs index
        for ligne in re.findall(r'hovertemplate \+= (".*?")', bloc):
            texte = ast.literal_eval(ligne)
            index = [int(i) for i in re.findall(r"%\{customdata\[(\d+)\]\}", texte)]
            if not index:
                continue
            libelle = re.sub(r"<[^>]+>|&#\d+;", "", texte)
            libelle = re.sub(r"%\{customdata\[\d+\]\}", "", libelle)
            libelle = libelle.strip(" :<>-").split(":")[0].strip()

            premier = index[0]
            if premier >= len(colonnes):
                print("  ECHEC   [{}] index {} hors liste (max {})".format(
                    libelle, premier, len(colonnes) - 1))
                anomalies += 1
                continue

            cible = colonnes[premier]
            attendu = SYNONYMES.get(normaliser(libelle), normaliser(libelle))
            ok = attendu in normaliser(cible) or normaliser(cible) in attendu

            marque = "OK     " if ok else "ECHEC  "
            if not ok:
                anomalies += 1
            print("  {} {:<34s} [{:>2d}] -> {}".format(
                marque, libelle[:34], premier, cible))
            if not ok:
                bons = [i for i, c in enumerate(colonnes)
                        if attendu in normaliser(c) or normaliser(c) in attendu]
                if bons:
                    print("           l'index attendu serait {} ({})".format(
                        bons[0], colonnes[bons[0]]))

    print()
    print("=" * 78)
    print("{} anomalie(s)".format(anomalies) if anomalies
          else "Tous les index du survol pointent la bonne colonne.")
    return 1 if anomalies else 0


if __name__ == "__main__":
    sys.exit(main())
