# -*- coding: utf-8 -*-
"""
Interdépendance des sélecteurs de la page visualisation.

La page ne détient pas les données : c'est le serveur qui construit le
graphique. Pour que les sélecteurs puissent se restreindre mutuellement côté
navigateur, /get_data_selecteur doit renvoyer, en plus des listes de valeurs,
la liste des COMBINAISONS existantes.

Il ne s'agit pas des données complètes mais des n-uplets distincts. Comme
generate_data_cycle() ne garde qu'une ligne par Designation_Article, leur
nombre est au plus celui des articles : c'est léger, et chargé une seule fois.

Intégration dans la route existante — le corps devient :

    @main.route("/get_data_selecteur", methods=["POST"])
    @login_required
    @roles_required("Admin", "Writer", "Reader")
    def get_data_selecteur():

        # Recuperation des donnees de cycles (MongoDB)
        df_cycle_detail = generate_data_cycle(config)
        df_cycle_detail = normaliser_colonnes_selecteur(df_cycle_detail)

        data = get_listes_selecteur(df_cycle_detail)
        data["combinaisons"] = get_combinaisons_selecteur(df_cycle_detail)

        return jsonify(data)

ET, IMPÉRATIVEMENT, LA MÊME NORMALISATION AVANT LE FILTRAGE DU GRAPHIQUE :

    @main.route("/create_graph_analyse_CE2", methods=["POST"])
    ...
    def create_graph_analyse_CE2():

        df_cycle_detail = generate_data_cycle(config)
        df_cycle_detail = normaliser_colonnes_selecteur(df_cycle_detail)
        # ... puis le filtrage existant sur req["reference_article"], etc.

Sans cela, une valeur portant un espace de fin est affichée "REF-A" mais vaut
"REF-A " en base : le filtrage ne retient aucune ligne et le graphique sort
vide, sans erreur.

POURQUOI REMPLACER AUSSI LA CONSTRUCTION DES LISTES
---------------------------------------------------
Côté navigateur, l'interdépendance compare les valeurs des combinaisons aux
libellés des cases à cocher. Si les deux ne sont pas produits exactement de la
même façon, la comparaison échoue silencieusement et le filtre se vide.

Le cas qui pose problème est la valeur manquante : avec `list(df[col].unique())`
un NaN part en JSON `null` et devient le libellé "null" dans la page, alors
qu'une conversion en chaîne côté combinaisons donnerait "nan". Les deux
fonctions ci-dessous partagent donc la même normalisation.

Tant que la clé "combinaisons" n'est pas renvoyée, visualisation.html
fonctionne quand même : les cases restent décochées par défaut, seule
l'interdépendance est inactive. Aucune régression possible.
"""

# Correspondance entre le nom du sélecteur côté page (list_nom_selecteur dans
# visualisation.html) et le nom de la colonne du DataFrame renvoyé par
# generate_data_cycle().
COLONNES_SELECTEUR = {
    "reference_article": "Référence Article",
    "programme": "Programme",
    "fournisseur": "Fournisseur",
    "categorie": "Catégorie Technologique",
    "sous_categorie": "Sous catégorie",
}


