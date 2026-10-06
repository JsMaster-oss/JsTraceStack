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

        data = get_listes_selecteur(df_cycle_detail)
        data["combinaisons"] = get_combinaisons_selecteur(df_cycle_detail)

        return jsonify(data)

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


def _normaliser(df_cycle_detail, colonnes):
    """
    Sous-ensemble du DataFrame limité aux colonnes des sélecteurs, renommées
    avec les noms attendus par la page et normalisées en chaînes.

    C'est le point commun aux deux fonctions publiques : libellés des cases à
    cocher et valeurs des combinaisons sortent d'ici, donc sont identiques.
    """
    manquantes = [col for col in colonnes.values() if col not in df_cycle_detail.columns]
    if manquantes:
        raise KeyError(
            "Colonnes absentes du DataFrame des cycles : " + ", ".join(manquantes)
        )

    sous_df = df_cycle_detail[list(colonnes.values())].copy()
    sous_df.columns = list(colonnes.keys())

    for selecteur in sous_df.columns:
        # fillna avant astype : sinon un NaN deviendrait la chaîne "nan"
        sous_df[selecteur] = sous_df[selecteur].fillna("").astype(str).str.strip()

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

    sous_df = _normaliser(df_cycle_detail, colonnes)

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

    sous_df = _normaliser(df_cycle_detail, colonnes)

    return sous_df.drop_duplicates().to_dict(orient="records")
