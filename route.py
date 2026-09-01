# -*- coding: utf-8 -*-

import json
from bisect import bisect_left  # MODIF GRAPH-4 : ajouté, requis par la cascade

import numpy as np  # MODIF GRAPH-4 : ajouté, requis par la cascade
import pandas as pd
import plotly
import plotly.graph_objects as go
from flask import jsonify, redirect, request, url_for
from flask_login import login_required


# MODIF GRAPH-5 : bloc ajouté. Les deux listes vivaient auparavant dans chaque
# route sous les noms liste_col_interet_analyse et dico_color, avec un ordre
# d'empilement et une couleur qui divergeaient entre les deux.
#
# Postes empilés dans la barre, dans l'ordre d'empilement.
#
# Cette liste sert à la fois au tracé et au calcul du Délai total : tout ce qui
# est dessiné est compté, et réciproquement. Les deux routes la partagent —
# c'est leur divergence qui faisait changer l'ordre des segments et les totaux
# au premier clic de légende.
#
# "date début t0 (mois)" y figure comme poste dessiné ; calculer_delai_total_et_t0
# l'écarte de la somme puisqu'elle le recalcule depuis le parent.
LISTE_COL_INTERET_ANALYSE = [
    "date début t0 (mois)",
    "Tmps recep (mois)",
    "Délai sécu (mois)",
    # MODIF GRAPH-13 : "Marge appro (mois)" retirée d'ici. Elle vaut
    # -MargeAppr/20 : une marge positive donnait un segment négatif, que Plotly
    # n'empile pas et qu'ajuster_pour_affichage faisait absorber par le cycle de
    # l'article. La marge est une position dans le temps, pas une quantité de
    # travail : elle est passée dans le décalage t0, cf. MODIF GRAPH-13b.
    # La colonne reste calculée et reste dans le survol, seul son tracé part.
    "Cycle Industriel (mois)",
    "Autres, Appros ou Semi-Finis (mois)",
    "Appros Longs LLI (mois)",
    "Somme des retards démontrés (mois)",
    "Cycle SAP (mois)",
    "Délais SAP non Analysé (mois)",
    "Risque majorant (mois)",
]

# MODIF GRAPH-10 : le poste tracé « Cycle SAP » vaut en réalité
# `Cycle SAP - ZPIF - ZO1 - ZO2`. Négatif, il signifie que ces trois postes
# dépassent le cycle SAP réel : ce sont eux qui absorbent le dépassement à
# l'écran, cf. ajuster_pour_affichage.
# MODIF GRAPH-13 : GROUPE_LIEN supprimée. Elle listait les deux postes qui
# absorbaient une marge négative ; la marge n'étant plus tracée, elle n'avait
# plus d'usage.

GROUPE_CYCLE_SAP = [
    "Cycle Industriel (mois)",
    "Autres, Appros ou Semi-Finis (mois)",
    "Appros Longs LLI (mois)",
]

DICO_COLOR = {
    "date début t0 (mois)": "#C8DCE7",
    "Tmps recep (mois)": "#E4DD5D",
    "Délai sécu (mois)": "#CFC841",
    # MODIF GRAPH-13 : "Marge appro (mois)": "#B5A83A" retirée, le poste
    # n'est plus tracé.
    "Délais SAP non Analysé (mois)": "#7142C6",
    "Cycle Industriel (mois)": "#073B4C",
    "Autres, Appros ou Semi-Finis (mois)": "#118AB2",
    "Appros Longs LLI (mois)": "#06D6A0",
    "Somme des retards démontrés (mois)": "#EF476F",
    "Cycle SAP (mois)": "#F78C6B",
    "Risque majorant (mois)": "#FFD166",
}


# Création graphique cycles en cascade cyclée