def normaliser_colonnes_selecteur(df_cycle_detail, colonnes=None):
    """
    Renvoie une copie du DataFrame dont les colonnes des sélecteurs sont
    normalisées : valeurs manquantes ramenées à une chaîne vide, conversion en
    chaîne, espaces de début et de fin supprimés. Les noms de colonnes sont
    inchangés.

    À APPLIQUER AUX DEUX ENDROITS
    -----------------------------
    Cette fonction doit être appelée aussi bien dans /get_data_selecteur (qui
    produit les libellés des cases à cocher) que dans /create_graph_analyse_CE2
    (qui filtre les données avec les valeurs renvoyées par la page).

    Si elle n'est appliquée que d'un côté, une valeur portant un espace de fin
    est affichée "REF-A" mais vaut "REF-A " en base : le `isin` du filtrage ne
    retient aucune ligne et le graphique sort vide, sans erreur.

    La fonction est idempotente : l'appliquer deux fois ne change rien.

    Parameters
    ----------
    df_cycle_detail : pandas.DataFrame
        Sortie de generate_data_cycle(config).
    colonnes : dict, optional
        {nom_selecteur: nom_colonne}. COLONNES_SELECTEUR par défaut.

    Returns
    -------
    pandas.DataFrame
        Copie normalisée, mêmes colonnes.
    """
    if colonnes is None:
        colonnes = COLONNES_SELECTEUR

    manquantes = [col for col in colonnes.values() if col not in df_cycle_detail.columns]
    if manquantes:
        raise KeyError(
            "Colonnes absentes du DataFrame des cycles : " + ", ".join(manquantes)
        )

    df_normalise = df_cycle_detail.copy()

    for colonne in colonnes.values():
        # fillna avant astype : sinon un NaN deviendrait la chaîne "nan"
        df_normalise[colonne] = (
            df_normalise[colonne].fillna("").astype(str).str.strip()
        )

    return df_normalise


def _sous_df(df_cycle_detail, colonnes):
    """
    Sous-ensemble normalisé, limité aux colonnes des sélecteurs et renommé avec
    les noms attendus par la page.

    Point commun aux deux fonctions publiques : libellés des cases à cocher et
    valeurs des combinaisons sortent d'ici, donc sont identiques.
    """
    df_normalise = normaliser_colonnes_selecteur(df_cycle_detail, colonnes)

    sous_df = df_normalise[list(colonnes.values())].copy()
    sous_df.columns = list(colonnes.keys())

    return sous_df


def get_listes_selecteur(df_cycle_detail, colonnes=None, trier=True):
    """
    Listes de valeurs proposées par chaque sélecteur.

    Remplace les `list(df[...].unique())` de la route : même résultat, mais
    produit avec la normalisation partagée avec get_combinaisons_selecteur().

    Parameters
    ----------
    df_cycle_detail : pandas.DataFrame
        Sortie de generate_data_cycle(config).
    colonnes : dict, optional
        {nom_selecteur: nom_colonne}. COLONNES_SELECTEUR par défaut.
    trier : bool, optional
        True (défaut) : valeurs triées par ordre alphabétique.
        False : ordre d'apparition dans le DataFrame, comme `.unique()`.

    Returns
    -------
    dict
        {"reference_article": [...], "programme": [...], ...}
    """
    if colonnes is None:
        colonnes = COLONNES_SELECTEUR

    sous_df = _sous_df(df_cycle_detail, colonnes)

    data = {}
    for selecteur in colonnes:
        valeurs = sous_df[selecteur].unique().tolist()
        data[selecteur] = sorted(valeurs) if trier else valeurs

    return data


def get_combinaisons_selecteur(df_cycle_detail, colonnes=None):
    """
    Combinaisons de valeurs réellement présentes dans les données.

    Une seule entrée par n-uplet distinct, par exemple :
        [{"reference_article": "REF-A", "programme": "P1",
          "fournisseur": "F1", "categorie": "C1", "sous_categorie": "SC1"},
         ...]

    Parameters
    ----------
    df_cycle_detail : pandas.DataFrame
        Sortie de generate_data_cycle(config).
    colonnes : dict, optional
        {nom_selecteur: nom_colonne}. COLONNES_SELECTEUR par défaut.

    Returns
    -------
    list[dict]
        Clés = noms des sélecteurs côté page.
    """
    if colonnes is None:
        colonnes = COLONNES_SELECTEUR

    sous_df = _sous_df(df_cycle_detail, colonnes)

    return sous_df.drop_duplicates().to_dict(orient="records")


