# -*- coding: utf-8 -*-
"""
Interdépendance des sélecteurs de la page visualisation.

La page visualisation ne détient pas les données : c'est le serveur qui
construit le graphique. Pour que les sélecteurs puissent se restreindre
mutuellement côté navigateur, la route /get_data_selecteur doit renvoyer, en
plus des listes de valeurs, la liste des COMBINAISONS existantes.

Il ne s'agit pas des données complètes mais seulement des n-uplets distincts
(une entrée par combinaison réellement présente en base), ce qui reste léger et
n'est chargé qu'une fois au chargement de la page.

À intégrer dans la route /get_data_selecteur existante :

    data = {...}                                    # ce qui est déjà renvoyé
    data["combinaisons"] = get_combinaisons_selecteur(my_collection)
    return jsonify(data)

Tant que cette clé n'est pas renvoyée, templates/visualisation.html fonctionne
quand même : les cases restent décochées par défaut, seule l'interdépendance
est inactive. Aucune régression possible.
"""

# Correspondance entre le nom du sélecteur côté page (list_nom_selecteur dans
# visualisation.html) et le nom du champ dans la collection MongoDB.
# >>> À VÉRIFIER / AJUSTER selon le schéma réel de la collection <<<
CHAMPS_SELECTEUR = {
    "reference_article": "Reference_Article",
    "programme": "Programme",
    "fournisseur": "Fournisseur",
    "categorie": "Categorie",
    "sous_categorie": "Sous_Categorie",
}


def get_combinaisons_selecteur(my_collection, champs=None):
    """
    Renvoie la liste des combinaisons de valeurs réellement présentes en base.

    Une seule entrée par n-uplet distinct, par exemple :
        [{"reference_article": "REF-A", "programme": "P1",
          "fournisseur": "F1", "categorie": "C1", "sous_categorie": "SC1"},
         ...]

    Parameters
    ----------
    my_collection : pymongo.collection.Collection
        Collection sur laquelle porte la page visualisation.
    champs : dict, optional
        Correspondance {nom_selecteur: champ_mongo}. CHAMPS_SELECTEUR par défaut.

    Returns
    -------
    list[dict]
        Combinaisons distinctes, clés = noms des sélecteurs côté page.
    """
    if champs is None:
        champs = CHAMPS_SELECTEUR

    pipeline = [
        {"$group": {"_id": {sel: "$" + champ for sel, champ in champs.items()}}},
        {"$replaceRoot": {"newRoot": "$_id"}},
    ]

    combinaisons = []
    for doc in my_collection.aggregate(pipeline):
        # Une valeur absente deviendrait None et ne correspondrait à aucune
        # case à cocher : on la ramène à une chaîne vide
        combinaisons.append({sel: (doc.get(sel) or "") for sel in champs})

    return combinaisons