@main.route("/create_graph_analyse_CCC2", methods=["POST"])
@login_required
@roles_required("Admin", "Writer", "Reader")
def create_graph_analyse_CCC2():

    req = request.get_json()
    b_ordonner = req["b_ordonner"]
    designation_article = req["designation_article"]

    # Stockage des données sur un DF
    df_power_bi = get_donnee_power_bi(config)
    df_cycle_detail = generate_data_cycle(config)

    # Filtrage du DF sur la designation article sélectionnée
    df_power_bi = df_power_bi[
        df_power_bi["désignation article de tête"] == designation_article
    ].copy()  # MODIF GRAPH-8 : .copy() ajouté, les affectations portaient sur une vue

    df_power_bi["Tmps recep (mois)"] = df_power_bi["Tps_Recep"].apply(
        lambda x: round(x / 20, 2)
    )
    df_power_bi["Délai sécu (mois)"] = df_power_bi["Délai_Sécu"].apply(
        lambda x: round(x / 20, 1)
    )
    # MODIF GRAPH-3 : la colonne "Delta SAP (mois) raw" est supprimée et
    # l'écrêtage `0 if x < 0 else ...` est retiré. update_graph n'écrêtait pas :
    # le premier clic de légende changeait tous les totaux.
    #
    # Delta SAP entre dans le Délai total à sa valeur réelle, négatifs compris.
    # L'écrêtage n'a lieu que pour le tracé, via df_analyse_positive : une barre
    # empilée ne sait pas dessiner un segment négatif.
    df_power_bi["Delta SAP (mois)"] = df_power_bi["Delta SAP"].apply(
        lambda x: round(x / 20, 1)
    )
    # MODIF GRAPH-12 : RG-040. Le délai du lien vaut
    # Délai_Sécu + Tps_Recep - MargeAppr, et zéro dès que Cyc_Cum = 0.
    #
    # Le signe est inversé sur la marge pour qu'elle s'empile dans le même sens
    # que les deux autres : une marge négative — le cas courant — allonge le
    # lien et donne donc un segment positif.
    if "MargeAppr" in df_power_bi.columns:
        _marge = pd.to_numeric(df_power_bi["MargeAppr"], errors="coerce").fillna(0)
    else:
        # CSV antérieur à MODIF GEN-5 : la colonne n'y est pas encore. On
        # dégrade en marge nulle plutôt que de faire tomber la route, ce qui
        # revient au comportement d'avant RG-040. Régénérer le CSV rétablit le
        # terme ; verification.py signale la colonne manquante en section 1.
        _marge = pd.Series(0.0, index=df_power_bi.index)

    df_power_bi["Marge appro (mois)"] = _marge.apply(lambda x: round(-x / 20, 2))
    lien_nul = pd.to_numeric(df_power_bi["Cyc_Cum"], errors="coerce").fillna(0) == 0
    for _colonne in ("Tmps recep (mois)", "Délai sécu (mois)", "Marge appro (mois)"):
        df_power_bi.loc[lien_nul, _colonne] = 0.0

    df_power_bi["Cycle Industriel Optimal (mois)"] = df_power_bi["ZPIF"].apply(
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Complément au cycle industriel (mois)"] = df_power_bi["ZO2"].apply(  # MODIF GRAPH-1 : lisait "202"
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Appros Longs (mois)"] = df_power_bi["ZO1"].apply(  # MODIF GRAPH-1 : lisait "201"
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Somme des retards démontrés (mois)"] = df_power_bi["Démontré"].apply(
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Risque majorant (mois)"] = df_power_bi["Risques"].apply(
        lambda x: round(x / 20, 1)
    )

    # Renommage des colonnes pour affichage correct sur le graphique
    df_power_bi = df_power_bi.rename(
        columns={"Délais non analysé (mois)": "Délais SAP non Analysé (mois)"}
    )
    df_power_bi = df_power_bi.rename(
        columns={"Appros Longs (mois)": "Appros Longs LLI (mois)"}
    )
    df_power_bi = df_power_bi.rename(
        columns={
            "Complément au cycle industriel (mois)": "Autres, Appros ou Semi-Finis (mois)"
        }
    )
    # MODIF GRAPH-2 : renommage ajouté. Il manquait ici alors que les deux
    # listes de tracé utilisent le nom court : la route levait une KeyError.
    df_power_bi = df_power_bi.rename(
        columns={"Cycle Industriel Optimal (mois)": "Cycle Industriel (mois)"}
    )
    df_power_bi = df_power_bi.rename(columns={"Delta SAP (mois)": "Cycle SAP (mois)"})

    df_power_bi = (
        df_power_bi.set_index("Article")
        .join(
            df_cycle_detail[
                [
                    "Référence Article",
                    "Fournisseur",
                    "Date de la validité du cycle contractuel équipementiers",
                    "Cycle Contractuel Equipementiers (en mois)",
                    "Mots clefs Risque",
                    "Mots Clefs Retard Démontré",
                    "Mots clefs expliquant le delta",
                    "Mots Clefs Appros Longs",
                    "Mots Clefs Complément au cycle industriel",
                    "Mots Clefs Cycle Industriel Optimal",
                ]
            # MODIF GRAPH-11 : une même Référence Article peut apparaître
            # plusieurs fois dans les fiches — generate_data_cycle ne
            # déduplique que sur Designation_Article. Chaque doublon
            # démultipliait la ligne au join : autant de barres en trop sur
            # le graphique, et une cascade faussée puisque plusieurs barres
            # portaient le même article.
            #
            # keep="first" pour rester aligné sur generateData.py, qui prend la
            # première fiche (.iloc[0]) pour calculer les six composantes : le
            # survol doit décrire la même fiche que les chiffres.
            ].drop_duplicates(subset=["Référence Article"], keep="first")
            .set_index("Référence Article")
        )
        .reset_index()
        .rename(columns={"index": "Article"})
        .sort_values(by=["ID"])
    )

    attributRef = "Designation bis"

    df_power_bi["Designation bis"] = df_power_bi["Designation"]

    identifier = (
        df_power_bi[[attributRef]].groupby(by=attributRef).transform("cumcount")
    )

    df_power_bi[attributRef] = df_power_bi[attributRef].astype("string") + (
        identifier.astype("string")
    ).replace("0", "")

    # MODIF GRAPH-5 : liste_col_interet_analyse et dico_color supprimées d'ici,
    # remontées en LISTE_COL_INTERET_ANALYSE et DICO_COLOR au niveau module.

    # Définition de la liste en hover global (analysé et non analysé)
    liste_hover_template_analyse = [
        "Designation",
        "Article",
        "article parent",
        "Fournisseur",
        "Date de la validité du cycle contractuel équipementiers",
        "Cycle Contractuel Equipementiers (en mois)",
        "Risque majorant (mois)",
        "Somme des retards démontrés (mois)",
        "Cycle SAP (mois)",
        "Appros Longs LLI (mois)",
        "Autres, Appros ou Semi-Finis (mois)",
        "Cycle Industriel (mois)",
        "Délai sécu (mois)",
        "Tmps recep (mois)",
        "date début t0 (mois)",
        "Mots clefs Risque",
        "Mots Clefs Retard Démontré",
        "Mots clefs expliquant le delta",
        "Mots Clefs Appros Longs",
        "Mots Clefs Complément au cycle industriel",
        "Mots Clefs Cycle Industriel Optimal",
        "Délais SAP non Analysé (mois)",
        # MODIF GRAPH-9 : "Delta SAP (mois) raw" retirée d'ici, elle était
        # devenue un doublon exact de "Cycle SAP (mois)" (cf. MODIF GRAPH-3).
        "Marge appro (mois)",   # MODIF GRAPH-12, index 22
        # "Délai total (mois)" reste en DERNIÈRE position : plusieurs contrôles
        # le lisent par l'index -1 du customdata.
        "Délai total (mois)",
    ]

    # On a maintenant le df power bi
    df_analyse = df_power_bi

    hovertemplates = []

    # Construction dynamique du hovertemplate selon si l'article est analysé ou non en détectant les lignes qui sont vides
    for _, row in df_analyse.iterrows():

        hovertemplate = "<br>Désignation Article: %{customdata[0]}"

        if not isinstance(
            row["Date de la validité du cycle contractuel équipementiers"], float
        ) and not pd.isna(
            row["Date de la validité du cycle contractuel équipementiers"]
        ):
            hovertemplate += "<br>Date de dernière mise à jour: %{customdata[4]}"

        hovertemplate += "<br>Référence Article: %{customdata[1]}"

        if not isinstance(row["Fournisseur"], float) and not pd.isna(
            row["Fournisseur"]
        ):
            hovertemplate += "<br>Fournisseur: %{customdata[3]}"

        if not isinstance(row["Mots clefs Risque"], float) and not pd.isna(
            row["Mots clefs Risque"]
        ):
            hovertemplate += "<br><span style='color: #FFD166;'>&#11044;</span> Risque majorant: %{customdata[6]}: %{customdata[15]}"

        if not isinstance(row["Mots Clefs Retard Démontré"], float) and not pd.isna(
            row["Mots Clefs Retard Démontré"]
        ):
            hovertemplate += "<br><span style='color: #EF476F;'>&#11044;</span> Somme des retards démontrés: %{customdata[7]}: %{customdata[16]}"

        if not isinstance(row["Mots clefs expliquant le delta"], float) and not pd.isna(
            row["Mots clefs expliquant le delta"]
        ):
            hovertemplate += "<br><span style='color: #F78C6B;'>&#11044;</span> Cycle SAP vs retard démontré: %{customdata[8]}: %{customdata[17]}"  # MODIF GRAPH-9 : [23] -> [8]

        if not isinstance(row["Mots Clefs Appros Longs"], float) and not pd.isna(
            row["Mots Clefs Appros Longs"]
        ):
            hovertemplate += "<br><span style='color: #06D6AC;'>&#11044;</span> Appros Longs LLI: %{customdata[9]}: %{customdata[18]}"

        if not isinstance(
            row["Mots Clefs Complément au cycle industriel"], float
        ) and not pd.isna(row["Mots Clefs Complément au cycle industriel"]):
            hovertemplate += "<br><span style='color: #118AB2;'>&#11044;</span> Autres, Appros ou Semi-Finis: %{customdata[10]}: %{customdata[19]}"

        if not isinstance(
            row["Mots Clefs Cycle Industriel Optimal"], float
        ) and not pd.isna(row["Mots Clefs Cycle Industriel Optimal"]):
            hovertemplate += "<br><span style='color: #073B4C;'>&#11044;</span> Cycle Industriel fournisseur: %{customdata[11]}: %{customdata[20]}"

        hovertemplate += "<br><span style='color: #CFC841;'>&#11044;</span> Délai de sécurité: %{customdata[12]}"
        hovertemplate += "<br><span style='color: #E4DD5D;'>&#11044;</span> Temps de Réception: %{customdata[13]}"
        # MODIF GRAPH-12
        hovertemplate += "<br><span style='color: #B5A83A;'>&#11044;</span> Marge appro: %{customdata[22]}"
        hovertemplate += "<br><span style='color: #98d2eb;'>&#11044;</span> Décalage de t0: %{customdata[14]}"

        if isinstance(row["Mots Clefs Cycle Industriel Optimal"], float) and pd.isna(
            row["Mots Clefs Cycle Industriel Optimal"]
        ):
            hovertemplate += "<br><span style='color: #7142c6;'>&#11044;</span> Délai SAP non analysé: %{customdata[21]}"

        hovertemplate += "<br><span style='color: white;'>&#11044;</span> Délai total: %{customdata[23]}"

        # MODIF GRAPH-10 : prévenir que les segments dessinés sont plus courts
        # que les valeurs ci-dessus, sinon l'écart passe pour une erreur.
        if row["Cycle SAP (mois)"] < 0:
            hovertemplate += (
                "<br><i>Cycle SAP négatif : les segments de cycle sont réduits "
                "à l'écran pour que la barre garde la bonne longueur.</i>"
            )

        hovertemplate += "<extra></extra>"

        hovertemplates.append(hovertemplate)

    df_analyse["hovertemplate"] = hovertemplates

    # MODIF GRAPH-6 : la liste `colonnes` qui précédait ici est supprimée, elle
    # faisait doublon avec LISTE_COL_INTERET_ANALYSE à l'ordre près.
    # MODIF GRAPH-4 : corps de la fonction entièrement remplacé ; la fonction
    # elle-même reste imbriquée dans la route. Voir MODIFICATIONS.md.
    def calculer_delai_total_et_t0(df, colonnes):
        """Délai total et décalage t0 de chaque article de la désignation.

        Le cumul suit la profondeur dans l'arbre, pas l'ordre des lignes : un
        export où l'enfant précède son parent donnait sinon à l'enfant la durée
        *propre* du parent au lieu de son cumul, sans erreur ni avertissement.

        Le rattachement porte sur une position de ligne et non sur la seule
        référence article : un composant monté à plusieurs endroits de la
        nomenclature a autant de t0 que de montages, et un rapprochement par
        référence renverrait toujours le premier. On retient la dernière
        occurrence du parent située *avant* la ligne — convention de la
        nomenclature indentée, où le parent précède ses composants — et on se
        rabat sur une occurrence suivante si aucune ne précède.

        `colonnes` est la liste des postes empilés dans la barre. La colonne t0
        y figure comme poste dessiné, elle est écartée de la somme puisqu'elle
        est recalculée ici depuis le parent.
        """
        df = df.copy()

        postes = [c for c in colonnes if c != "date début t0 (mois)"]
        duree_propre = df[postes].fillna(0).sum(axis=1).to_numpy(dtype=float)

        # Position de la ligne parente de chaque ligne, -1 pour une racine
        positions = {}
        for i, article in enumerate(df["Article"].to_numpy()):
            positions.setdefault(article, []).append(i)

        parent_pos = np.full(len(df), -1, dtype=int)
        for i, ref_parent in enumerate(df["article parent"].to_numpy()):
            if pd.isna(ref_parent):
                continue
            candidats = positions.get(ref_parent)
            if not candidats:
                continue
            rang = bisect_left(candidats, i)
            if rang > 0:
                parent_pos[i] = candidats[rang - 1]
            else:
                suivants = [c for c in candidats if c != i]
                if suivants:
                    parent_pos[i] = suivants[0]

        # Profondeur de chaque ligne, pour cumuler les parents avant les enfants
        profondeur = np.full(len(df), -1, dtype=int)
        for depart in range(len(df)):
            chaine, vus, courant = [], set(), depart
            while courant != -1 and profondeur[courant] == -1:
                if courant in vus:
                    raise ValueError(
                        "Boucle dans la relation article / article parent, "
                        "lignes {}".format(sorted(vus))
                    )
                vus.add(courant)
                chaine.append(courant)
                courant = parent_pos[courant]

            niveau = 0 if courant == -1 else profondeur[courant] + 1
            for ligne in reversed(chaine):
                profondeur[ligne] = niveau
                niveau += 1

        # MODIF GRAPH-13 : bloc ajouté. La marge appro n'est plus un poste
        # empilé, c'est un décalage du t0. Le poste vaut -MargeAppr/20, donc une
        # marge positive rapproche l'enfant de la livraison : il démarre pendant
        # le cycle de son parent, ce qu'attend le métier sur un F/30.
        #
        # Le Délai total est inchangé au centime près — c'est le même terme, il
        # change seulement de place dans l'addition — mais la barre dessinée
        # redevient égale à ce total, ce qui n'était plus le cas dès qu'une
        # marge positive dépassait Sécu + Recep.
        #
        # Une racine garde t0 = 0 et sa marge n'est pas comptée : RG-040 est un
        # délai de lien, un article de tête n'a pas de parent donc pas de lien.
        decalage = df["Marge appro (mois)"].fillna(0).to_numpy(dtype=float)

        t0 = np.zeros(len(df), dtype=float)
        total = np.zeros(len(df), dtype=float)

        for ligne in np.argsort(profondeur, kind="stable"):
            parent = parent_pos[ligne]
            # MODIF GRAPH-13 : « + decalage[ligne] » ajouté, la ligne valait
            # t0[ligne] = 0.0 if parent == -1 else total[parent]
            t0[ligne] = 0.0 if parent == -1 else total[parent] + decalage[ligne]
            total[ligne] = duree_propre[ligne] + t0[ligne]

        df["date début t0 (mois)"] = np.round(t0, 2)
        df["Délai total (mois)"] = np.round(total, 2)

        return df

    df_analyse = calculer_delai_total_et_t0(df_analyse, LISTE_COL_INTERET_ANALYSE)  # MODIF GRAPH-6 : passait `colonnes`


    # MODIF GRAPH-10 : remplace l'écrêtage `clip(lower=0)` — lui-même remplaçant
    # de `.applymap(lambda x: max(x, 0))`, supprimé de pandas 3. L'écrêtage
    # laissait la barre plus longue que le Délai total dès qu'un segment était
    # négatif : Plotly ignore un segment négatif dans une barre empilée, donc la
    # longueur manquait à l'appel sans que rien ne le signale.
    def ajuster_pour_affichage(df, colonnes):
        """Version du DataFrame destinée au tracé, sans segment négatif.

        On répartit les valeurs négatives sur les postes voisins au lieu de les
        écrêter, pour que la longueur de la barre reste égale au Délai total.

        Cas principal, le Cycle SAP. Il vaut `Cycle SAP - ZPIF - ZO1 - ZO2` :
        négatif, il veut dire que le cycle industriel et ses gains dépassent le
        cycle SAP réel. On réduit donc ces trois postes au prorata jusqu'à
        absorber le dépassement, et le segment Cycle SAP tombe à 0. Les trois
        postes réduits somment alors exactement au cycle SAP.

        Tout autre poste négatif est ramené à 0 et son montant retiré au prorata
        des postes encore positifs.

        Le décalage t0 est laissé hors de la répartition : c'est une position
        dans le temps, pas une quantité, et le réduire décalerait le début de la
        barre.

        Les valeurs réelles restent dans df_analyse : le survol continue de les
        afficher, c'est là qu'on lit la décomposition exacte.
        """
        affichage = df.copy()
        rang = {c: i for i, c in enumerate(colonnes)}
        postes = affichage[colonnes].to_numpy(dtype=float).copy()

        absorbables = [i for c, i in rang.items() if c != "date début t0 (mois)"]

        # 1. Cycle SAP négatif : absorbé par les trois postes dont il est le résidu
        i_sap = rang.get("Cycle SAP (mois)")
        groupe = [rang[c] for c in GROUPE_CYCLE_SAP if c in rang]
        if i_sap is not None and groupe:
            for ligne in np.where(postes[:, i_sap] < 0)[0]:
                deficit = -postes[ligne, i_sap]
                disponible = postes[ligne, groupe].clip(min=0)
                base = disponible.sum()
                absorbe = min(deficit, base)
                if base > 0:
                    postes[ligne, groupe] = disponible * (1.0 - absorbe / base)
                # Reliquat non absorbé : laissé négatif pour l'étape 3, sinon
                # la barre resterait trop longue de ce montant.
                postes[ligne, i_sap] = -(deficit - absorbe)

        # MODIF GRAPH-13 : l'étape « 2. Marge appro négative », qui répartissait
        # le segment de marge sur Tmps recep et Délai sécu, est supprimée. La
        # marge n'est plus un poste tracé (MODIF GRAPH-13a) : elle n'apparaît
        # plus dans `colonnes`, donc dans `rang`, et le bloc ne s'exécutait plus.
        # Supprimé plutôt que laissé : du code mort qui a l'air vivant.

        # 3. Filet de sécurité : tout négatif restant, au prorata du reste.
        #    Quand le lien est négatif au point de dépasser la durée de la
        #    tâche — une marge appro très supérieure à Sécu + Recep — la
        #    contribution de l'article est négative et la barre tombe à zéro :
        #    c'est le plus court qu'une barre puisse être. Le Délai total du
        #    survol, lui, reste inférieur au t0 et dit la vérité.
        for ligne in np.where((postes[:, absorbables] < 0).any(axis=1))[0]:
            valeurs = postes[ligne, absorbables]
            deficit = -valeurs[valeurs < 0].sum()
            valeurs = valeurs.clip(min=0)
            base = valeurs.sum()
            if base > 0:
                valeurs = valeurs * max(0.0, 1.0 - min(deficit, base) / base)
            postes[ligne, absorbables] = valeurs

        for colonne, i in rang.items():
            affichage[colonne] = np.round(np.maximum(postes[:, i], 0.0), 2)

        return affichage

    # Version du DataFrame destinée au tracé
    df_analyse_positive = ajuster_pour_affichage(
        df_analyse, LISTE_COL_INTERET_ANALYSE
    )

    fig = go.Figure()

    for col in LISTE_COL_INTERET_ANALYSE:  # MODIF GRAPH-5 : liste locale

        fig.add_trace(
            go.Bar(
                x=df_analyse_positive[col],
                y=df_analyse_positive[attributRef],
                texttemplate="%{x:.0f}",
                textposition="inside",
                textangle=0,
                textfont_color="white",
                orientation="h",
                customdata=df_analyse[liste_hover_template_analyse].fillna("").values,
                hovertemplate=df_analyse["hovertemplate"],
                marker_color=DICO_COLOR[col],  # MODIF GRAPH-5 : dico local
                name=col,
            )
        )

    fig.update_layout(barmode="stack", title_text="Relative Barmode")

    fig.update_layout(
        yaxis_title="Article",
        xaxis_title="Mois",
        title="Cascade cyclée par rapport à t0 (en mois)",
        height=700,
    )

    fig.update_layout(template="plotly_white", height=700)
    fig.update_yaxes(side="right")
    fig.update_xaxes(autorange="reversed")

    fig.update_layout(
        legend=dict(
            orientation="h",
            yanchor="top",
            xanchor="left",
            y=-0.1,
            x=-0.1,
            bgcolor="rgba(0,0,0,0)",
            entrywidth=0,
        )
    )

    fig.update_layout(
        hoverlabel=dict(
            bgcolor="rgba(0,0,0,1)",
            font_size=10,
        )
    )

    if b_ordonner:
        fig.update_yaxes(categoryorder="total ascending")
    else:
        fig.update_layout(
            yaxis={
                "categoryorder": "array",
                "categoryarray": df_analyse[attributRef].tolist()[::-1],
            }
        )

    data = {"graph": json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)}

    log_action(
        action="Consultation",
        menu="Visualisation",
        detail="Visualisation en cascade cyclée de : " + str(designation_article),
    )

    return jsonify(data)