def appliquer_filtres_selecteur(df_cycle_detail, req, colonnes=None):
    """
    Applique au DataFrame les filtres envoyés par la page.

    RÈGLE CENTRALE : une liste vide (ou absente) signifie AUCUNE contrainte sur
    ce sélecteur. La colonne est alors laissée tranquille — on ne filtre pas.

    C'est ce qui garantit qu'un graphique sans filtre actif est complet. Filtrer
    sur la liste de toutes les valeurs ne serait PAS équivalent : ce serait
    comparer les données à des libellés, et toute ligne dont la valeur ne
    correspond pas exactement au libellé affiché disparaîtrait, sans filtre et
    sans erreur.

    La normalisation est appliquée ici, donc les valeurs comparées sont bien
    celles qui ont servi à fabriquer les libellés.

    Parameters
    ----------
    df_cycle_detail : pandas.DataFrame
        Sortie de generate_data_cycle(config).
    req : dict
        Corps de la requête : {"reference_article": [...], "programme": [...],
        ..., "b_ordonner": bool}. Les clés inconnues sont ignorées.
    colonnes : dict, optional
        {nom_selecteur: nom_colonne}. COLONNES_SELECTEUR par défaut.

    Returns
    -------
    pandas.DataFrame
        Sous-ensemble filtré, colonnes des sélecteurs normalisées.
    """
    if colonnes is None:
        colonnes = COLONNES_SELECTEUR

    df_filtre = normaliser_colonnes_selecteur(df_cycle_detail, colonnes)

    for selecteur, colonne in colonnes.items():
        valeurs = req.get(selecteur) or []

        # Aucune case cochée : on ne touche pas à cette colonne
        if not valeurs:
            continue

        df_filtre = df_filtre[df_filtre[colonne].isin(valeurs)]

    return df_filtre


def completer_filtres_selecteur(req, df_cycle_detail, colonnes=None):
    """
    Remplace les listes vides de la requête par toutes les valeurs de la
    colonne correspondante, prises sur le DataFrame NORMALISÉ.

    À utiliser quand on ne veut pas toucher au filtrage existant d'une route :
    le code qui fait `df[df[col].isin(req[selecteur])]` continue de fonctionner
    tel quel, et une liste vide se comporte bien comme "aucune contrainte".

        df_cycle_detail = generate_data_cycle(config)

        df_cycle_detail = normaliser_colonnes_selecteur(df_cycle_detail)
        req = completer_filtres_selecteur(req, df_cycle_detail)

        # ... filtrage existant, inchangé

    POURQUOI CE N'EST PAS LE BUG QUE L'ON VIENT DE CORRIGER
    -------------------------------------------------------
    La page, elle, ne doit jamais envoyer toutes les valeurs : ses libellés
    seraient confrontés à des données brutes, et toute ligne dont une valeur
    diffère de son libellé disparaîtrait. Ici les valeurs de remplissage sont
    extraites du DataFrame normalisé lui-même, celui qui va être filtré : la
    correspondance est exacte par construction, donc aucune ligne n'est perdue.

    Attention : `df_cycle_detail` doit être le DataFrame réellement filtré
    ensuite, et déjà normalisé.

    Parameters
    ----------
    req : dict
        Corps de la requête reçue de la page.
    df_cycle_detail : pandas.DataFrame
        DataFrame normalisé qui sera filtré.
    colonnes : dict, optional
        {nom_selecteur: nom_colonne}. COLONNES_SELECTEUR par défaut.

    Returns
    -------
    dict
        Copie de req, listes vides remplacées. Les autres clés
        (b_ordonner...) sont conservées telles quelles.
    """
    if colonnes is None:
        colonnes = COLONNES_SELECTEUR

    req_complete = dict(req)

    for selecteur, colonne in colonnes.items():
        if req_complete.get(selecteur):
            continue

        # Aucune case cochée : on remplit avec les valeurs du DataFrame qui
        # sera filtré, donc le filtre ne peut écarter aucune ligne
        req_complete[selecteur] = df_cycle_detail[colonne].unique().tolist()

    return req_complete
