/* ==================================================================
   filtres_selecteur.js
   Gestion des sélecteurs à cases à cocher de la page visualisation.

   DEUX PRINCIPES
   --------------
   1. Aucune case cochée sur un sélecteur = AUCUNE contrainte.
      Au chargement rien n'est coché, le graphique porte donc sur
      l'ensemble des données. L'utilisateur coche ce qu'il veut voir
      au lieu de décocher ce qu'il ne veut pas.

   2. Les sélecteurs sont INTERDÉPENDANTS : chaque sélecteur ne propose
      que les valeurs encore compatibles avec les choix faits sur les
      AUTRES sélecteurs. Le sélecteur lui-même est exclu du calcul :
      ses valeurs restent donc toutes affichées, ce qui permet d'élargir
      sa propre sélection.

   PRÉ-REQUIS POUR LE POINT 2
   --------------------------
   Le navigateur ne détient pas les données (c'est le serveur qui
   construit le graphique). L'interdépendance s'appuie donc sur la liste
   des combinaisons existantes, renvoyée une seule fois par le serveur
   dans la clé "combinaisons" de /get_data_selecteur :

       [{"reference_article": "...", "programme": "...",
         "fournisseur": "...", "categorie": "...",
         "sous_categorie": "..."}, ...]

   Si cette clé est absente, le module fonctionne quand même : le point 1
   s'applique, le point 2 est simplement neutralisé (toutes les valeurs
   restent proposées). Aucune régression possible.

   STRUCTURE HTML ATTENDUE (déjà en place dans visualisation.html)
   --------------------------------------------------------------
       #id_filtre_<selecteur>      : <input type="text">  recherche
       #id_radio_<selecteur>       : <input type="checkbox"> "Tout cocher"
       #id_list_check_<selecteur>  : <div class="radio"> liste des valeurs
   ================================================================== */