@main.route("/update_info_cascade", methods=["POST", "GET"])
@login_required
@roles_required("Admin", "Writer", "Reader")
def update_info_cascade():

    df_power_bi = generate_data_power_bi(config)

    if df_power_bi is not None:
        stocker_donnee_power_bi(config, df_power_bi)

    data = {"MSG": "Ok"}

    return jsonify(data)


@main.route("/update_info_cascade_index", methods=["POST", "GET"])
@login_required
@roles_required("Admin", "Writer", "Reader")
def update_info_cascade_index():

    df_power_bi = generate_data_power_bi(config)

    if df_power_bi is not None:
        stocker_donnee_power_bi(config, df_power_bi)

    return redirect(url_for("main.index_fiche"))


# Route intermédiaire qui sert à rendre dynamique le clic de la légende en retraçant le graphique
@main.route("/update_graph", methods=["POST"])
# MODIF GRAPH-9 : décorateurs inversés, @roles_required précédait @login_required
@login_required
@roles_required("Admin", "Writer", "Reader")
def update_graph():

    req = request.get_json()

    b_ordonner = req["server_data"]["b_ordonner"]
    designation_article = req["server_data"]["designation_article"]

    # Récupération du statut de chaque trace
    traceStatus = req["traceStatut"]

    # Récupération du nom de la trace cliquée
    clickedTrace = req["clickedTraceName"]

    df_power_bi = get_donnee_power_bi(config)
    df_cycle_detail = generate_data_cycle(config)

    df_power_bi = df_power_bi[
        df_power_bi["désignation article de tête"] == designation_article
    ].copy()  # MODIF GRAPH-8 : .copy() ajouté, les affectations portaient sur une vue

    df_power_bi["Article Article père"] = (
        df_power_bi["Article"] + "-" + df_power_bi["article parent"]
    )

    df_power_bi["Tmps recep (mois) raw"] = df_power_bi["Tps_Recep"].apply(
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Délai sécu (mois) raw"] = df_power_bi["Délai_Sécu"].apply(
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Delta SAP (mois) raw"] = df_power_bi["Delta SAP"].apply(
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Tmps recep (mois)"] = df_power_bi["Tps_Recep"].apply(
        lambda x: round(x / 20, 2)
    )
    df_power_bi["Délai sécu (mois)"] = df_power_bi["Délai_Sécu"].apply(
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Delta SAP (mois)"] = df_power_bi["Delta SAP"].apply(
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Cycle Industriel Optimal (mois) raw"] = df_power_bi["ZPIF"].apply(
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Complément au cycle industriel (mois) raw"] = df_power_bi["ZO2"].apply(  # MODIF GRAPH-1 : lisait "202"
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Appros Longs (mois) raw"] = df_power_bi["ZO1"].apply(  # MODIF GRAPH-1 : lisait "201"
        lambda x: round(x / 20, 1)
    )
    # MODIF GRAPH-12 : RG-040. Le délai du lien vaut
    # Délai_Sécu + Tps_Recep - MargeAppr, et zéro dès que Cyc_Cum = 0.
    #
    # Le signe est inversé sur la marge pour qu'elle s'empile dans le même sens
    # que les deux autres : une marge négative — le cas courant — allonge le
    # lien et donne donc un segment positif.
    if "MargeAppr" in df_power_bi.columns:
        _marge = pd.to_numeric(df_power_bi["MargeAppr"], errors="coerce").fillna(0)
    else:
        # CSV antérieur à MODIF GEN-5 : la colonne n'y est pas encore. On
        # dégrade en marge nulle plutôt que de faire tomber la route, ce qui
        # revient au comportement d'avant RG-040. Régénérer le CSV rétablit le
        # terme ; verification.py signale la colonne manquante en section 1.
        _marge = pd.Series(0.0, index=df_power_bi.index)

    df_power_bi["Marge appro (mois)"] = _marge.apply(lambda x: round(-x / 20, 2))
    lien_nul = pd.to_numeric(df_power_bi["Cyc_Cum"], errors="coerce").fillna(0) == 0
    for _colonne in ("Tmps recep (mois)", "Délai sécu (mois)", "Marge appro (mois)"):
        df_power_bi.loc[lien_nul, _colonne] = 0.0

    df_power_bi["Cycle Industriel Optimal (mois)"] = df_power_bi["ZPIF"].apply(
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Complément au cycle industriel (mois)"] = df_power_bi["ZO2"].apply(  # MODIF GRAPH-1 : lisait "202"
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Appros Longs (mois)"] = df_power_bi["ZO1"].apply(  # MODIF GRAPH-1 : lisait "201"
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Démontré (mois) raw"] = df_power_bi["Démontré"].apply(
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Risques (mois) raw"] = df_power_bi["Risques"].apply(
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Somme des retards démontrés (mois)"] = df_power_bi["Démontré"].apply(
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Risque majorant (mois)"] = df_power_bi["Risques"].apply(
        lambda x: round(x / 20, 1)
    )
    df_power_bi["Délais non analysé (mois) raw"] = df_power_bi[
        "Délais non analysé (mois)"
    ].apply(lambda x: round(x, 1))

    df_power_bi = df_power_bi.rename(
        columns={"Délais non analysé (mois)": "Délais SAP non Analysé (mois)"}
    )
    df_power_bi = df_power_bi.rename(
        columns={"Appros Longs (mois)": "Appros Longs LLI (mois)"}
    )
    df_power_bi = df_power_bi.rename(
        columns={
            "Complément au cycle industriel (mois)": "Autres, Appros ou Semi-Finis (mois)"
        }
    )
    df_power_bi = df_power_bi.rename(
        columns={"Cycle Industriel Optimal (mois)": "Cycle Industriel (mois)"}
    )
    df_power_bi = df_power_bi.rename(columns={"Delta SAP (mois)": "Cycle SAP (mois)"})

    df_power_bi = (
        df_power_bi.set_index("Article")
        .join(
            df_cycle_detail[
                [
                    "Référence Article",
                    "Fournisseur",
                    "Cycle Contractuel Equipementiers (en mois)",
                    "Mots clefs Risque",
                    "Mots Clefs Retard Démontré",
                    "Mots clefs expliquant le delta",
                    "Mots Clefs Appros Longs",
                    "Mots Clefs Complément au cycle industriel",
                    "Mots Clefs Cycle Industriel Optimal",
                ]
            # MODIF GRAPH-11 : une même Référence Article peut apparaître
            # plusieurs fois dans les fiches — generate_data_cycle ne
            # déduplique que sur Designation_Article. Chaque doublon
            # démultipliait la ligne au join : autant de barres en trop sur
            # le graphique, et une cascade faussée puisque plusieurs barres
            # portaient le même article.
            #
            # keep="first" pour rester aligné sur generateData.py, qui prend la
            # première fiche (.iloc[0]) pour calculer les six composantes : le
            # survol doit décrire la même fiche que les chiffres.
            ].drop_duplicates(subset=["Référence Article"], keep="first")
            .set_index("Référence Article")
        )
        .reset_index()
        .rename(columns={"index": "Article"})
        .sort_values(by=["ID"])
    )

    attributRef = "Designation bis"

    df_power_bi["Designation bis"] = df_power_bi["Designation"]

    identifier = (
        df_power_bi[[attributRef]].groupby(by=attributRef).transform("cumcount")
    )

    # MODIF GRAPH-7 : le `"-" +` qui préfixait identifier est retiré. Avec lui,
    # .replace("0", "") ne remplaçait rien — les valeurs étaient "-0", "-1" — et
    # le premier exemplaire s'appelait NOM-0 au lieu de NOM.
    df_power_bi[attributRef] = df_power_bi[attributRef].astype("string") + (
        identifier.astype("string")
    ).replace("0", "")

    # MODIF GRAPH-5 : liste_col_interet_analyse et dico_color supprimées d'ici,
    # remontées en LISTE_COL_INTERET_ANALYSE et DICO_COLOR au niveau module.

    liste_hover_template_analyse = [
        "Designation",
        "Article",
        "article parent",
        "Fournisseur",
        "Cycle Contractuel Equipementiers (en mois)",
        "Risque majorant (mois)",
        "Somme des retards démontrés (mois)",
        "Cycle SAP (mois)",
        "Appros Longs LLI (mois)",
        "Autres, Appros ou Semi-Finis (mois)",
        "Cycle Industriel (mois)",
        "Délai sécu (mois)",
        "Tmps recep (mois)",
        "date début t0 (mois)",
        "Mots clefs Risque",
        "Mots Clefs Retard Démontré",
        "Mots clefs expliquant le delta",
        "Mots Clefs Appros Longs",
        "Mots Clefs Complément au cycle industriel",
        "Mots Clefs Cycle Industriel Optimal",
        "Délais SAP non Analysé (mois)",
        "Marge appro (mois)",   # MODIF GRAPH-12, index 21
        # "Délai total (mois)" reste en DERNIÈRE position : plusieurs contrôles
        # le lisent par l'index -1 du customdata.
        "Délai total (mois)",
    ]

    df_analyse = df_power_bi

    hovertemplates = []

    for _, row in df_analyse.iterrows():

        hovertemplate = "<br>Désignation Article: %{customdata[0]}"
        hovertemplate += "<br>Référence Article: %{customdata[1]}"

        if not isinstance(row["Fournisseur"], float) and not pd.isna(
            row["Fournisseur"]
        ):
            hovertemplate += "<br>Fournisseur: %{customdata[3]}"

        if not isinstance(row["Mots clefs Risque"], float) and not pd.isna(
            row["Mots clefs Risque"]
        ):
            hovertemplate += "<br><span style='color: #FFD166;'>&#11044;</span> Risque majorant: %{customdata[5]}: %{customdata[14]}"

        if not isinstance(row["Mots Clefs Retard Démontré"], float) and not pd.isna(
            row["Mots Clefs Retard Démontré"]
        ):
            hovertemplate += "<br><span style='color: #EF476F;'>&#11044;</span> Somme des retards démontrés: %{customdata[6]}: %{customdata[15]}"

        if not isinstance(row["Mots clefs expliquant le delta"], float) and not pd.isna(
            row["Mots clefs expliquant le delta"]
        ):
            hovertemplate += "<br><span style='color: #F78C6B;'>&#11044;</span> Cycle SAP vs retard démontré: %{customdata[7]}: %{customdata[16]}"

        if not isinstance(row["Mots Clefs Appros Longs"], float) and not pd.isna(
            row["Mots Clefs Appros Longs"]
        ):
            hovertemplate += "<br><span style='color: #06D6AC;'>&#11044;</span> Appros Longs LLI: %{customdata[8]}: %{customdata[17]}"

        if not isinstance(
            row["Mots Clefs Complément au cycle industriel"], float
        ) and not pd.isna(row["Mots Clefs Complément au cycle industriel"]):
            hovertemplate += "<br><span style='color: #118AB2;'>&#11044;</span> Autres, Appros ou Semi-Finis: %{customdata[9]}: %{customdata[18]}"

        if not isinstance(
            row["Mots Clefs Cycle Industriel Optimal"], float
        ) and not pd.isna(row["Mots Clefs Cycle Industriel Optimal"]):
            hovertemplate += "<br><span style='color: #073B4C;'>&#11044;</span> Cycle Industriel fournisseur: %{customdata[10]}: %{customdata[19]}"

        hovertemplate += "<br><span style='color: #CFC841;'>&#11044;</span> Délai de sécurité: %{customdata[11]}"
        hovertemplate += "<br><span style='color: #E4DD5D;'>&#11044;</span> Temps de Réception: %{customdata[12]}"
        # MODIF GRAPH-12
        hovertemplate += "<br><span style='color: #B5A83A;'>&#11044;</span> Marge appro: %{customdata[21]}"
        hovertemplate += "<br><span style='color: #98d2eb;'>&#11044;</span> Décalage de t0: %{customdata[13]}"

        if isinstance(row["Mots Clefs Cycle Industriel Optimal"], float) and pd.isna(
            row["Mots Clefs Cycle Industriel Optimal"]
        ):
            hovertemplate += "<br><span style='color: #7142c6;'>&#11044;</span> Délai SAP non analysé: %{customdata[20]}"

        hovertemplate += "<br><span style='color: white;'>&#11044;</span> Délai total: %{customdata[22]}"

        # MODIF GRAPH-10 : prévenir que les segments dessinés sont plus courts
        # que les valeurs ci-dessus, sinon l'écart passe pour une erreur.
        if row["Cycle SAP (mois)"] < 0:
            hovertemplate += (
                "<br><i>Cycle SAP négatif : les segments de cycle sont réduits "
                "à l'écran pour que la barre garde la bonne longueur.</i>"
            )

        hovertemplate += "<extra></extra>"

        hovertemplates.append(hovertemplate)

    df_analyse["hovertemplate"] = hovertemplates

    # Check du statut de chaque trace, si une trace est masquée, alors sa valeur
    # est mise à 0 avant le recalcul.
    #
    # "date début t0 (mois)" fait exception : la masquer n'a aucun effet, elle
    # est de toute façon recalculée depuis le parent juste après.
    for json_obj in traceStatus:
        if (
            json_obj["visible"] == "legendonly"
            and json_obj["name"] in LISTE_COL_INTERET_ANALYSE  # MODIF GRAPH-6 : `colonnes`
        ):
            df_analyse[json_obj["name"]] = df_analyse[json_obj["name"]] * 0

    # Recalcul du t0 à partir de la relation article article parent
    # MODIF GRAPH-6 : la liste `colonnes` qui précédait ici est supprimée, elle
    # faisait doublon avec LISTE_COL_INTERET_ANALYSE à l'ordre près.
    # MODIF GRAPH-4 : corps de la fonction entièrement remplacé ; la fonction
    # elle-même reste imbriquée dans la route. Voir MODIFICATIONS.md.
    def calculer_delai_total_et_t0(df, colonnes):
        """Délai total et décalage t0 de chaque article de la désignation.

        Le cumul suit la profondeur dans l'arbre, pas l'ordre des lignes : un
        export où l'enfant précède son parent donnait sinon à l'enfant la durée
        *propre* du parent au lieu de son cumul, sans erreur ni avertissement.

        Le rattachement porte sur une position de ligne et non sur la seule
        référence article : un composant monté à plusieurs endroits de la
        nomenclature a autant de t0 que de montages, et un rapprochement par
        référence renverrait toujours le premier. On retient la dernière
        occurrence du parent située *avant* la ligne — convention de la
        nomenclature indentée, où le parent précède ses composants — et on se
        rabat sur une occurrence suivante si aucune ne précède.

        `colonnes` est la liste des postes empilés dans la barre. La colonne t0
        y figure comme poste dessiné, elle est écartée de la somme puisqu'elle
        est recalculée ici depuis le parent.
        """
        df = df.copy()

        postes = [c for c in colonnes if c != "date début t0 (mois)"]
        duree_propre = df[postes].fillna(0).sum(axis=1).to_numpy(dtype=float)

        # Position de la ligne parente de chaque ligne, -1 pour une racine
        positions = {}
        for i, article in enumerate(df["Article"].to_numpy()):
            positions.setdefault(article, []).append(i)

        parent_pos = np.full(len(df), -1, dtype=int)
        for i, ref_parent in enumerate(df["article parent"].to_numpy()):
            if pd.isna(ref_parent):
                continue
            candidats = positions.get(ref_parent)
            if not candidats:
                continue
            rang = bisect_left(candidats, i)
            if rang > 0:
                parent_pos[i] = candidats[rang - 1]
            else:
                suivants = [c for c in candidats if c != i]
                if suivants:
                    parent_pos[i] = suivants[0]

        # Profondeur de chaque ligne, pour cumuler les parents avant les enfants
        profondeur = np.full(len(df), -1, dtype=int)
        for depart in range(len(df)):
            chaine, vus, courant = [], set(), depart
            while courant != -1 and profondeur[courant] == -1:
                if courant in vus:
                    raise ValueError(
                        "Boucle dans la relation article / article parent, "
                        "lignes {}".format(sorted(vus))
                    )
                vus.add(courant)
                chaine.append(courant)
                courant = parent_pos[courant]

            niveau = 0 if courant == -1 else profondeur[courant] + 1
            for ligne in reversed(chaine):
                profondeur[ligne] = niveau
                niveau += 1

        # MODIF GRAPH-13 : bloc ajouté. La marge appro n'est plus un poste
        # empilé, c'est un décalage du t0. Le poste vaut -MargeAppr/20, donc une
        # marge positive rapproche l'enfant de la livraison : il démarre pendant
        # le cycle de son parent, ce qu'attend le métier sur un F/30.
        #
        # Le Délai total est inchangé au centime près — c'est le même terme, il
        # change seulement de place dans l'addition — mais la barre dessinée
        # redevient égale à ce total, ce qui n'était plus le cas dès qu'une
        # marge positive dépassait Sécu + Recep.
        #
        # Une racine garde t0 = 0 et sa marge n'est pas comptée : RG-040 est un
        # délai de lien, un article de tête n'a pas de parent donc pas de lien.
        decalage = df["Marge appro (mois)"].fillna(0).to_numpy(dtype=float)

        t0 = np.zeros(len(df), dtype=float)
        total = np.zeros(len(df), dtype=float)

        for ligne in np.argsort(profondeur, kind="stable"):
            parent = parent_pos[ligne]
            # MODIF GRAPH-13 : « + decalage[ligne] » ajouté, la ligne valait
            # t0[ligne] = 0.0 if parent == -1 else total[parent]
            t0[ligne] = 0.0 if parent == -1 else total[parent] + decalage[ligne]
            total[ligne] = duree_propre[ligne] + t0[ligne]

        df["date début t0 (mois)"] = np.round(t0, 2)
        df["Délai total (mois)"] = np.round(total, 2)

        return df

    df_analyse = calculer_delai_total_et_t0(df_analyse, LISTE_COL_INTERET_ANALYSE)  # MODIF GRAPH-6 : passait `colonnes`

    # MODIF GRAPH-10 : remplace l'écrêtage `clip(lower=0)` — lui-même remplaçant
    # de `.applymap(lambda x: max(x, 0))`, supprimé de pandas 3. L'écrêtage
    # laissait la barre plus longue que le Délai total dès qu'un segment était
    # négatif : Plotly ignore un segment négatif dans une barre empilée, donc la
    # longueur manquait à l'appel sans que rien ne le signale.
    def ajuster_pour_affichage(df, colonnes):
        """Version du DataFrame destinée au tracé, sans segment négatif.

        On répartit les valeurs négatives sur les postes voisins au lieu de les
        écrêter, pour que la longueur de la barre reste égale au Délai total.

        Cas principal, le Cycle SAP. Il vaut `Cycle SAP - ZPIF - ZO1 - ZO2` :
        négatif, il veut dire que le cycle industriel et ses gains dépassent le
        cycle SAP réel. On réduit donc ces trois postes au prorata jusqu'à
        absorber le dépassement, et le segment Cycle SAP tombe à 0. Les trois
        postes réduits somment alors exactement au cycle SAP.

        Tout autre poste négatif est ramené à 0 et son montant retiré au prorata
        des postes encore positifs.

        Le décalage t0 est laissé hors de la répartition : c'est une position
        dans le temps, pas une quantité, et le réduire décalerait le début de la
        barre.

        Les valeurs réelles restent dans df_analyse : le survol continue de les
        afficher, c'est là qu'on lit la décomposition exacte.
        """
        affichage = df.copy()
        rang = {c: i for i, c in enumerate(colonnes)}
        postes = affichage[colonnes].to_numpy(dtype=float).copy()

        absorbables = [i for c, i in rang.items() if c != "date début t0 (mois)"]

        # 1. Cycle SAP négatif : absorbé par les trois postes dont il est le résidu
        i_sap = rang.get("Cycle SAP (mois)")
        groupe = [rang[c] for c in GROUPE_CYCLE_SAP if c in rang]
        if i_sap is not None and groupe:
            for ligne in np.where(postes[:, i_sap] < 0)[0]:
                deficit = -postes[ligne, i_sap]
                disponible = postes[ligne, groupe].clip(min=0)
                base = disponible.sum()
                absorbe = min(deficit, base)
                if base > 0:
                    postes[ligne, groupe] = disponible * (1.0 - absorbe / base)
                # Reliquat non absorbé : laissé négatif pour l'étape 3, sinon
                # la barre resterait trop longue de ce montant.
                postes[ligne, i_sap] = -(deficit - absorbe)

        # MODIF GRAPH-13 : l'étape « 2. Marge appro négative », qui répartissait
        # le segment de marge sur Tmps recep et Délai sécu, est supprimée. La
        # marge n'est plus un poste tracé (MODIF GRAPH-13a) : elle n'apparaît
        # plus dans `colonnes`, donc dans `rang`, et le bloc ne s'exécutait plus.
        # Supprimé plutôt que laissé : du code mort qui a l'air vivant.

        # 3. Filet de sécurité : tout négatif restant, au prorata du reste.
        #    Quand le lien est négatif au point de dépasser la durée de la
        #    tâche — une marge appro très supérieure à Sécu + Recep — la
        #    contribution de l'article est négative et la barre tombe à zéro :
        #    c'est le plus court qu'une barre puisse être. Le Délai total du
        #    survol, lui, reste inférieur au t0 et dit la vérité.
        for ligne in np.where((postes[:, absorbables] < 0).any(axis=1))[0]:
            valeurs = postes[ligne, absorbables]
            deficit = -valeurs[valeurs < 0].sum()
            valeurs = valeurs.clip(min=0)
            base = valeurs.sum()
            if base > 0:
                valeurs = valeurs * max(0.0, 1.0 - min(deficit, base) / base)
            postes[ligne, absorbables] = valeurs

        for colonne, i in rang.items():
            affichage[colonne] = np.round(np.maximum(postes[:, i], 0.0), 2)

        return affichage

    # Version du DataFrame destinée au tracé
    df_analyse_positive = ajuster_pour_affichage(
        df_analyse, LISTE_COL_INTERET_ANALYSE
    )

    # Check de la valeur visible de chaque légende pour l'affichée masquée ou pas lors du retraçage du graphique
    trace_visibility = {
        json_obj["name"]: json_obj["visible"] for json_obj in traceStatus
    }

    # Traçage de chaque colonne
    fig = go.Figure()

    for col in LISTE_COL_INTERET_ANALYSE:  # MODIF GRAPH-5 : liste locale

        visible = None if trace_visibility.get(col) != "legendonly" else "legendonly"

        fig.add_trace(
            go.Bar(
                x=df_analyse_positive[col],
                y=df_analyse_positive[attributRef],
                texttemplate="%{x:.0f}",
                textposition="inside",
                textangle=0,
                textfont_color="white",
                orientation="h",
                customdata=df_analyse[liste_hover_template_analyse].fillna("").values,
                hovertemplate=df_analyse["hovertemplate"],
                marker_color=DICO_COLOR[col],  # MODIF GRAPH-5 : dico local
                name=col,
                visible=visible,
            )
        )

    fig.update_layout(barmode="stack", title_text="Relative Barmode")

    fig.update_layout(
        yaxis_title="Article",
        xaxis_title="Mois",
        title="Cascade cyclée par rapport à t0 (en mois)",
        height=700,
    )

    fig.update_layout(template="plotly_white", height=700)
    fig.update_yaxes(side="right")
    fig.update_xaxes(autorange="reversed")

    fig.update_layout(
        legend=dict(
            orientation="h",
            yanchor="top",
            xanchor="left",
            y=-0.1,
            x=-0.1,
            bgcolor="rgba(0,0,0,0)",
        )
    )

    fig.update_layout(
        hoverlabel=dict(
            bgcolor="rgba(0,0,0,1)",
            font_size=10,
        )
    )

    if b_ordonner:
        fig.update_yaxes(categoryorder="total ascending")
    else:
        fig.update_layout(
            yaxis={
                "categoryorder": "array",
                "categoryarray": df_analyse[attributRef].tolist()[::-1],
            }
        )

    data = {"graph": json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)}

    _ = clickedTrace
    return jsonify(data)
