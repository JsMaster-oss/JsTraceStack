// fichier : accueil.js
// ========================================
// Gestion des interactions UI pour la page d'accueil
// Compatible avec l'architecture 3 tables (sticky, vx, compare)
// ========================================

/**
 * Recharge puis reconstruit le tableau pour la famille + les versions selectionnees.
 *
 * Point d'entree unique de tous les handlers (famille, version courante, version
 * comparee) : loadVersionData sert le cache si la version demandee est deja
 * chargee, sinon il la recharge. Comme la famille est resolue depuis le
 * selecteur quand currentFamily n'est pas encore renseigne (1er chargement en
 * cours), un changement de version pendant la requete initiale n'est plus ignore.
 */
function reloadTable(famille, buildFilters) {
    // Le libelle du selecteur fait foi : currentFamily n'est mis a jour qu'a la
    // reception de la reponse, il pointe encore sur la famille precedente si on
    // change de version juste apres avoir change de famille.
    famille = famille || $('#filter-select').text().trim() || currentFamily;
    if (!famille) return;
    if (buildFilters === undefined) buildFilters = true;

    function rebuild() {
        rebuildTable($('#tbody-sticky'), $('#tbody-vx'), $('#tbody-compare'),
                     getCurrentVersionData(), buildFilters, true);
    }

    loadVersionData(famille, versionCurrent, currentUserRole, buildFilters, function() {
        if (!isCompareMode) {
            rebuild();
            return;
        }
        loadCompareData(famille, versionCurrent, versionCompared, rebuild);
    });
}