var FiltresSelecteur = (function () {
    "use strict";

    var _selecteurs = [];           // ["reference_article", "programme", ...]
    var _combinaisons = [];         // combinaisons existantes (cf. ci-dessus)
    var _onChange = function () {}; // appelé quand la sélection change
    var _envoyerToutSiVide = true;  // cf. contraintes()

    /* ---------- petits accès DOM ---------- */

    function divListe(nom)   { return document.getElementById("id_list_check_" + nom); }
    function inputRecherche(nom) { return document.getElementById("id_filtre_" + nom); }
    function inputToutCocher(nom) { return document.getElementById("id_radio_" + nom); }

    function cases(nom)
    {
        var div = divListe(nom);
        return (div === null) ? [] : div.querySelectorAll('input[type="checkbox"]');
    }

    function libelle(check)
    {
        var label = check.parentNode.querySelector('label');
        return (label === null) ? "" : label.innerHTML;
    }

    // Retrouve le nom du sélecteur à partir de l'id de l'élément déclencheur
    function nomDepuisId(id, prefixe)
    {
        if (typeof id !== "string" || id.indexOf(prefixe) !== 0) { return null; }
        var nom = id.substring(prefixe.length);
        return (_selecteurs.indexOf(nom) === -1) ? null : nom;
    }

    /* ---------- construction des cases à cocher ---------- */

    function init(config)
    {
        _selecteurs   = config.selecteurs || [];
        _combinaisons = config.combinaisons || [];
        _onChange     = config.onChange || function () {};

        if (config.envoyerToutSiVide === false) { _envoyerToutSiVide = false; }

        for (var s = 0; s < _selecteurs.length; s++)
        {
            var nom = _selecteurs[s];
            var divRadio = divListe(nom);

            if (divRadio === null) { continue; }

            var list_option = (config.valeurs || {})[nom] || [];

            divRadio.innerHTML = "";

            var fieldset = document.createElement('fieldset');
            fieldset.className = "scroll_selecteur";

            for (var o = 0; o < list_option.length; o++)
            {
                var option = list_option[o];

                var divCheckBox = document.createElement('div');

                var inputCheckBox = document.createElement('input');
                inputCheckBox.type = "checkbox";
                inputCheckBox.id = "id_checkbox_" + nom + "_" + option;
                // Décoché par défaut : aucune contrainte, tout est pris en compte
                inputCheckBox.checked = false;
                inputCheckBox.onclick = function () { changement(); };

                var labelCheckBox = document.createElement('label');
                labelCheckBox.innerHTML = option;
                labelCheckBox.setAttribute("for", inputCheckBox.id);
                labelCheckBox.classList.add("filterDiv");

                divCheckBox.appendChild(inputCheckBox);
                divCheckBox.appendChild(labelCheckBox);
                fieldset.appendChild(divCheckBox);
            }

            divRadio.appendChild(fieldset);

            var toutCocher = inputToutCocher(nom);
            if (toutCocher !== null) { toutCocher.checked = false; }

            // On repart d'une recherche vide, sinon un texte résiduel
            // masquerait des valeurs de la liste fraîchement reconstruite
            var recherche = inputRecherche(nom);
            if (recherche !== null) { recherche.value = ""; }
        }

        majAffichage();
    }

    /* ---------- lecture de l'état courant ---------- */

    function etats()
    {
        var liste = [];

        for (var s = 0; s < _selecteurs.length; s++)
        {
            var nom = _selecteurs[s];
            var coches = [];
            var liste_cases = cases(nom);

            for (var c = 0; c < liste_cases.length; c++)
            {
                if (liste_cases[c].checked) { coches.push(libelle(liste_cases[c])); }
            }

            var recherche = inputRecherche(nom);

            liste.push({nom: nom,
                        coches: coches,
                        recherche: (recherche === null) ? "" : recherche.value.toUpperCase()});
        }
        return liste;
    }

    /* ---------- une combinaison passe-t-elle les filtres ? ----------
       ignore = indice du sélecteur à NE PAS appliquer (-1 = tous).
       C'est ce paramètre qui produit l'interdépendance.               */

    function combinaison_valide(combi, etats_courants, ignore)
    {
        for (var i = 0; i < etats_courants.length; i++)
        {
            if (i === ignore) { continue; }

            // Aucune case cochée => pas de contrainte sur ce sélecteur
            if (etats_courants[i].coches.length === 0) { continue; }

            if (etats_courants[i].coches.indexOf(combi[etats_courants[i].nom]) === -1)
            {
                return false;
            }
        }
        return true;
    }

    /* ---------- mise à jour des valeurs proposées ---------- */

    function majAffichage()
    {
        var etats_courants = etats();

        for (var i = 0; i < _selecteurs.length; i++)
        {
            var nom = _selecteurs[i];

            // Valeurs encore possibles compte tenu des AUTRES sélecteurs.
            // null = interdépendance indisponible (pas de combinaisons fournies)
            var dispo = null;

            if (_combinaisons.length > 0)
            {
                dispo = new Set();
                for (var k = 0; k < _combinaisons.length; k++)
                {
                    if (combinaison_valide(_combinaisons[k], etats_courants, i))
                    {
                        dispo.add(_combinaisons[k][nom]);
                    }
                }
            }

            var liste_cases = cases(nom);
            var visibles = 0;
            var cochees = 0;

            for (var c = 0; c < liste_cases.length; c++)
            {
                var check = liste_cases[c];
                var txt = libelle(check);

                // Une valeur cochée reste visible même si elle n'est plus
                // proposée, sinon il deviendrait impossible de la décocher
                var compatible = (dispo === null) || dispo.has(txt) || check.checked;

                // La zone de recherche ne filtre QUE la liste affichée,
                // elle n'a aucun effet sur le graphique
                var trouve = txt.toUpperCase().indexOf(etats_courants[i].recherche) !== -1;

                var affiche = compatible && trouve;
                check.parentNode.style.display = affiche ? "" : "none";

                if (affiche)
                {
                    visibles++;
                    if (check.checked) { cochees++; }
                }
            }

            // "Tout cocher" reflète l'état des valeurs réellement visibles
            var toutCocher = inputToutCocher(nom);
            if (toutCocher !== null)
            {
                toutCocher.checked = (visibles > 0) && (visibles === cochees);
            }
        }
    }

    /* ---------- données envoyées au serveur ---------- */

    function contraintes()
    {
        var server_data = {};

        for (var s = 0; s < _selecteurs.length; s++)
        {
            var nom = _selecteurs[s];
            var coches = [];
            var liste_cases = cases(nom);

            for (var c = 0; c < liste_cases.length; c++)
            {
                if (liste_cases[c].checked) { coches.push(libelle(liste_cases[c])); }
            }

            if (coches.length === 0 && _envoyerToutSiVide)
            {
                // Rien de coché = aucune contrainte. On envoie toutes les
                // valeurs : le serveur n'a pas besoin d'être modifié, il
                // continue de recevoir une liste non vide.
                // Passer envoyerToutSiVide:false une fois que la route sait
                // traiter une liste vide comme "pas de filtre" (plus léger).
                for (var c2 = 0; c2 < liste_cases.length; c2++)
                {
                    coches.push(libelle(liste_cases[c2]));
                }
            }

            server_data[nom] = coches;
        }
        return server_data;
    }

    /* ---------- gestionnaires d'évènements ---------- */

    function changement()
    {
        majAffichage();
        _onChange();
    }

    // Remplace l'ancienne fonction filtrer() : recalcul centralisé,
    // plus aucune manipulation directe des labels
    function filtrer(evt)
    {
        var nom = nomDepuisId(evt.target.id, "id_filtre_");
        if (nom === null) { return; }
        majAffichage();
    }

    // Remplace l'ancienne fonction coche_decoche() : ne porte que sur
    // les valeurs visibles, donc compatibles avec les autres sélecteurs
    function coche_decoche(evt)
    {
        var nom = nomDepuisId(evt.target.id, "id_radio_");
        if (nom === null) { return; }

        var liste_cases = cases(nom);
        for (var c = 0; c < liste_cases.length; c++)
        {
            if (liste_cases[c].parentNode.style.display !== "none")
            {
                liste_cases[c].checked = evt.target.checked;
            }
        }
        changement();
    }

    // Remet tous les sélecteurs à zéro (aucune case cochée)
    function reinitialiser()
    {
        for (var s = 0; s < _selecteurs.length; s++)
        {
            var liste_cases = cases(_selecteurs[s]);
            for (var c = 0; c < liste_cases.length; c++) { liste_cases[c].checked = false; }
        }
        changement();
    }

    return {init: init,
            majAffichage: majAffichage,
            contraintes: contraintes,
            filtrer: filtrer,
            coche_decoche: coche_decoche,
            reinitialiser: reinitialiser};
})();