$(document).ready(function() {
    // === INITIALISATION ===
    $("#input-annees-affichees").val(parseInt(yearsToShowByQuarter));

    // Restaurer les versions mémorisées (cookie) si présentes
    var memoCourante = getCookie('versionCourante');
    var memoComparee = getCookie('versionComparee');
    if (memoCourante && appliquerVersion('#panel-version-current', '#filter-select-version-current', memoCourante)) versionCurrent = memoCourante;
    if (memoComparee && appliquerVersion('#panel-version', '#filter-select-version', memoComparee)) versionCompared = memoComparee;
    isCompareMode = versionCurrent !== versionCompared;
    createCookie('versionCourante', versionCurrent);
    createCookie('versionComparee', versionCompared);

    // Charger la famille par défaut (avec comparaison si une version comparée est mémorisée)
    var initialFamily = $('#filter-select').text().trim();
    if (initialFamily) {
        reloadTable(initialFamily, true);
    }

    // === DROPDOWN FAMILLE ===
    $('.trie-f-c .select').on('click', function(e) {
        e.stopPropagation();
        $(this).toggleClass('active');
        $(this).next('.field-list').toggleClass('open');
        // Fermer les dropdowns version si ouverts
        $('.trie-v-current .select, .trie-v-compare .select').removeClass('active');
        $('.trie-v-current .field-list, .trie-v-compare .field-list').removeClass('open');
    });

    // === DROPDOWN VERSION COURANTE ===
    $('.trie-v-current .select').on('click', function(e) {
        e.stopPropagation();
        $(this).toggleClass('active');
        $(this).next('.field-list').toggleClass('open');
        // Fermer les autres dropdowns
        $('.trie-f-c .select').removeClass('active');
        $('.trie-f-c .field-list').removeClass('open');
        $('.trie-v-compare .select').removeClass('active');
        $('.trie-v-compare .field-list').removeClass('open');
    });

    // === DROPDOWN VERSION COMPARAISON ===
    $('.trie-v-compare .select').on('click', function(e) {
        e.stopPropagation();
        $(this).toggleClass('active');
        $(this).next('.field-list').toggleClass('open');
        // Fermer les autres dropdowns
        $('.trie-f-c .select').removeClass('active');
        $('.trie-f-c .field-list').removeClass('open');
        $('.trie-v-current .select').removeClass('active');
        $('.trie-v-current .field-list').removeClass('open');
    });

    // Fermer les dropdowns sur clic exterieur
    $('body').on('click', function(event) {
        if (!$(event.target).closest('.trie-f-c').length) {
            $('.trie-f-c .select').removeClass('active');
            $('.trie-f-c .field-list').removeClass('open');
        }
        if (!$(event.target).closest('.trie-v-current').length) {
            $('.trie-v-current .select').removeClass('active');
            $('.trie-v-current .field-list').removeClass('open');
        }
        if (!$(event.target).closest('.trie-v-compare').length) {
            $('.trie-v-compare .select').removeClass('active');
            $('.trie-v-compare .field-list').removeClass('open');
        }
    });

    // === SELECTION FAMILLE ===
    $('body').on('click', '#panel-famille li', function() {
        // Fermer le dropdown
        $('.trie-f-c .select').removeClass('active');
        $('.trie-f-c .field-list').removeClass('open');

        var searchFamille = $(this).text().trim();
        var selected = $('#filter-select');

        if (searchFamille !== selected.text().trim()) {
            selected.text(searchFamille);
            $('#panel-famille li.active').removeClass('active');
            $(this).addClass('active');

            // Charger les donnees de la nouvelle famille en conservant les versions sélectionnées
            reloadTable(searchFamille, true);

            // Reinitialiser les filtres
            $('.checkFilterField').each(function() {
                var type = $(this).attr('type');
                if (type === 'checkbox') {
                    $(this).prop('checked', false);
                } else if (type === 'date') {
                    $(this).val('');
                }
            });
            $('#reference-search').val('');
            $('#reference-search-clear').hide();
        }
    });

    // === SELECTION VERSION COURANTE ===
    $('body').on('click', '#panel-version-current li', function() {
        // Fermer le dropdown
        $('.trie-v-current .select').removeClass('active');
        $('.trie-v-current .field-list').removeClass('open');

        var selectedVersion = $(this).text().trim();

        $('#filter-select-version-current').text(selectedVersion);
        $('#panel-version-current li.active').removeClass('active');
        $(this).addClass('active');

        versionCurrent = selectedVersion;
        isCompareMode = versionCurrent !== versionCompared;
        createCookie('versionCourante', versionCurrent);

        // Recharger les donnees de cette version via l'API
        reloadTable(null, true);
    });

    // === SELECTION VERSION COMPARAISON ===
    $('body').on('click', '#panel-version li', function() {
        // Fermer le dropdown
        $('.trie-v-compare .select').removeClass('active');
        $('.trie-v-compare .field-list').removeClass('open');

        var selectedVersion = $(this).text().trim();

        // Si meme version que la courante, desactiver la comparaison
        if (selectedVersion === versionCurrent) {
            isCompareMode = false;
            versionCompared = selectedVersion;
            createCookie('versionComparee', versionCompared);
            $('#filter-select-version').text(selectedVersion);
            $('#panel-version li.active').removeClass('active');
            $(this).addClass('active');
            reloadTable(null, true);
            return;
        }

        isCompareMode = true;
        versionCompared = selectedVersion;
        createCookie('versionComparee', versionCompared);

        $('#filter-select-version').text(selectedVersion);
        $('#panel-version li.active').removeClass('active');
        $(this).addClass('active');

        // Charger les donnees de comparaison a la demande, puis rebuild
        reloadTable(null, true);
    });

    // === PANNEAU FILTRE ===
    $('#d-filter').on('click', function(event) {
        event.stopPropagation();
        var filterStatus = $('#p-filter').attr('data-filter');
        var newStatus = (filterStatus === 'close') ? 'open' : 'close';
        $('#p-filter').attr('data-filter', newStatus);
    });

    $('#close-filter, #icon-close').on('click', function() {
        $('#p-filter').attr('data-filter', 'close');
    });

    // Tracker l'origine du mousedown : si ça commence dans le filtre,
    // on ignore le click suivant même si la souris a glissé dehors
    // (cas : sélection de texte dans l'input part number).
    var _filterDragOrigin = false;
    $(document).on('mousedown', function(e) {
        _filterDragOrigin = !!$(e.target).closest('.pos-filter').length;
    });

    // Fermer filtre sur clic exterieur
    $(document).on('click', function(event) {
        if (_filterDragOrigin) return;
        if (!$(event.target).closest('.pos-filter').length &&
            !$(event.target).closest('#d-filter').length) {
            $('#p-filter').attr('data-filter', 'close');
        }
    });

    // Toggle sections filtre
    $('.f-box-title').on('click', function() {
        $(this).siblings('.f-box-content').toggleClass('open');
    });

    // === RECHERCHE TEMPS RÉEL — handler générique via data-filter-search ===
    $(document).on('input', '[data-filter-search]', function() {
        var key = $(this).data('filter-search');
        var q   = $(this).val();
        $('[data-filter-clear="' + key + '"]').toggle(q.length > 0);
        filterSearchText[key] = q;
        renderFilterList(key, getAvailableValues(key));
    });

    $(document).on('click', '[data-filter-clear]', function(e) {
        e.stopPropagation();
        var key = $(this).data('filter-clear');
        $('[data-filter-search="' + key + '"]').val('');
        $(this).hide();
        filterSearchText[key] = '';
        renderFilterList(key, getAvailableValues(key));
    });

    // === CASCADE sur date et onGoingContract ===
    $(document).on('change', '#dateTStart, #dateTEnd', function() {
        refreshAllFilterLists();
    });
    $(document).on('change', 'input[name="onGoingContract"]', function() {
        refreshAllFilterLists();
    });

    // === APPLIQUER FILTRE ===
    // rowPassesFilters (défini dans tableau_echeance.js) applique tous les filtres actifs
    $('#apply-filter').on('click', function() {
        if (!currentFamily || !dataByFamily[currentFamily]) return;

        var dataFilter = getCurrentVersionData().filter(function(rawLine) {
            return rowPassesFilters(rawLine, null);
        });

        var tbodySticky  = $('#tbody-sticky');
        var tbodyVX      = $('#tbody-vx');
        var tbodyCompare = $('#tbody-compare');

        rebuildTable(tbodySticky, tbodyVX, tbodyCompare, dataFilter, false, true);
    });

    // === REINITIALISER FILTRE ===
    $('#reset-filter').on('click', function() {
        // Vider tous les Sets (FILTER_CONFIG) et les textes de recherche
        Object.keys(FILTER_CONFIG).forEach(function(k) {
            FILTER_CONFIG[k].checkedSet.clear();
            filterSearchText[k] = '';
        });

        $("input[type='checkbox']").prop('checked', false);
        $("#dateTStart").val('');
        $("#dateTEnd").val('');
        $('[data-filter-search]').val('');
        $('[data-filter-clear]').hide();

        if (!currentFamily || !dataByFamily[currentFamily]) return;

        var cache = dataByFamily[currentFamily]._htmlCache;

        if (cache) {
            // Restauration rapide depuis le cache HTML : on réinjecte directement
            // le innerHTML sauvegardé lors de la dernière construction complète,
            // sans recalculer toutes les lignes. L'event delegation étant déjà en
            // place, aucun rebinding n'est nécessaire.
            var tbodySticky  = $('#tbody-sticky');
            var tbodyVX      = $('#tbody-vx');
            var tbodyCompare = $('#tbody-compare');

            tbodySticky[0].innerHTML  = cache.sticky;
            tbodyVX[0].innerHTML      = cache.vx;
            tbodyCompare[0].innerHTML = cache.compare !== null ? cache.compare : '';

            updateAllBackgroundColorScenario();
            syncRowHeights();
            equalizeScrollHeights();
            refreshAllFilterLists();  // Sets vidés → affiche toutes les valeurs
        } else {
            // Fallback : reconstruction complète si le cache n'est pas disponible
            // (ex : première charge, ou famille sans données).
            var data = getCurrentVersionData();
            var tbodySticky  = $('#tbody-sticky');
            var tbodyVX      = $('#tbody-vx');
            var tbodyCompare = $('#tbody-compare');

            rebuildTable(tbodySticky, tbodyVX, tbodyCompare, data, true, true);
        }
    });

    // === CHANGEMENT VUE ECHEANCES (mois/trimestre/annee) ===
    $('#btn-group-vue-echeances input').on('change', function() {
        // Mettre a jour la variable globale
        idVueEcheancesActif = this.id;

        // Mettre a jour le champ nombre d'annees
        if (idVueEcheancesActif === 'btn-vue-mois') {
            $("#input-annees-affichees").val(parseInt(yearsToShowByMonth));
        } else if (idVueEcheancesActif === 'btn-vue-trimestre') {
            $("#input-annees-affichees").val(parseInt(yearsToShowByQuarter));
        } else if (idVueEcheancesActif === 'btn-vue-annuelle') {
            $("#input-annees-affichees").val(parseInt(yearsToShowAnnually));
        }

        // Reconstruire le tableau
        if (currentFamily && dataByFamily[currentFamily]) {
            rebuildTable($('#tbody-sticky'), $('#tbody-vx'), $('#tbody-compare'), getCurrentVersionData(), false, true);
        }
    });

    // === CHANGEMENT NOMBRE D'ANNEES ===
    $('#input-annees-affichees').on('change', function() {
        var val = parseInt($(this).val(), 10);
        if (isNaN(val) || val < 1) return;

        // Mettre a jour la variable globale correspondante
        if (idVueEcheancesActif === 'btn-vue-mois') {
            yearsToShowByMonth = val;
        } else if (idVueEcheancesActif === 'btn-vue-trimestre') {
            yearsToShowByQuarter = val;
        } else if (idVueEcheancesActif === 'btn-vue-annuelle') {
            yearsToShowAnnually = val;
        }

        // Reconstruire le tableau
        if (currentFamily && dataByFamily[currentFamily]) {
            rebuildTable($('#tbody-sticky'), $('#tbody-vx'), $('#tbody-compare'), getCurrentVersionData(), false, true);
        }
    });

    // === TOGGLE DIFFERENCES UNIQUEMENT ===
    $('#toggle-diff-only').on('change', function() {
        showOnlyDifferences = $(this).is(':checked');
        if (currentFamily && dataByFamily[currentFamily]) {
            rebuildTable($('#tbody-sticky'), $('#tbody-vx'), $('#tbody-compare'), getCurrentVersionData(), false, false);
        }
    });
});
