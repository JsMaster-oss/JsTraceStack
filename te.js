// fichier : tableau_echeance.js
// ========================================
// Tableau split-view avec sticky columns et scroll synchronise
// Structure donnees : { ligne_premier_id, current: { ...champs... }, compare: null }
// current contient : echeances_tab (arrays 0-11), echeance, et tous les champs ligne
// Colonnes sticky : Produit, Part number, Contrat, WONO, T0, Quantite, Scenario
// Colonnes scrollables : <annee, echeances, >annee+5, Total retenu, Total non retenu,
//                         Commentaire, Entite, Commentaire CSS
// ========================================

let dataByFamily = {};

let currentFamily = '';
let versionCurrent = 'En cours';
let versionCompared = 'En cours';
let isCompareMode = false;

// Numero de la derniere requete de chargement de version emise : sert a ignorer
// les reponses arrivees dans le desordre (clic rapide sur famille / version).
let versionRequestSeq = 0;

const currentYear = new Date().getFullYear();
const AFTER_YEAR_LIMIT = currentYear + 5;

let yearsToShowByMonth = 4;
let yearsToShowByQuarter = 6;
let yearsToShowAnnually = 11;

let idVueEcheancesActif = 'btn-vue-trimestre';

let totalsByProduct = {};
let totalsByActivity = {};
let totalsByProductCompare = {};
let totalsByActivityCompare = {};

let extraTotalsByProduct = {};
let extraTotalsByActivity = {};
let extraTotalsByProductCompare = {};
let extraTotalsByActivityCompare = {};

const STICKY_COL_COUNT = 7;
var hiddenStickyColumns = [];
// allReferenceValues supprimé — remplacé par FILTER_CONFIG.reference.allValues

const EXTRA_HEADERS_AFTER = [
    '>' + AFTER_YEAR_LIMIT,
    'Total retenu',
    'Total non retenu',
    'Commentaire',
    'Entité',
    'Commentaire CSS'
];

// Noms des mois en français
const MONTH_NAMES = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];

// Compteur global pour identifier les lignes de totaux
var totalRowCounter = 0;

// ========================================
// UTILITAIRES
// ========================================

function getNumYears() {
    switch (idVueEcheancesActif) {
        case 'btn-vue-mois': return yearsToShowByMonth;
        case 'btn-vue-trimestre': return yearsToShowByQuarter;
        case 'btn-vue-annuelle': return yearsToShowAnnually;
        default: return 0;
    }
}

function getPeriodCount() {
    if (idVueEcheancesActif === 'btn-vue-mois') return 12;
    if (idVueEcheancesActif === 'btn-vue-trimestre') return 4;
    if (idVueEcheancesActif === 'btn-vue-annuelle') return 1;
    return 0;
}

function isYearStart(periodIndex) {
    var periodsPerYear = getPeriodCount();
    if (periodsPerYear === 0) return false;
    return periodIndex % periodsPerYear === 0;
}

function normalizeSortValue(val) {
    if (val == null || val === '') return '';
    return String(val).toLowerCase().trim();
}

function createTd(opts) {
    var o = opts || {};
    var value = o.value !== undefined ? o.value : '';
    var cls = o.cls || '';
    var link = o.link || null;
    var diffType = o.diffType || false;
    var diffClass = o.diffClass || '';

    // DOM natif : ~5-10x plus rapide que $('<td></td>')
    var td = document.createElement('td');
    if (cls) td.className = cls;

    if (link) {
        var a = document.createElement('a');
        a.href = link;
        a.textContent = (value === null || value === undefined) ? '' : value;
        td.appendChild(a);
    } else {
        td.textContent = (value === null || value === undefined) ? '' : String(value);
    }

    if (diffType && diffClass) td.classList.add(diffClass);
    return td;
}

/**
 * Determine le type de difference entre deux valeurs
 */
function getDiffClass(val1, val2) {
    var s1 = (val1 === '' || val1 == null) ? '' : String(val1);
    var s2 = (val2 === '' || val2 == null) ? '' : String(val2);

    if (s1 === s2) return '';

    var v1 = s1 === '' ? null : parseFloat(s1);
    var v2 = s2 === '' ? null : parseFloat(s2);

    if (v1 === null && v2 === null) return '';
    if (v1 === null && v2 !== null) return 'diff-addition';
    if (v1 !== null && v2 === null) return 'diff-deletion';

    if (!isNaN(v1) && !isNaN(v2)) {
        if (v1 < v2) return 'diff-increase';
        if (v1 > v2) return 'diff-decrease';
        return '';
    }
    return 'diff-change';
}

/**
 * Extrait l'objet plat depuis un wrapper {ligne_premier_id, current, compare}
 * Si l'objet a deja la structure plate (pas de .current), le retourne tel quel
 */
function unwrapRow(row) {
    if (row && row.current) return row.current;
    return row;
}

// ========================================
// CALCULS ECHEANCES
// ========================================

function computeBeforeYear(echeanceArray) {
    var sum = 0;
    if (!echeanceArray || !Array.isArray(echeanceArray)) return '';
    echeanceArray.forEach(function(e) {
        if (e.date_mad) {
            var year = parseInt(e.date_mad.substring(0, 4), 10);
            if (year < currentYear) sum += Number(e.quantite || 0);
        }
    });
    return sum > 0 ? sum : '';
}

function computeAfterYear(echeanceArray) {
    var sum = 0;
    if (!echeanceArray || !Array.isArray(echeanceArray)) return '';
    echeanceArray.forEach(function(e) {
        if (e.date_mad) {
            var year = parseInt(e.date_mad.substring(0, 4), 10);
            if (year > AFTER_YEAR_LIMIT) sum += Number(e.quantite || 0);
        }
    });
    return sum > 0 ? sum : '';
}

function mergeSchedules(base, add) {
    var result = {};
    var baseObj = base || {};
    for (var yr in baseObj) {
        result[yr] = baseObj[yr] ? baseObj[yr].slice() : [];
    }
    for (var year in (add || {})) {
        if (!result[year]) result[year] = Array(12).fill('');
        var addYear = add[year] || [];
        for (var m = 0; m < 12; m++) {
            var addVal = addYear[m] !== undefined ? addYear[m] : '';
            if (addVal !== '' && addVal !== null) {
                if (result[year][m] === '' || result[year][m] === null) {
                    result[year][m] = Number(addVal);
                } else {
                    result[year][m] = Number(result[year][m]) + Number(addVal);
                }
            }
        }
    }
    return result;
}

function getDeadlinesForView(echeancesTab, numYears) {
    var result = [];

    if (idVueEcheancesActif === 'btn-vue-mois') {
        for (var y = 0; y < numYears; y++) {
            var year = currentYear + y;
            var yearData = echeancesTab[String(year)] || [];
            for (var m = 0; m < 12; m++) {
                var val = yearData[m] !== undefined ? yearData[m] : '';
                result.push({ year: year, month: m + 1, value: (val === '' || val === null) ? '' : val });
            }
        }
    } else if (idVueEcheancesActif === 'btn-vue-trimestre') {
        for (var y = 0; y < numYears; y++) {
            var year = currentYear + y;
            var yearData = echeancesTab[String(year)] || [];
            for (var q = 1; q <= 4; q++) {
                var sum = 0, hasValue = false;
                var start = (q - 1) * 3;
                for (var m = start; m < start + 3; m++) {
                    var v = yearData[m] !== undefined ? yearData[m] : '';
                    if (v !== '' && v !== null) { sum += Number(v); hasValue = true; }
                }
                result.push({ year: year, quarter: q, value: hasValue ? sum : '' });
            }
        }
    } else if (idVueEcheancesActif === 'btn-vue-annuelle') {
        for (var y = 0; y < numYears; y++) {
            var year = currentYear + y;
            var yearData = echeancesTab[String(year)] || [];
            var sum = 0, hasValue = false;
            for (var m = 0; m < 12; m++) {
                var v = yearData[m] !== undefined ? yearData[m] : '';
                if (v !== '' && v !== null) { sum += Number(v); hasValue = true; }
            }
            result.push({ year: year, value: hasValue ? sum : '' });
        }
    }
    return result;
}

function addToExtra(target, rowData) {
    var bv = computeBeforeYear(rowData.echeance);
    var av = computeAfterYear(rowData.echeance);
    target.before = (target.before || 0) + (bv === '' ? 0 : Number(bv));
    target.after = (target.after || 0) + (av === '' ? 0 : Number(av));
    target.quantite_totale = (target.quantite_totale || 0) + Number(rowData.quantite_totale || 0);
    target.non_retenue_totale = (target.non_retenue_totale || 0) + Number(rowData.non_retenue_totale || 0);
}

// ========================================
// GENERATION EN-TETES POUR LES 3 TABLES
// ========================================

function generateTableHeaders() {
    var numYears = getNumYears();

    generateStickyHeaders();
    generateScrollableHeaders($('#thead-vx'), numYears, versionCurrent.toUpperCase(), 'header-group-vx');

    if (isCompareMode) {
        generateScrollableHeaders($('#thead-compare'), numYears, versionCompared.toUpperCase(), 'header-group-compare');
    }
}

/**
 * Nombre de lignes de header dans les tables scrollables :
 * - Vue mois/trimestre : 3 lignes (label, annees, periodes)
 * - Vue annuelle : 2 lignes (label, annees)
 */
function getHeaderRowCount() {
    if (idVueEcheancesActif === 'btn-vue-mois' || idVueEcheancesActif === 'btn-vue-trimestre') return 3;
    return 2;
}

function generateStickyHeaders() {
    var thead = $('#thead-sticky');
    thead.empty();

    var fixedHeaders = ['Produit', 'Part number', 'Contrat', 'WONO', 'T0', 'Quantité', 'Scénario'];
    var headerRows = getHeaderRowCount();

    // Ligne 1 : vide pour aligner avec le label scrollable (VX / VERSION)
    var emptyRow = $('<tr class="titre-tableau"></tr>');
    emptyRow.append($('<th colspan="' + STICKY_COL_COUNT + '" class="header-empty-sticky"></th>'));
    thead.append(emptyRow);

    // Ligne 2 : noms des colonnes (avec rowspan en vue mois/trimestre
    // pour couvrir la ligne annees + la ligne periodes, comme les extra headers)
    var headerRow = $('<tr class="titre-tableau"></tr>');
    var rowspan = headerRows - 1; // 2 en mois/trimestre, 1 en annuel
    fixedHeaders.forEach(function(label, idx) {
        var th = $('<th></th>').text(label);
        if (rowspan > 1) th.attr('rowspan', rowspan);
        th.attr('data-col-idx', idx);
        th.attr('title', 'Cliquer pour masquer / afficher');
        (function(colIdx) {
            th.on('click', function() { toggleStickyColumn(colIdx); });
        })(idx);
        headerRow.append(th);
    });
    thead.append(headerRow);

    // En vue mois/trimestre, ajouter un <tr> vide pour la ligne des periodes
    // (les cellules sont couvertes par le rowspan ci-dessus)
    if (headerRows === 3) {
        thead.append($('<tr class="titre-tableau"></tr>'));
    }
}

function toggleStickyColumn(colIdx) {
    var i = hiddenStickyColumns.indexOf(colIdx);
    if (i === -1) {
        hiddenStickyColumns.push(colIdx);
    } else {
        hiddenStickyColumns.splice(i, 1);
    }
    applyHiddenStickyColumns();
    syncRowHeights();
}

function applyHiddenStickyColumns() {
    var table = document.getElementById('table-sticky');
    if (!table) return;

    // En-tetes nommes : toggle la classe col-sticky-collapsed
    table.querySelectorAll('th[data-col-idx]').forEach(function(th) {
        var idx = parseInt(th.getAttribute('data-col-idx'));
        th.classList.toggle('col-sticky-collapsed', hiddenStickyColumns.indexOf(idx) !== -1);
    });

    // Cellules de donnees : ignorer les lignes de totaux (1 seul td avec colspan=7)
    table.querySelectorAll('tbody tr').forEach(function(row) {
        var cells = row.querySelectorAll('td');
        if (cells.length !== STICKY_COL_COUNT) return;
        for (var c = 0; c < cells.length; c++) {
            cells[c].classList.toggle('col-sticky-collapsed', hiddenStickyColumns.indexOf(c) !== -1);
        }
    });
}

function generateScrollableHeaders(thead, numYears, label, labelClass) {
    thead.empty();

    var periodsPerYear = getPeriodCount();
    var periodsCount = periodsPerYear * numYears;
    var totalCols = 1 + periodsCount + EXTRA_HEADERS_AFTER.length;

    // === Ligne 1 : Label (VX / Version) avec div sticky inside ===
    var labelRow = $('<tr class="titre-tableau"></tr>');
    var labelTh = $('<th colspan="' + totalCols + '" class="' + labelClass + '"></th>');
    var labelDiv = $('<div class="label-sticky-inner"></div>').text(label);
    labelTh.append(labelDiv);
    labelRow.append(labelTh);
    thead.append(labelRow);

    if (idVueEcheancesActif === 'btn-vue-mois' || idVueEcheancesActif === 'btn-vue-trimestre') {
        // === Ligne 2 : Annees (avec colspan sur les periodes) ===
        var yearRow = $('<tr class="titre-tableau"></tr>');
        yearRow.append($('<th class="header-year-group" rowspan="1"></th>').text('<' + currentYear));
        for (var y = 0; y < numYears; y++) {
            var yearTh = $('<th class="header-year-group" colspan="' + periodsPerYear + '"></th>').text(String(currentYear + y));
            if (y === 0) yearTh.addClass('year-separator');
            yearRow.append(yearTh);
        }
        // Extra headers sur cette ligne (rowspan 2 pour couvrir la ligne des periodes)
        EXTRA_HEADERS_AFTER.forEach(function(h) {
            yearRow.append($('<th rowspan="2" class="header-extra-col"></th>').text(h));
        });
        thead.append(yearRow);

        // === Ligne 3 : Periodes (mois ou trimestres) ===
        var periodRow = $('<tr class="titre-tableau"></tr>');
        periodRow.append($('<th></th>')); // Vide sous "<annee"
        for (var y = 0; y < numYears; y++) {
            appendPeriodSubHeaders(periodRow);
        }
        // Pas d'extra ici, ils sont en rowspan=2 depuis la ligne 2
        thead.append(periodRow);

    } else {
        // === Vue annuelle : Ligne 2 = annees directement ===
        var headerRow = $('<tr class="titre-tableau"></tr>');
        headerRow.append($('<th></th>').text('<' + currentYear));
        for (var y = 0; y < numYears; y++) {
            var th = $('<th></th>').text(String(currentYear + y));
            th.addClass('year-separator');
            headerRow.append(th);
        }
        EXTRA_HEADERS_AFTER.forEach(function(h) {
            headerRow.append($('<th></th>').text(h));
        });
        thead.append(headerRow);
    }
}

/**
 * Sous-headers de periodes (mois ou trimestres) pour une annee donnee
 */
function appendPeriodSubHeaders(row) {
    if (idVueEcheancesActif === 'btn-vue-mois') {
        for (var m = 0; m < 12; m++) {
            var th = $('<th></th>').text(MONTH_NAMES[m]);
            if (m === 0) th.addClass('year-separator');
            row.append(th);
        }
    } else if (idVueEcheancesActif === 'btn-vue-trimestre') {
        for (var q = 1; q <= 4; q++) {
            var th = $('<th></th>').text('Q' + q);
            if (q === 1) th.addClass('year-separator');
            row.append(th);
        }
    }
}

// ========================================
// CONSTRUCTION CELLULES FACTORISEES
// ========================================

/**
 * Construit les cellules d'echeances (before + periodes + after) dans un <tr>
 */
function buildScheduleCellsRow(tr, deadlines, beforeValue, afterValue, rowIdx, cellClass, compareDeadlines, compareBefore, compareAfter) {
    var hasCmp = compareDeadlines !== null;

    // <annee
    var diffB = hasCmp ? getDiffClass(compareBefore, beforeValue) : '';
    var tdB = createTd({ value: beforeValue, cls: 'period-cell ' + cellClass, diffType: diffB !== '', diffClass: diffB });
    tdB.setAttribute('data-col-type', 'before');
    tdB.setAttribute('data-row-index', rowIdx);
    tr.append(tdB);

    // Periodes
    deadlines.forEach(function(dl, idx) {
        var cmpVal = hasCmp && compareDeadlines[idx] ? compareDeadlines[idx].value : '';
        var diff = hasCmp ? getDiffClass(cmpVal, dl.value) : '';
        var td = createTd({ value: dl.value === '' ? '' : dl.value, cls: 'period-cell ' + cellClass, diffType: diff !== '', diffClass: diff });
        td.setAttribute('data-period-index', idx);
        td.setAttribute('data-row-index', rowIdx);
        if (isYearStart(idx)) td.classList.add('year-separator');
        tr.append(td);
    });

    // >annee+5
    var diffA = hasCmp ? getDiffClass(compareAfter, afterValue) : '';
    var tdA = createTd({ value: afterValue, cls: 'period-cell ' + cellClass, diffType: diffA !== '', diffClass: diffA });
    tdA.setAttribute('data-col-type', 'after');
    tdA.setAttribute('data-row-index', rowIdx);
    tr.append(tdA);
}

/**
 * Construit les cellules extra dans un <tr>
 */
function buildExtraCellsRow(tr, values, rowIdx, compareValues) {
    var hasCmp = compareValues !== null;
    values.forEach(function(val, i) {
        var cmpVal = hasCmp ? compareValues[i] : null;
        var diff = hasCmp ? getDiffClass(cmpVal, val) : '';
        var td = createTd({ value: val, cls: 'extra-cell', diffType: diff !== '', diffClass: diff });
        td.setAttribute('data-extra-index', i);
        td.setAttribute('data-row-index', rowIdx);
        tr.append(td);
    });
}

/**
 * Construit les cellules d'echeances pour une ligne de totaux
 */
function buildTotalScheduleCellsRow(tr, deadlines, beforeValue, afterValue, totalRowIdx, compareDeadlines, compareBefore, compareAfter) {
    var hasCmp = compareDeadlines !== null;

    var diffB = hasCmp ? getDiffClass(compareBefore || '', beforeValue || '') : '';
    var tdB = createTd({ value: beforeValue || '', cls: 'total-cell', diffType: diffB !== '', diffClass: diffB });
    tdB.setAttribute('data-total-col-type', 'before');
    tdB.setAttribute('data-total-row-index', totalRowIdx);
    tr.append(tdB);

    deadlines.forEach(function(dl, idx) {
        var cmpVal = hasCmp && compareDeadlines[idx] ? compareDeadlines[idx].value : '';
        var diff = hasCmp ? getDiffClass(cmpVal, dl.value) : '';
        var td = createTd({ value: dl.value === '' ? '' : dl.value, cls: 'total-cell', diffType: diff !== '', diffClass: diff });
        td.setAttribute('data-total-period-index', idx);
        td.setAttribute('data-total-row-index', totalRowIdx);
        if (isYearStart(idx)) td.classList.add('year-separator');
        tr.append(td);
    });

    var diffA = hasCmp ? getDiffClass(compareAfter || '', afterValue || '') : '';
    var tdA = createTd({ value: afterValue || '', cls: 'total-cell', diffType: diffA !== '', diffClass: diffA });
    tdA.setAttribute('data-total-col-type', 'after');
    tdA.setAttribute('data-total-row-index', totalRowIdx);
    tr.append(tdA);
}

/**
 * Construit les cellules extra pour une ligne de totaux
 */
function buildTotalExtraCellsRow(tr, values, totalRowIdx, compareValues) {
    var hasCmp = compareValues !== null;
    values.forEach(function(val, i) {
        var cmpVal = hasCmp ? compareValues[i] : null;
        var diff = hasCmp ? getDiffClass(cmpVal, val) : '';
        var td = createTd({ value: val, cls: 'total-cell', diffType: diff !== '', diffClass: diff });
        td.setAttribute('data-total-extra-index', i);
        td.setAttribute('data-total-row-index', totalRowIdx);
        tr.append(td);
    });
}

// ========================================
// CONSTRUCTION DES LIGNES POUR LES 3 TABLES
// ========================================

function isDifferent(val1, val2) {
    if (val1 === val2) return false;
    if ((val1 || '') === '' && (val2 || '') === '') return false;
    return String(val1) !== String(val2);
}

/**
 * Construit les lignes pour un produit présent dans la version comparée
 * dont le produit a changé dans VX → traité comme une suppression.
 * - Sticky + Compare : données comparée en rouge (ligne supprimée de VX)
 * - VX : cellules vides
 */
function buildDeletedCompareRow(compareData, rowIdx) {
    var trSticky = $('<tr class="ligne compare-row-missing"></tr>');
    trSticky.attr('data-row-index', rowIdx);
    trSticky.attr('data-ligne-premier-id', compareData.ligne_premier_id || '');

    var quantiteDisplay = Number(compareData.quantite_totale || 0) + Number(compareData.non_retenue_totale || 0);
    var wonoLink = (compareData.version_label === 'En cours' && compareData.id_contrat)
        ? linkToModifierContrat + compareData.id_contrat + '/' + compareData.id_famille
        : null;
    var fixedFields = [
        { key: 'produit',    value: compareData.produit },
        { key: 'reference',  value: compareData.reference },
        { key: 'libelle',    value: compareData.libelle },
        { key: 'num_wono',   value: compareData.num_wono, link: wonoLink },
        { key: 't0',         value: compareData.t0 },
        { key: 'quantite',   value: quantiteDisplay },
        { key: 'scenario',   value: compareData.scenario }
    ];
    fixedFields.forEach(function(field) {
        var td = createTd({ value: field.value || '', cls: 'sticky-column', link: field.link || null });
        td.setAttribute('data-field', field.key);
        trSticky.append(td);
    });

    var trVX = $('<tr class="ligne vx-row-missing"></tr>');
    trVX.attr('data-row-index', rowIdx);
    var numYears = getNumYears();
    buildScheduleCellsRow(trVX, getDeadlinesForView({}, numYears), '', '', rowIdx, 'period-cell-vx', null, null, null);
    buildExtraCellsRow(trVX, ['', '', '', '', ''], rowIdx, null);

    var trCompare = $('<tr class="ligne"></tr>');
    trCompare.attr('data-row-index', rowIdx);
    var deadlines = getDeadlinesForView(compareData.echeances_tab || {}, numYears);
    buildScheduleCellsRow(trCompare, deadlines, computeBeforeYear(compareData.echeance), computeAfterYear(compareData.echeance), rowIdx, 'period-cell-compare', null, null, null);
    buildExtraCellsRow(trCompare, [
        compareData.quantite_totale || '', compareData.non_retenue_totale || '',
        compareData.commentaire_prod || '', compareData.entite || '', compareData.commentaire_css || ''
    ], rowIdx, null);

    return { trSticky: trSticky, trVX: trVX, trCompare: trCompare, hasAnyDifference: true };
}

/**
 * Construit les lignes pour un produit présent uniquement dans VX (nouveau produit).
 * - Sticky + VX : données VX en vert (ajout)
 * - Compare : cellules vides
 */
function buildCreatedVXRow(rowData, rowIdx) {
    var trSticky = $('<tr class="ligne sticky-row-added"></tr>');
    trSticky.attr('data-row-index', rowIdx);
    trSticky.attr('data-ligne-premier-id', rowData.ligne_premier_id || '');

    var quantiteDisplay = Number(rowData.quantite_totale || 0) + Number(rowData.non_retenue_totale || 0);
    var wonoLink = (rowData.id_contrat) ? linkToModifierContrat + rowData.id_contrat + '/' + rowData.id_famille : null;
    var fixedFields = [
        { key: 'produit',   value: rowData.produit },
        { key: 'reference', value: rowData.reference },
        { key: 'libelle',   value: rowData.libelle, link: linkToModifierContrat + rowData.id_contrat + '/' + rowData.id_famille },
        { key: 'num_wono',  value: rowData.num_wono },
        { key: 't0',        value: rowData.t0 },
        { key: 'quantite',  value: quantiteDisplay },
        { key: 'scenario',  value: rowData.scenario }
    ];
    fixedFields.forEach(function(field) {
        var td = createTd({ value: field.value || '', cls: 'sticky-column', link: field.link || null });
        td.setAttribute('data-field', field.key);
        trSticky.append(td);
    });

    var numYears = getNumYears();
    var deadlinesVX = getDeadlinesForView(rowData.echeances_tab || {}, numYears);
    var trVX = $('<tr class="ligne vx-row-added"></tr>');
    trVX.attr('data-row-index', rowIdx);
    buildScheduleCellsRow(trVX, deadlinesVX, computeBeforeYear(rowData.echeance), computeAfterYear(rowData.echeance), rowIdx, 'period-cell-vx', null, null, null);
    buildExtraCellsRow(trVX, [
        rowData.quantite_totale || '', rowData.non_retenue_totale || '',
        rowData.commentaire_prod || '', rowData.entite || '', rowData.commentaire_css || ''
    ], rowIdx, null);

    var trCompare = $('<tr class="ligne"></tr>');
    trCompare.attr('data-row-index', rowIdx);
    buildScheduleCellsRow(trCompare, getDeadlinesForView({}, numYears), '', '', rowIdx, 'period-cell-compare', null, null, null);
    buildExtraCellsRow(trCompare, ['', '', '', '', ''], rowIdx, null);

    return { trSticky: trSticky, trVX: trVX, trCompare: trCompare, hasAnyDifference: true };
}

function buildTableRows(rowData, compareData) {
    var hasDiff = compareData !== null && compareData !== undefined;
    var hasAnyDifference = false;

    // === TABLE STICKY ===
    var trSticky = $('<tr class="ligne"></tr>');
    trSticky.attr('data-contract-id', rowData.id_contrat || '');
    trSticky.attr('data-row-index', rowData._rowIndex || 0);
    trSticky.attr('data-ligne-premier-id', rowData.ligne_premier_id || '');

    var quantiteDisplay = Number(rowData.quantite_totale || 0) + Number(rowData.non_retenue_totale || 0);

    var fixedFields = [
        { key: 'produit', value: rowData.produit },
        { key: 'reference', value: rowData.reference },
        { key: 'libelle', value: rowData.libelle, link: linkToModifierContrat + rowData.id_contrat + '/' + rowData.id_famille },
        { key: 'num_wono', value: rowData.num_wono },
        { key: 't0', value: rowData.t0 },
        { key: 'quantite', value: quantiteDisplay },
        { key: 'scenario', value: rowData.scenario }
    ];

    fixedFields.forEach(function(field) {
        var compareValue = null;
        if (hasDiff) {
            if (field.key === 'quantite') {
                compareValue = Number(compareData.quantite_totale || 0) + Number(compareData.non_retenue_totale || 0);
            } else {
                compareValue = compareData[field.key];
            }
        }
        var diffType = hasDiff && isDifferent(field.value, compareValue);
        if (diffType) hasAnyDifference = true;

        var td = createTd({
            value: field.value || '',
            link: field.link || null,
            diffType: diffType,
            diffClass: diffType ? 'cell-has-diff' : ''
        });
        td.setAttribute('data-field', field.key);
        trSticky.append(td);
    });

    // === TABLE VX ===
    var trVX = $('<tr class="ligne"></tr>');
    trVX.attr('data-row-index', rowData._rowIndex || 0);

    var numYears = getNumYears();
    var deadlinesVX = getDeadlinesForView(rowData.echeances_tab || {}, numYears);
    var beforeVX = computeBeforeYear(rowData.echeance);
    var afterVX = computeAfterYear(rowData.echeance);

    var extraVXValues = [
        rowData.quantite_totale || '',
        rowData.non_retenue_totale || '',
        rowData.commentaire_prod || '',
        rowData.entite || '',
        rowData.commentaire_css || ''
    ];

    // Pre-calculer les donnees compare (necessaire pour les diff sur VX)
    var deadlinesCompare = null, beforeCompare = '', afterCompare = '';
    var extraCompareValues = null;
    if (isCompareMode && hasDiff) {
        deadlinesCompare = getDeadlinesForView(compareData.echeances_tab || {}, numYears);
        beforeCompare = computeBeforeYear(compareData.echeance);
        afterCompare = computeAfterYear(compareData.echeance);
        extraCompareValues = [
            compareData.quantite_totale || '',
            compareData.non_retenue_totale || '',
            compareData.commentaire_prod || '',
            compareData.entite || '',
            compareData.commentaire_css || ''
        ];

        // Verifier les diffs
        if (getDiffClass(beforeVX, beforeCompare) !== '') hasAnyDifference = true;
        if (getDiffClass(afterVX, afterCompare) !== '') hasAnyDifference = true;
        deadlinesCompare.forEach(function(dl, idx) {
            var vxVal = deadlinesVX[idx] ? deadlinesVX[idx].value : '';
            if (getDiffClass(vxVal, dl.value) !== '') hasAnyDifference = true;
        });
        extraCompareValues.forEach(function(val, i) {
            if (getDiffClass(extraVXValues[i], val) !== '') hasAnyDifference = true;
        });
    }

    // VX : couleurs de diff affichees sur la Référence
    buildScheduleCellsRow(trVX, deadlinesVX, beforeVX, afterVX, rowData._rowIndex || 0, 'period-cell-vx', deadlinesCompare, beforeCompare, afterCompare);
    buildExtraCellsRow(trVX, extraVXValues, rowData._rowIndex || 0, extraCompareValues);

    // === TABLE COMPARE ===
    var trCompare = $('<tr class="ligne"></tr>');
    trCompare.attr('data-row-index', rowData._rowIndex || 0);

    if (isCompareMode && hasDiff) {
        // Compare : donnees brutes, sans couleur de diff
        buildScheduleCellsRow(trCompare, deadlinesCompare, beforeCompare, afterCompare, rowData._rowIndex || 0, 'period-cell-compare', null, null, null);
        buildExtraCellsRow(trCompare, extraCompareValues, rowData._rowIndex || 0, null);
    } else if (isCompareMode) {
        buildScheduleCellsRow(trCompare, getDeadlinesForView({}, numYears), '', '', rowData._rowIndex || 0, 'period-cell-compare', null, null, null);
        buildExtraCellsRow(trCompare, ['', '', '', '', ''], rowData._rowIndex || 0, null);
    }

    if (hasAnyDifference) {
        trSticky.addClass('row-has-diff');
        trVX.addClass('row-has-diff');
    }

    return { trSticky: trSticky, trVX: trVX, trCompare: trCompare, hasAnyDifference: hasAnyDifference };
}

/**
 * Construit les lignes pour une entrée présente dans la comparaison mais absente de la Référence.
 * → Ligne supprimée de la Référence : rouge partout.
 * - Sticky : données de la version comparée (fond rouge)
 * - VX/Référence : cellules vides (fond rouge clair)
 * - Compare : données réelles (fond rouge)
 */
function buildCompareOnlyRow(compareData) {
    var rowIdx = compareData._rowIndex || 0;

    // === STICKY ===
    var trSticky = $('<tr class="ligne sticky-row-missing"></tr>');
    trSticky.attr('data-row-index', rowIdx);
    trSticky.attr('data-ligne-premier-id', compareData.ligne_premier_id || '');

    var quantiteDisplay = Number(compareData.quantite_totale || 0) + Number(compareData.non_retenue_totale || 0);

    var fixedFields = [
        { key: 'produit', value: compareData.produit },
        { key: 'reference', value: compareData.reference },
        { key: 'libelle', value: compareData.libelle },
        { key: 'num_wono', value: compareData.num_wono },
        { key: 't0', value: compareData.t0 },
        { key: 'quantite', value: quantiteDisplay },
        { key: 'scenario', value: compareData.scenario }
    ];
    fixedFields.forEach(function(field) {
        var td = createTd({ value: field.value || '', cls: 'sticky-column' });
        td.setAttribute('data-field', field.key);
        trSticky.append(td);
    });

    // === VX/Référence : cellules vides (ligne absente de la Référence) ===
    var trVX = $('<tr class="ligne vx-row-missing"></tr>');
    trVX.attr('data-row-index', rowIdx);

    var numYears = getNumYears();
    buildScheduleCellsRow(trVX, getDeadlinesForView({}, numYears), '', '', rowIdx, 'period-cell-vx', null, null, null);
    buildExtraCellsRow(trVX, ['', '', '', '', ''], rowIdx, null);

    // === Compare : données réelles, sans couleur ===
    var trCompare = $('<tr class="ligne"></tr>');
    trCompare.attr('data-row-index', rowIdx);

    var deadlinesCompare = getDeadlinesForView(compareData.echeances_tab || {}, numYears);
    var beforeCompare = computeBeforeYear(compareData.echeance);
    var afterCompare = computeAfterYear(compareData.echeance);
    buildScheduleCellsRow(trCompare, deadlinesCompare, beforeCompare, afterCompare, rowIdx, 'period-cell-compare', null, null, null);

    var extraCompareValues = [
        compareData.quantite_totale || '',
        compareData.non_retenue_totale || '',
        compareData.commentaire_prod || '',
        compareData.entite || '',
        compareData.commentaire_css || ''
    ];
    buildExtraCellsRow(trCompare, extraCompareValues, rowIdx, null);

    return { trSticky: trSticky, trVX: trVX, trCompare: trCompare, hasAnyDifference: true };
}

// ========================================
// SURBRILLANCE DES CELLULES
// ========================================

function highlightCorrespondingCells(clickedCell) {
    var periodIdx = clickedCell.attr('data-period-index');
    var rowIdx = clickedCell.attr('data-row-index');
    var colType = clickedCell.attr('data-col-type');
    var extraIdx = clickedCell.attr('data-extra-index');
    var totalRowIdx = clickedCell.attr('data-total-row-index');

    $('.period-cell, .extra-cell, .total-cell').removeClass('highlighted');
    $('.ligne, .ligne-activite-total, .ligne-produit-total').removeClass('row-highlighted');

    if (periodIdx !== undefined && rowIdx !== undefined) {
        $('[data-period-index="' + periodIdx + '"][data-row-index="' + rowIdx + '"]').addClass('highlighted');
        $('tr[data-row-index="' + rowIdx + '"]').addClass('row-highlighted');
    } else if (colType !== undefined && rowIdx !== undefined) {
        $('[data-col-type="' + colType + '"][data-row-index="' + rowIdx + '"]').addClass('highlighted');
        $('tr[data-row-index="' + rowIdx + '"]').addClass('row-highlighted');
    } else if (extraIdx !== undefined && rowIdx !== undefined) {
        $('[data-extra-index="' + extraIdx + '"][data-row-index="' + rowIdx + '"]').addClass('highlighted');
        $('tr[data-row-index="' + rowIdx + '"]').addClass('row-highlighted');
    } else if (totalRowIdx !== undefined) {
        var totalPeriodIdx = clickedCell.attr('data-total-period-index');
        var totalColType = clickedCell.attr('data-total-col-type');
        var totalExtraIdx = clickedCell.attr('data-total-extra-index');

        if (totalPeriodIdx !== undefined) {
            $('[data-total-period-index="' + totalPeriodIdx + '"][data-total-row-index="' + totalRowIdx + '"]').addClass('highlighted');
        } else if (totalColType !== undefined) {
            $('[data-total-col-type="' + totalColType + '"][data-total-row-index="' + totalRowIdx + '"]').addClass('highlighted');
        } else if (totalExtraIdx !== undefined) {
            $('[data-total-extra-index="' + totalExtraIdx + '"][data-total-row-index="' + totalRowIdx + '"]').addClass('highlighted');
        }
        $('tr[data-total-row-index="' + totalRowIdx + '"]').addClass('row-highlighted');
    }
}

// ========================================
// LIGNES DE TOTAUX (factorisee)
// ========================================

/**
 * Construit les lignes de totaux (activite ou produit)
 * @param {string} label - Ex: "Total Développement" ou "Total Smartphone Pro"
 * @param {string} cssClass - 'ligne-activite-total' ou 'ligne ligne-produit-total'
 * @param {string} idPrefix - 'activity' ou 'product'
 * @param {Object} scheduleVX - echeances_tab cumulees VX
 * @param {Object|null} scheduleCompare - echeances_tab cumulees Compare
 * @param {Object} extrasVX - extras cumules VX
 * @param {Object|null} extrasCompare - extras cumules Compare
 */
function buildTotalRows(label, cssClass, idPrefix, scheduleVX, scheduleCompare, extrasVX, extrasCompare) {
    var numYears = getNumYears();
    var totalScrollCols = 1 + getPeriodCount() * numYears + EXTRA_HEADERS_AFTER.length;
    var totalRowIdx = idPrefix + '-' + (totalRowCounter++);

    // STICKY
    var trSticky = $('<tr class="' + cssClass + '"></tr>');
    trSticky.attr('data-total-row-index', totalRowIdx);
    var tdS = createTd({ value: label });
    tdS.setAttribute('colspan', STICKY_COL_COUNT);
    trSticky.append(tdS);

    // VX
    var trVX = $('<tr class="' + cssClass + '"></tr>');
    trVX.attr('data-total-row-index', totalRowIdx);

    var dlVX = getDeadlinesForView(scheduleVX, numYears);
    var extraVXVals = [extrasVX.quantite_totale || '', extrasVX.non_retenue_totale || '', '', '', ''];

    // COMPARE
    var trCompare = $('<tr class="' + cssClass + '"></tr>');
    trCompare.attr('data-total-row-index', totalRowIdx);

    if (isCompareMode && extrasCompare) {
        var dlC = getDeadlinesForView(scheduleCompare || {}, numYears);
        var extraCVals = [extrasCompare.quantite_totale || '', extrasCompare.non_retenue_totale || '', '', '', ''];

        // VX : diff affiches sur la Référence
        buildTotalScheduleCellsRow(trVX, dlVX, extrasVX.before || '', extrasVX.after || '', totalRowIdx, dlC, extrasCompare.before || '', extrasCompare.after || '');
        buildTotalExtraCellsRow(trVX, extraVXVals, totalRowIdx, extraCVals);

        // Compare : donnees brutes, sans couleur de diff
        buildTotalScheduleCellsRow(trCompare, dlC, extrasCompare.before || '', extrasCompare.after || '', totalRowIdx, null, null, null);
        buildTotalExtraCellsRow(trCompare, extraCVals, totalRowIdx, null);
    } else {
        buildTotalScheduleCellsRow(trVX, dlVX, extrasVX.before || '', extrasVX.after || '', totalRowIdx, null, null, null);
        buildTotalExtraCellsRow(trVX, extraVXVals, totalRowIdx, null);

        if (isCompareMode) {
            for (var c = 0; c < totalScrollCols; c++) {
                trCompare.append(createTd({ value: '', cls: 'total-cell' }));
            }
        }
    }

    return { trSticky: trSticky, trVX: trVX, trCompare: trCompare };
}

function buildActivityTotalRows(activity, scheduleVX, scheduleCompare, extrasVX, extrasCompare) {
    return buildTotalRows('Total ' + activity, 'ligne-activite-total', 'activity', scheduleVX, scheduleCompare, extrasVX, extrasCompare);
}

function buildProductTotalRows(product, scheduleVX, scheduleCompare, extrasVX, extrasCompare) {
    return buildTotalRows('Total ' + product, 'ligne ligne-produit-total', 'product', scheduleVX, scheduleCompare, extrasVX, extrasCompare);
}

// ========================================
// FILTRES — état global et fonctions cascade
// ========================================

/**
 * Déclaration de tous les filtres checkbox.
 * hasSearch=true  → barre de recherche + limite MAX_VISIBLE
 * hasSearch=false → liste complète sans recherche
 * checkedSet      → source de vérité (pas le DOM)
 */
const FILTER_CONFIG = {
    activite:        { allValues: [], checkedSet: new Set(), hasSearch: false },
    scenario:        { allValues: [], checkedSet: new Set(), hasSearch: false },
    entite:          { allValues: [], checkedSet: new Set(), hasSearch: false },
    produit:         { allValues: [], checkedSet: new Set(), hasSearch: true  },
    pays:            { allValues: [], checkedSet: new Set(), hasSearch: true  },
    reference:       { allValues: [], checkedSet: new Set(), hasSearch: true  },
    commentaire_css: { allValues: [], checkedSet: new Set(), hasSearch: true  },
};

// Texte de recherche courant par clé (persiste à travers les refreshs de cascade)
const filterSearchText = {};

/**
 * Retourne vrai si rawRow passe TOUS les filtres actifs,
 * sauf éventuellement le filtre de la clé excludeKey (pour le calcul cascade).
 * Lit les dates et onGoingContract depuis le DOM (état courant).
 */
function rowPassesFilters(rawRow, excludeKey) {
    var line = unwrapRow(rawRow);
    if (!line || !line.activite) return false;

    // Filtres FILTER_CONFIG (Sets)
    var keys = Object.keys(FILTER_CONFIG);
    for (var i = 0; i < keys.length; i++) {
        var key = keys[i];
        if (key === excludeKey) continue;
        var cfg = FILTER_CONFIG[key];
        if (cfg.checkedSet.size > 0 && !cfg.checkedSet.has(line[key] || '')) return false;
    }

    // Filtre date T0
    var tStartVal = $('#dateTStart').val();
    var tEndVal   = $('#dateTEnd').val();
    var dateTStart = tStartVal ? new Date(tStartVal) : null;
    var dateTEnd   = tEndVal   ? new Date(tEndVal)   : null;
    if (line.t0 && (dateTStart || dateTEnd)) {
        var parts = line.t0.split('/');
        if (parts.length === 3) {
            var dateT0 = new Date(parts[2] + '-' + parts[1] + '-' + parts[0]);
            if (dateTStart && dateTStart > dateT0) return false;
            if (dateTEnd   && dateTEnd   < dateT0) return false;
        }
    }

    // Filtre contrats en cours
    if ($('input[name="onGoingContract"]:checked').length > 0 && line.echeance) {
        var today = new Date();
        var hasActive = false;
        for (var j = 0; j < line.echeance.length; j++) {
            if (new Date(line.echeance[j].date_mad) >= today) { hasActive = true; break; }
        }
        if (!hasActive) return false;
    }

    return true;
}

function hasActiveFilters() {
    var keys = Object.keys(FILTER_CONFIG);
    for (var i = 0; i < keys.length; i++) {
        if (FILTER_CONFIG[keys[i]].checkedSet.size > 0) return true;
    }
    if ($('#dateTStart').val() || $('#dateTEnd').val()) return true;
    if ($('input[name="onGoingContract"]:checked').length > 0) return true;
    return false;
}

/**
 * Calcule les valeurs disponibles pour la clé key,
 * en appliquant tous les autres filtres actifs (cascade).
 */
function getAvailableValues(key) {
    if (!currentFamily || !dataByFamily[currentFamily]) return new Set();
    var result = new Set();

    // Données version courante
    getCurrentVersionData().forEach(function(rawRow) {
        if (!rowPassesFilters(rawRow, key)) return;
        var line = unwrapRow(rawRow);
        var val = line ? (line[key] || '') : '';
        if (val) result.add(val);
    });

    return result;
}

/**
 * Rend la liste de checkboxes pour une clé.
 * - Items cochés mais devenus indisponibles : affichés grisés (f-item-unavailable).
 * - hasSearch=true : limité à MAX_VISIBLE avec overflow message.
 * - hasSearch=false : liste complète.
 */
function renderFilterList(key, availableValues) {
    var cfg       = FILTER_CONFIG[key];
    var container = $('#' + key + '-filter');
    if (!container.length || !cfg) return;

    var q = (filterSearchText[key] || '').toLowerCase().trim();

    // Candidats = valeurs disponibles + valeurs cochées (même devenues indisponibles)
    var candidates = cfg.allValues.filter(function(v) {
        return availableValues.has(v) || cfg.checkedSet.has(v);
    });
    if (q) {
        candidates = candidates.filter(function(v) { return v.toLowerCase().includes(q); });
    }

    container.empty();
    // Réinitialiser un éventuel scroll appliqué à un rendu précédent
    container.css({ maxHeight: '', overflowY: '' });

    if (candidates.length === 0) {
        container.append($('<li class="f-search-no-result"></li>').text(
            q ? 'Aucun résultat pour "' + q + '"' : 'Aucune valeur disponible'
        ));
        return;
    }

    var visible  = candidates;
    var overflow = 0;

    if (cfg.hasSearch) {
        var MAX_VISIBLE = 10;
        if (!q && cfg.checkedSet.size > 0) {
            var checkedItems   = candidates.filter(function(v) { return  cfg.checkedSet.has(v); });
            var uncheckedItems = candidates.filter(function(v) { return !cfg.checkedSet.has(v); });
            var freeSlots      = Math.max(0, MAX_VISIBLE - checkedItems.length);
            visible  = checkedItems.concat(uncheckedItems.slice(0, freeSlots));
            overflow = uncheckedItems.length - freeSlots;
        } else {
            visible  = candidates.slice(0, MAX_VISIBLE);
            overflow = candidates.length - MAX_VISIBLE;
        }
    }

    visible.forEach(function(val, idx) {
        var inputId   = key + '-' + idx;
        var isChecked = cfg.checkedSet.has(val);
        var isAvail   = availableValues.has(val);

        var li    = $('<li class="f-line f-line-checkbox"></li>');
        if (!isAvail) li.addClass('f-item-unavailable');

        var label = $('<label></label>').attr('for', inputId);
        var input = $('<input type="checkbox" class="checkFilterField" />')
            .attr('id', inputId)
            .attr('name', key)
            .val(val)
            .prop('checked', isChecked);

        // Mise à jour du Set + cascade sur changement
        (function(k, v, cfg) {
            input.on('change', function() {
                if (this.checked) cfg.checkedSet.add(v);
                else              cfg.checkedSet.delete(v);
                refreshAllFilterLists();
            });
        })(key, val, cfg);

        label.append(input).append($('<div></div>').text(val));
        li.append(label);
        container.append(li);
    });

    if (overflow > 0) {
        container.append($('<li class="f-search-overflow"></li>').text(
            overflow + ' autre' + (overflow > 1 ? 's' : '') + '… affinez la recherche'
        ));
    }

    // Scroll vertical si la liste dépasse 10 éléments (hauteur plafonnée à ~10 lignes).
    // S'ajoute au message "affinez la recherche" des filtres avec recherche, et rend
    // scrollables les filtres SANS recherche (activité, scénario, entité) aux longues listes.
    if (visible.length > 10) {
        container.css({ maxHeight: '280px', overflowY: 'auto' });
    }
}

/**
 * Rafraîchit toutes les listes de filtres avec les valeurs disponibles
 * selon les filtres actifs courants (cascade complète).
 */
function refreshAllFilterLists() {
    Object.keys(FILTER_CONFIG).forEach(function(key) {
        renderFilterList(key, getAvailableValues(key));
    });
}

/**
 * Initialise toutes les valeurs de filtres depuis les données (reset des Sets inclus).
 * Appelé uniquement lors d'un changement de famille/version ou d'un reset complet.
 */
function initFilterValues(data) {
    // Vider les Sets et les textes de recherche (nouveau contexte)
    Object.keys(FILTER_CONFIG).forEach(function(k) {
        FILTER_CONFIG[k].checkedSet.clear();
        filterSearchText[k] = '';
    });

    // Collecter toutes les valeurs présentes dans les données
    var sets = {};
    Object.keys(FILTER_CONFIG).forEach(function(k) { sets[k] = new Set(); });

    data.forEach(function(rawRow) {
        var line = unwrapRow(rawRow);
        if (!line) return;
        Object.keys(FILTER_CONFIG).forEach(function(key) {
            var val = line[key];
            if (val) sets[key].add(val);
        });
    });

    Object.keys(FILTER_CONFIG).forEach(function(key) {
        FILTER_CONFIG[key].allValues = Array.from(sets[key]).sort();
    });

    refreshAllFilterLists();
}

// ========================================
// RECONSTRUCTION DU TABLEAU
// ========================================

function rebuildTable(tbodySticky, tbodyVX, tbodyCompare, data, buildFilters, resetTotals, activeFilters) {
    if (buildFilters === undefined) buildFilters = true;
    if (resetTotals === undefined) resetTotals = true;
    if (activeFilters === undefined) activeFilters = null;


    tbodySticky.empty();
    tbodyVX.empty();
    tbodyCompare.empty();

    totalRowCounter = 0;

    // Map des donnees comparees par ligne_premier_id
    // Calculé avant createFilterTable pour inclure les lignes compare-only dans le panel filtre
    var compareDataMap = {};
    var compareOnlyItems = [];
    var familyData = dataByFamily[currentFamily];
    if (isCompareMode && familyData && familyData.compareCache) {
        var cacheKey = versionCurrent + '|' + versionCompared;
        var pairedList = familyData.compareCache[cacheKey] || [];
        pairedList.forEach(function(item) {
            var key = item.ligne_premier_id;
            if (key && item.compare) {
                if (item.current) {
                    compareDataMap[key] = item.compare;
                } else {
                    // Ligne presente uniquement dans la version comparee
                    compareOnlyItems.push(item);
                }
            }
        });
    }

    if (buildFilters) {
        initFilterValues(data.slice());
    }

    // Les en-tetes et la visibilite de la colonne comparee refletent la selection
    // courante : on les regenere AVANT le test "aucune donnee", sinon un passage
    // par une version vide laisse les en-tetes (et donc le libelle de version)
    // de l'affichage precedent.
    generateTableHeaders();

    if (isCompareMode) {
        $('#compare-scroll-container').show();
    } else {
        $('#compare-scroll-container').hide();
    }

    if ((!data || data.length === 0) && compareOnlyItems.length === 0) {
        tbodySticky.append('<tr><td colspan="' + STICKY_COL_COUNT + '">Aucune donnée disponible</td></tr>');
        tbodyVX.append('<tr><td colspan="20">Aucune donnée disponible</td></tr>');
        if (isCompareMode) {
            tbodyCompare.append('<tr><td colspan="20">Aucune donnée disponible</td></tr>');
        }
        syncRowHeights();
        return;
    }

    // Clés dont le produit OU l'activité a changé → traité comme une nouvelle ligne
    var productChangedKeys = {};
    if (isCompareMode) {
        data.forEach(function(rawRow) {
            var row = unwrapRow(rawRow);
            if (!row) return;
            var key = row.ligne_premier_id;
            var cmp = compareDataMap[key] || null;
            if (cmp && (isDifferent(row.produit, cmp.produit) || isDifferent(row.activite, cmp.activite))) {
                productChangedKeys[key] = true;
            }
        });
    }

    // Unwrap toutes les lignes et grouper par activite puis produit
    var grouped = {};
    data.forEach(function(rawRow, idx) {
        var row = unwrapRow(rawRow);
        row._rowIndex = idx;
        row.ligne_premier_id = rawRow.ligne_premier_id || row.ligne_premier_id;

        var act = row.activite || 'Autre';
        var prod = row.produit || 'Autre';
        if (!grouped[act]) grouped[act] = {};
        if (!grouped[act][prod]) grouped[act][prod] = [];
        grouped[act][prod].push(row);
    });

    // Ajouter les lignes présentes uniquement dans la version comparée.
    // Respect de TOUS les filtres : on n'affiche que celles qui correspondent à
    // chaque filtre coché (même logique que buildDeletedCompareRow), au lieu de
    // toutes les masquer dès qu'un filtre est actif.
    if (isCompareMode) {
        compareOnlyItems.forEach(function(item, idx) {
            if (hasActiveFilters() && !rowPassesFilters({ current: item.compare }, null)) return;

            var row = $.extend({}, item.compare);
            row._rowIndex = data.length + idx;
            row._isCompareOnly = true;
            row.ligne_premier_id = item.ligne_premier_id;

            var act = row.activite || 'Autre';
            var prod = row.produit || 'Autre';

            if (!grouped[act]) grouped[act] = {};
            if (!grouped[act][prod]) grouped[act][prod] = [];
            grouped[act][prod].push(row);
        });
    }

    if (isCompareMode) {

        // Injecter les lignes supprimées (produit ou activité changé) dans leur PROPRE groupe
        // (activité/produit de la version comparée), pas dans le groupe VX.
        // Cela garantit que chaque groupe produit/activité ne contient que des données homogènes
        // et que les totaux par produit/activité restent cohérents.
        data.forEach(function(rawRow) {
            var row = unwrapRow(rawRow);
            if (!row) return;
            var key = row.ligne_premier_id;
            var cmp = compareDataMap[key] || null;
            if (cmp && (isDifferent(row.produit, cmp.produit) || isDifferent(row.activite, cmp.activite))) {
                // Respect de TOUS les filtres : l'ANCIEN (côté comparé) n'est affiché
                // que s'il correspond LUI-MÊME à chaque filtre coché (produit, activité,
                // scénario, référence, entité, pays, commentaire CSS, dates, en cours).
                // → on n'affiche que ce qui est coché, même si les lignes sont liées
                //   par premier_id. Un filtre non actif ne masque rien (logique ET sur
                //   les filtres actifs uniquement).
                if (hasActiveFilters() && !rowPassesFilters({ current: cmp }, null)) return;

                var deletedRow = $.extend({}, cmp);
                deletedRow._rowIndex = 'del-' + (row._rowIndex !== undefined ? row._rowIndex : key);
                deletedRow._isDeletedProduct = true;
                deletedRow.ligne_premier_id = key;

                // Utiliser les coordonnées de la version COMPARÉE (pas VX)
                var act  = deletedRow.activite || 'Autre';
                var prod = deletedRow.produit  || 'Autre';
                if (!grouped[act]) grouped[act] = {};
                if (!grouped[act][prod]) grouped[act][prod] = [];
                grouped[act][prod].push(deletedRow);
            }
        });
    }

    // Accumuler toutes les lignes hors DOM pour un seul appendChild par tbody en fin de boucle
    var fragSticky = document.createDocumentFragment();
    var fragVX = document.createDocumentFragment();
    var fragCompare = document.createDocumentFragment();

    var sortedActivities = Object.keys(grouped).sort(function(a, b) {
        return normalizeSortValue(a).localeCompare(normalizeSortValue(b));
    });

    sortedActivities.forEach(function(activity) {
        var products = grouped[activity];
        var sortedProducts = Object.keys(products).sort(function(a, b) {
            return normalizeSortValue(a).localeCompare(normalizeSortValue(b));
        });

        // Totaux activite = somme de tous les produits de cette activite
        var actScheduleVX = {};
        var actExtrasVX = { before: 0, after: 0, quantite_totale: 0, non_retenue_totale: 0 };
        var actScheduleCompare = {};
        var actExtrasCompare = { before: 0, after: 0, quantite_totale: 0, non_retenue_totale: 0 };

        sortedProducts.forEach(function(product) {
            var rows = products[product];

            // Totaux produit = locaux a ce produit dans cette activite
            var prodScheduleVX = {};
            var prodExtrasVX = { before: 0, after: 0, quantite_totale: 0, non_retenue_totale: 0 };
            var prodScheduleCompare = {};
            var prodExtrasCompare = { before: 0, after: 0, quantite_totale: 0, non_retenue_totale: 0 };

            rows.forEach(function(rowData) {
                var key = rowData.ligne_premier_id;

                if (rowData._isDeletedProduct) {
                    // Ancien produit supprimé (produit changé) — s'affiche dans son propre groupe produit
                    var result = buildDeletedCompareRow(rowData, rowData._rowIndex);

                    fragSticky.appendChild(result.trSticky[0]);
                    fragVX.appendChild(result.trVX[0]);
                    if (isCompareMode) fragCompare.appendChild(result.trCompare[0]);

                    // Cumul côté compare uniquement
                    if (rowData.echeances_tab) {
                        prodScheduleCompare = mergeSchedules(prodScheduleCompare, rowData.echeances_tab);
                    }
                    addToExtra(prodExtrasCompare, rowData);

                } else if (rowData._isCompareOnly) {
                    // Ligne presente uniquement dans la version comparee
                    var result = buildCompareOnlyRow(rowData);

                    fragSticky.appendChild(result.trSticky[0]);
                    fragVX.appendChild(result.trVX[0]);
                    if (isCompareMode) fragCompare.appendChild(result.trCompare[0]);

                    // Cumul uniquement cote compare (pas dans VX)
                    if (rowData.echeances_tab) {
                        prodScheduleCompare = mergeSchedules(prodScheduleCompare, rowData.echeances_tab);
                    }
                    addToExtra(prodExtrasCompare, rowData);

                } else {
                    var compareData = compareDataMap[key] || null;

                    // Produit différent → la ligne supprimée est déjà dans son groupe, on affiche la VX en vert
                    // Ligne présente uniquement dans la Référence (VX) → ajout (vert)
                    if (isCompareMode && (productChangedKeys[key] || compareData === null)) {
                        var result = buildCreatedVXRow(rowData, rowData._rowIndex || 0);
                        fragSticky.appendChild(result.trSticky[0]);
                        fragVX.appendChild(result.trVX[0]);
                        fragCompare.appendChild(result.trCompare[0]);
                        if (rowData.echeances_tab) prodScheduleVX = mergeSchedules(prodScheduleVX, rowData.echeances_tab);
                        addToExtra(prodExtrasVX, rowData);
                        return;
                    }

                    var result = buildTableRows(rowData, compareData);

                    fragSticky.appendChild(result.trSticky[0]);
                    fragVX.appendChild(result.trVX[0]);
                    if (isCompareMode) fragCompare.appendChild(result.trCompare[0]);

                    // Cumul produit VX
                    if (rowData.echeances_tab) {
                        prodScheduleVX = mergeSchedules(prodScheduleVX, rowData.echeances_tab);
                    }
                    addToExtra(prodExtrasVX, rowData);

                    // Cumul produit Compare
                    if (isCompareMode && compareData) {
                        if (compareData.echeances_tab) {
                            prodScheduleCompare = mergeSchedules(prodScheduleCompare, compareData.echeances_tab);
                        }
                        addToExtra(prodExtrasCompare, compareData);
                    }
                }
            });

            // Total produit
            var prodTotals = buildProductTotalRows(
                product,
                prodScheduleVX,
                isCompareMode ? prodScheduleCompare : null,
                prodExtrasVX,
                isCompareMode ? prodExtrasCompare : null
            );
            fragSticky.appendChild(prodTotals.trSticky[0]);
            fragVX.appendChild(prodTotals.trVX[0]);
            if (isCompareMode) fragCompare.appendChild(prodTotals.trCompare[0]);

            // Cumuler dans les totaux activite
            actScheduleVX = mergeSchedules(actScheduleVX, prodScheduleVX);
            actExtrasVX.before += prodExtrasVX.before || 0;
            actExtrasVX.after += prodExtrasVX.after || 0;
            actExtrasVX.quantite_totale += prodExtrasVX.quantite_totale || 0;
            actExtrasVX.non_retenue_totale += prodExtrasVX.non_retenue_totale || 0;

            if (isCompareMode) {
                actScheduleCompare = mergeSchedules(actScheduleCompare, prodScheduleCompare);
                actExtrasCompare.before += prodExtrasCompare.before || 0;
                actExtrasCompare.after += prodExtrasCompare.after || 0;
                actExtrasCompare.quantite_totale += prodExtrasCompare.quantite_totale || 0;
                actExtrasCompare.non_retenue_totale += prodExtrasCompare.non_retenue_totale || 0;
            }
        });

        // Total activite (englobe tous les produits)
        var actTotals = buildActivityTotalRows(
            activity,
            actScheduleVX,
            isCompareMode ? actScheduleCompare : null,
            actExtrasVX,
            isCompareMode ? actExtrasCompare : null
        );
        fragSticky.appendChild(actTotals.trSticky[0]);
        fragVX.appendChild(actTotals.trVX[0]);
        if (isCompareMode) fragCompare.appendChild(actTotals.trCompare[0]);
    });

    // Flush : insertion unique dans le DOM après toute la construction
    tbodySticky[0].appendChild(fragSticky);
    tbodyVX[0].appendChild(fragVX);
    if (isCompareMode) tbodyCompare[0].appendChild(fragCompare);

    // Cache HTML : on sauvegarde uniquement lors d'une construction complète (buildFilters=true),
    // c'est-à-dire quand aucun filtre n'est actif. Cela permet au reset de restaurer
    // directement le DOM sans recalculer toutes les lignes.
    if (buildFilters && currentFamily && dataByFamily[currentFamily]) {
        dataByFamily[currentFamily]._htmlCache = {
            sticky:  tbodySticky[0].innerHTML,
            vx:      tbodyVX[0].innerHTML,
            compare: isCompareMode ? tbodyCompare[0].innerHTML : null
        };
    }

    updateAllBackgroundColorScenario();
    setupScrollSynchronization();
    syncRowHeights();
    equalizeScrollHeights();
    applyHiddenStickyColumns();
}

// ========================================
// SYNCHRONISATION DU SCROLL
// ========================================

function updateLabelPositions() {
    $('#vx-scroll-container, #compare-scroll-container').each(function() {
        var scrollLeft = $(this).scrollLeft();
        $(this).find('.label-sticky-inner').css('transform', 'translateX(' + scrollLeft + 'px)');
    });
}

function setupScrollSynchronization() {
    var stickyEl = document.querySelector('.sticky-columns-wrapper');
    var vxEl = document.getElementById('vx-scroll-container');
    var compareEl = document.getElementById('compare-scroll-container');
    if (!stickyEl || !vxEl) return;

    var isSyncing = false;

    // Synchroniser le scroll vertical et horizontal via l'evenement scroll natif.
    // Le sync est absolu (scrollTop direct). equalizeScrollHeights() garantit en amont
    // que tous les conteneurs ont le même maxScrollTop, donc aucun clamping en bas.
    function syncFrom(source) {
        if (isSyncing) return;
        isSyncing = true;
        requestAnimationFrame(function() {
            var scrollTop  = source.scrollTop;
            var scrollLeft = source.scrollLeft;

            if (source !== stickyEl) stickyEl.scrollTop = scrollTop;
            if (source !== vxEl) {
                vxEl.scrollTop = scrollTop;
                if (source === compareEl) vxEl.scrollLeft = scrollLeft;
            }
            if (compareEl && source !== compareEl) {
                compareEl.scrollTop = scrollTop;
                if (source === vxEl && isCompareMode) compareEl.scrollLeft = scrollLeft;
            }

            isSyncing = false;
            updateLabelPositions();
        });
    }

    // Detacher les anciens handlers
    $(stickyEl).off('scroll.sync');
    $(vxEl).off('scroll.sync');
    $(compareEl).off('scroll.sync');

    $(stickyEl).on('scroll.sync', function() { syncFrom(stickyEl); });
    $(vxEl).on('scroll.sync', function() { syncFrom(vxEl); });
    $(compareEl).on('scroll.sync', function() { syncFrom(compareEl); });

    // Intercepter la molette sur le conteneur sticky pour forcer le scroll
    // (la scrollbar est cachee, mais le scroll doit fonctionner via molette)
    // Utiliser addEventListener natif avec passive:false pour que preventDefault fonctionne
    if (stickyEl._wheelSyncHandler) {
        stickyEl.removeEventListener('wheel', stickyEl._wheelSyncHandler);
    }
    stickyEl._wheelSyncHandler = function(e) {
        e.preventDefault();
        stickyEl.scrollTop += e.deltaY;
        syncFrom(stickyEl);
    };
    stickyEl.addEventListener('wheel', stickyEl._wheelSyncHandler, { passive: false });
}

// ========================================
// ALIGNEMENT PARFAIT DES LIGNES
// ========================================

function syncRowHeights() {
    // Sélection native (plus rapide que jQuery + .eq(i))
    var sRows = document.querySelectorAll('#tbody-sticky tr');
    var vRows = document.querySelectorAll('#tbody-vx tr');
    var cRows = document.querySelectorAll('#tbody-compare tr');
    var len = sRows.length;

    // Passe 1 : effacer toutes les hauteurs (writes groupés — pas de reflow ici)
    for (var i = 0; i < len; i++) {
        sRows[i].style.height = '';
        if (vRows[i]) vRows[i].style.height = '';
        if (cRows[i]) cRows[i].style.height = '';
    }

    // Passe 2 : lire toutes les hauteurs d'un coup (1 seul reflow au lieu de N)
    var heights = new Array(len);
    for (var i = 0; i < len; i++) {
        heights[i] = Math.max(
            sRows[i] ? sRows[i].offsetHeight : 0,
            vRows[i] ? vRows[i].offsetHeight : 0,
            cRows[i] ? cRows[i].offsetHeight : 0
        );
    }

    // Passe 3 : appliquer les hauteurs (writes groupés — pas de reflow intermédiaire)
    for (var i = 0; i < len; i++) {
        if (heights[i] > 0) {
            sRows[i].style.height = heights[i] + 'px';
            if (vRows[i]) vRows[i].style.height = heights[i] + 'px';
            if (cRows[i]) cRows[i].style.height = heights[i] + 'px';
        }
    }

    // Sync headers : approche ligne par ligne (seulement 2-3 lignes — impact perf négligeable).
    // On ne peut PAS utiliser les 3 passes globales ici : la ligne sticky a des <th rowspan="2">
    // qui couvrent une ligne vide (<tr> sans cellules). Si on reset toutes les hauteurs en bloc
    // avant de lire, le navigateur redistribue la hauteur du rowspan sur les deux lignes à partir
    // de la hauteur naturelle, ce qui donne une valeur fausse pour la ligne 0 (header-empty-sticky).
    // En traitant ligne par ligne, chaque ligne précédente est déjà fixée quand on lit la suivante.
    var sHead = document.querySelectorAll('#thead-sticky tr');
    var vHead = document.querySelectorAll('#thead-vx tr');
    var cHead = document.querySelectorAll('#thead-compare tr');
    var headLen = Math.max(sHead.length, vHead.length, cHead.length);

    for (var j = 0; j < headLen; j++) {
        var sh = sHead[j] || null;
        var vh = vHead[j] || null;
        var ch = cHead[j] || null;

        if (sh) sh.style.height = '';
        if (vh) vh.style.height = '';
        if (ch) ch.style.height = '';

        var maxHH = Math.max(
            sh ? (sh.offsetHeight || 0) : 0,
            vh ? (vh.offsetHeight || 0) : 0,
            ch ? (ch.offsetHeight || 0) : 0
        );

        if (maxHH > 0) {
            if (sh) sh.style.height = maxHH + 'px';
            if (vh) vh.style.height = maxHH + 'px';
            if (ch) ch.style.height = maxHH + 'px';
        }
    }
}

/**
 * Égalise les maxScrollTop de tous les conteneurs verticaux.
 *
 * Problème : la scrollbar horizontale de vx-scroll-container réduit son clientHeight
 * de ~17px, donc son maxScrollTop (scrollHeight - clientHeight) est ~17px plus grand
 * que celui du sticky qui n'a pas de scrollbar horizontale. Avec un sync absolu,
 * sticky est clampé avant d'atteindre la dernière ligne.
 *
 * Fix : on ajoute un padding-bottom sur le sticky pour que son scrollHeight augmente
 * du même montant — les deux conteneurs ont alors le même maxScrollTop.
 */
function equalizeScrollHeights() {
    var stickyEl  = document.querySelector('.sticky-columns-wrapper');
    var vxEl      = document.getElementById('vx-scroll-container');
    var compareEl = document.getElementById('compare-scroll-container');
    if (!stickyEl || !vxEl) return;

    // Réinitialiser les compensations avant mesure
    stickyEl.style.paddingBottom = '0px';

    // Calculer le maxScrollTop de référence (le plus grand parmi les conteneurs visibles)
    var refMax = vxEl.scrollHeight - vxEl.clientHeight;
    if (compareEl && compareEl.style.display !== 'none') {
        refMax = Math.max(refMax, compareEl.scrollHeight - compareEl.clientHeight);
    }

    // Compenser sticky si son maxScrollTop est inférieur
    var stickyMax = stickyEl.scrollHeight - stickyEl.clientHeight;
    var diff = refMax - stickyMax;
    if (diff > 0) {
        stickyEl.style.paddingBottom = diff + 'px';
    }
}

// ========================================
// RECUPERATION DES DONNEES
// ========================================

/**
 * Retourne les lignes wrappees de la version courante
 */
function getCurrentVersionData() {
    if (!currentFamily || !dataByFamily[currentFamily]) return [];
    return dataByFamily[currentFamily].data || [];
}

function retrieveDataTableBySelectedFamily(family, userRole) {
    loadVersionData(family, versionCurrent, userRole, true);
}

/**
 * Charge les lignes d'une version pour une famille
 * Si deja en cache (meme famille + meme version), on rebuild directement
 * @param {Function} [callback] - Si fourni, appele apres chargement SANS rebuild auto
 */
function updateDerniereModif(modif) {
    var el = document.getElementById('derniere-modif-famille');
    if (!el) return;
    if (modif && modif.date) {
        el.textContent = 'Dernière modification : ' + modif.date + ' par ' + modif.editeur;
        el.style.display = '';
    } else {
        el.textContent = '';
        el.style.display = 'none';
    }
}

function loadVersionData(family, version, userRole, buildFilters, callback) {
    // Jeton de requete : une reponse qui arrive apres un changement de famille
    // ou de version est perimee et ne doit ni ecraser dataByFamily ni declencher
    // un rebuild (sinon le tableau affiche les donnees d'une autre selection).
    var seq = ++versionRequestSeq;

    function finish() {
        if (seq !== versionRequestSeq) return;
        if (callback) {
            callback();
        } else {
            rebuildTable($('#tbody-sticky'), $('#tbody-vx'), $('#tbody-compare'), getCurrentVersionData(), buildFilters, true);
        }
    }

    // Verifier si on a deja les donnees pour cette famille + version
    if (dataByFamily[family] && dataByFamily[family]._loadedVersion === version) {
        currentFamily = family;
        finish();
        return;
    }

    $.ajax({
        url: tableDataRetrievalUrl,
        method: 'POST',
        data: { famille: family, version: version, role: userRole },
        success: function(response) {
            if (seq !== versionRequestSeq) return;
            var parsed = typeof response === 'string' ? JSON.parse(response) : response;
            // Conserver le compareCache si on change juste de version
            var existingCache = (dataByFamily[family] && dataByFamily[family].compareCache) || {};
            dataByFamily[family] = {
                data: parsed.data || [],
                all_versions: parsed.all_versions || [],
                derniere_modif: parsed.derniere_modif || null,
                compareCache: existingCache,
                _loadedVersion: version
            };
            currentFamily = family;
            updateDerniereModif(parsed.derniere_modif);
            finish();
        },
        error: function(xhr, status, error) {
            console.error('Erreur AJAX:', error);
            // On enchaine quand meme : sans cela le tableau reste fige sur le
            // contenu precedent (ex. "Aucune donnee disponible") alors que les
            // selecteurs affichent deja une autre version.
            finish();
        }
    });
}

/**
 * Charge les donnees de comparaison entre deux versions (a la demande)
 * Utilise un cache pour ne pas recharger les memes paires
 */
function loadCompareData(family, vCurrent, vCompare, callback) {
    // Le callback declenche le rebuild : il doit etre appele dans TOUS les cas
    // (cache, erreur reseau, famille inconnue), sinon le tableau garde l'etat
    // precedent alors que les selecteurs ont deja change.
    function finish() {
        // Selection modifiee pendant la requete -> reponse perimee, le chargement
        // en cours pour la nouvelle selection fera le rebuild.
        if (vCurrent !== versionCurrent || vCompare !== versionCompared) return;
        if (callback) callback();
    }

    var familyData = dataByFamily[family];
    if (!familyData) {
        finish();
        return;
    }

    if (!familyData.compareCache) familyData.compareCache = {};
    var cacheKey = vCurrent + '|' + vCompare;

    // Deja en cache ?
    if (familyData.compareCache[cacheKey]) {
        finish();
        return;
    }

    $.ajax({
        url: compareDataRetrievalUrl,
        method: 'POST',
        data: { famille: family, version_current: vCurrent, version_compare: vCompare },
        success: function(response) {
            var parsed = typeof response === 'string' ? JSON.parse(response) : response;
            familyData.compareCache[cacheKey] = parsed.paired || [];
            finish();
        },
        error: function(xhr, status, error) {
            console.error('Erreur AJAX compare:', error);
            finish();
        }
    });
}

// ========================================
// UTILITAIRES VISUELS
// ========================================

function updateAllBackgroundColorScenario() {
    $('#tbody-sticky tr.ligne').each(function() {
        var scenario = $(this).find('td[data-field="scenario"]').text().trim().toLowerCase();
        var rowIndex = $(this).attr('data-row-index');

        var color = '';
        if (scenario === 'downside' || scenario === 'down side') {
            color = 'rgba(255, 0, 0, 0.15)';
        } else if (scenario === 'upside' || scenario === 'up side') {
            color = 'rgba(0, 255, 0, 0.15)';
        }

        $('tr.ligne[data-row-index="' + rowIndex + '"]').css('background-color', color);
    });
}

// ========================================
// EXPORT XLSX - Fonctions utilitaires partagées
// ========================================
//
// Ces fonctions sont utilisées par l'export Excel sur toutes les pages :
//   - Page Accueil   : export du layout 3 tables (sticky + VX + compare)
//   - Page Comparaison en ligne   : export d'un tableau unique
//   - Page Comparaison dupliquée  : export d'un tableau unique
//
// Le processus d'export se déroule ainsi :
//   1. cssColorToARGB()           : convertir les couleurs CSS en format Excel
//   2. extractCellStyle()         : lire les styles visuels d'une cellule DOM
//   3. extractHeaderRowCells()    : parcourir un <tr> de header en gérant rowspan/colspan
//   4. addSheetFromSingleTable()  : exporter un <table> HTML complet vers une feuille Excel
//      (La page Accueil utilise sa propre fonction addSheetFromTables qui combine
//       deux tables, mais réutilise les 3 fonctions ci-dessus.)
// ========================================

/**
 * Convertit une couleur CSS en format ARGB hexadécimal pour ExcelJS.
 *
 * Fonctionnement pas à pas :
 *   1. Si la couleur est null, 'transparent' ou rgba(0,0,0,0), retourne null (pas de couleur)
 *   2. Tente de parser le format rgb(r,g,b) ou rgba(r,g,b,a) via une regex
 *   3. Si ce n'est pas du rgb, tente le format hex (#FFF ou #FFFFFF)
 *   4. Si rgba avec alpha < 0.05, ignore (trop transparent pour être visible)
 *   5. Si rgba avec alpha, mélange la couleur avec du blanc (fond de page)
 *      pour simuler la transparence dans Excel (qui ne supporte pas l'alpha)
 *   6. Retourne la chaîne 'FFRRGGBB' (FF = opacité max, puis rouge-vert-bleu)
 *
 * @param {string} cssColor - Valeur CSS de couleur (ex: 'rgb(255,0,0)', 'rgba(0,0,0,0.5)', '#FF0000')
 * @returns {string|null} - Couleur ARGB hex pour ExcelJS, ou null si transparent/invalide
 */
function cssColorToARGB(cssColor) {
    // Étape 1 : Éliminer les valeurs nulles ou transparentes
    if (!cssColor || cssColor === 'transparent' || cssColor === 'rgba(0, 0, 0, 0)') return null;

    // Étape 2 : Tenter de parser rgb/rgba
    var m = cssColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    if (!m) {
        // Étape 3 : Tenter le format hexadécimal (#FFF ou #FFFFFF)
        if (cssColor.charAt(0) === '#') {
            var hex = cssColor.replace('#', '');
            if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
            return 'FF' + hex.toUpperCase();
        }
        return null;
    }

    // Convertir les composantes R, G, B en hex 2 chiffres
    var r = parseInt(m[1]).toString(16).padStart(2, '0');
    var g = parseInt(m[2]).toString(16).padStart(2, '0');
    var b = parseInt(m[3]).toString(16).padStart(2, '0');

    // Étape 4-5 : Gérer l'alpha (transparence) pour les fonds semi-transparents
    var mAlpha = cssColor.match(/rgba?\(\d+,\s*\d+,\s*\d+,\s*([\d.]+)/);
    if (mAlpha) {
        var alpha = parseFloat(mAlpha[1]);
        // Étape 4 : Si trop transparent, ignorer
        if (alpha < 0.05) return null;

        // Étape 5 : Mélanger avec blanc (simulation de transparence)
        // Formule : couleur_finale = couleur * alpha + blanc * (1 - alpha)
        var rVal = Math.round(parseInt(m[1]) * alpha + 255 * (1 - alpha));
        var gVal = Math.round(parseInt(m[2]) * alpha + 255 * (1 - alpha));
        var bVal = Math.round(parseInt(m[3]) * alpha + 255 * (1 - alpha));
        r = Math.min(255, rVal).toString(16).padStart(2, '0');
        g = Math.min(255, gVal).toString(16).padStart(2, '0');
        b = Math.min(255, bVal).toString(16).padStart(2, '0');
    }

    // Étape 6 : Retourner ARGB (FF = opaque)
    return 'FF' + (r + g + b).toUpperCase();
}

/**
 * Extrait les styles calculés (computed styles) d'une cellule DOM pour ExcelJS.
 *
 * Fonctionnement pas à pas :
 *   1. Récupère les styles calculés de l'élément via window.getComputedStyle()
 *   2. Convertit la couleur de fond (backgroundColor) en ARGB via cssColorToARGB()
 *   3. Convertit la couleur de texte (color) en ARGB
 *   4. Détecte si le texte est en gras (fontWeight >= 700 ou 'bold')
 *   5. Construit l'objet style ExcelJS avec :
 *      - fill     : couleur de fond (pattern solid)
 *      - font     : police, taille, gras, couleur
 *      - border   : bordures fines grises sur les 4 côtés
 *      - alignment: centrage vertical, pas de retour à la ligne
 *
 * @param {HTMLElement} cell - Élément DOM (th ou td)
 * @returns {Object} - Objet style compatible ExcelJS
 */
function extractCellStyle(cell) {
    // Étape 1 : Récupérer les styles calculés du navigateur
    var cs = window.getComputedStyle(cell);

    // Étape 2-3 : Convertir les couleurs CSS en ARGB
    var bgColor = cssColorToARGB(cs.backgroundColor);
    var fontColor = cssColorToARGB(cs.color);

    // Étape 4 : Détecter le gras
    var isBold = parseInt(cs.fontWeight, 10) >= 700 || cs.fontWeight === 'bold';

    // Étape 5 : Construire l'objet style ExcelJS
    var style = {};

    // 5a. Couleur de fond
    if (bgColor) {
        style.fill = {
            type: 'pattern',
            pattern: 'solid',
            fgColor: { argb: bgColor }
        };
    }

    // 5b. Police
    style.font = {
        bold: isBold,
        size: 10,
        name: 'Calibri'
    };
    if (fontColor) {
        style.font.color = { argb: fontColor };
    }

    // 5c. Bordures fines grises
    style.border = {
        top: { style: 'thin', color: { argb: 'FFCCCCCC' } },
        left: { style: 'thin', color: { argb: 'FFCCCCCC' } },
        bottom: { style: 'thin', color: { argb: 'FFCCCCCC' } },
        right: { style: 'thin', color: { argb: 'FFCCCCCC' } }
    };

    // 5d. Alignement
    //   - vertical : toujours centré verticalement
    //   - horizontal : lu depuis le CSS text-align de la cellule
    //     (center, right, left, justify → repris tel quel par ExcelJS)
    var textAlign = cs.textAlign;
    style.alignment = {
        vertical: 'middle',
        wrapText: false
    };
    if (textAlign === 'center' || textAlign === 'right' || textAlign === 'justify') {
        style.alignment.horizontal = textAlign;
    }

    return style;
}

/**
 * Extrait les cellules d'une ligne <tr> de header en gérant rowspan et colspan.
 *
 * Cette fonction est complexe car les en-têtes HTML utilisent :
 *   - colspan : une cellule <th> s'étend sur plusieurs colonnes (ex: année sur 4 trimestres)
 *   - rowspan : une cellule <th> s'étend sur plusieurs lignes (ex: "Produit" couvre 2 lignes)
 *
 * Fonctionnement pas à pas :
 *   1. On parcourt les colonnes (colIdx) et les cellules DOM (cellIdx) en parallèle
 *   2. Pour chaque position de colonne, on vérifie d'abord si elle est couverte par
 *      un rowspan d'une ligne précédente (via rowspanTracker)
 *      → Si oui : on insère une cellule vide (couverte) et on avance colIdx
 *   3. Sinon, on lit la cellule DOM courante :
 *      a. On récupère ses attributs colspan, rowspan, texte, style
 *      b. Si rowspan > 1 : on enregistre dans rowspanTracker que les colonnes concernées
 *         sont couvertes pour les (rowspan - 1) lignes suivantes
 *      c. On ajoute la cellule dans le résultat
 *      d. Si colspan > 1 : on ajoute (colspan - 1) cellules vides à droite
 *   4. On avance cellIdx et colIdx en conséquence
 *   5. Le résultat est un tableau plat de cellules pour cette ligne,
 *      chaque entrée occupant exactement 1 position de colonne
 *
 * @param {HTMLTableRowElement} tr - Ligne de header à analyser
 * @param {Object} rowspanTracker - Dictionnaire { colIdx: { remaining, style } }
 *                                  partagé entre les appels successifs (une entrée par ligne)
 * @returns {Array} - Tableau d'objets { value, style, colspan, rowspan, coveredByRowspan }
 */
function extractHeaderRowCells(tr, rowspanTracker) {
    if (!tr) return [];
    var result = [];
    var cells = tr.querySelectorAll('th, td');
    var cellIdx = 0; // Index dans les cellules DOM réelles
    var colIdx = 0;  // Index de la colonne logique (position dans la grille)

    // Parcourir jusqu'à 300 colonnes max (sécurité anti-boucle infinie)
    while (colIdx < 300 && cellIdx <= cells.length) {
        // Étape 2 : Cette colonne est-elle couverte par un rowspan précédent ?
        if (rowspanTracker[colIdx] && rowspanTracker[colIdx].remaining > 0) {
            // Oui → insérer une cellule vide marquée comme couverte
            result.push({ value: '', style: rowspanTracker[colIdx].style, colspan: 1, rowspan: 1, coveredByRowspan: true });
            rowspanTracker[colIdx].remaining--;
            colIdx++;
        } else if (cellIdx < cells.length) {
            // Étape 3 : Lire la cellule DOM courante
            var cell = cells[cellIdx];
            var colspan = parseInt(cell.getAttribute('colspan')) || 1;
            var rowspan = parseInt(cell.getAttribute('rowspan')) || 1;
            var value = cell.textContent.trim();
            var style = extractCellStyle(cell);

            // Étape 3b : Si rowspan > 1, enregistrer pour les lignes suivantes
            if (rowspan > 1) {
                for (var rs = 0; rs < colspan; rs++) {
                    rowspanTracker[colIdx + rs] = { remaining: rowspan - 1, style: style };
                }
            }

            // Étape 3c : Ajouter la cellule principale
            result.push({ value: value, style: style, colspan: colspan, rowspan: rowspan, coveredByRowspan: false });
            colIdx++;

            // Étape 3d : Ajouter des entrées vides pour le colspan (colonnes droites)
            for (var s = 1; s < colspan; s++) {
                result.push({ value: '', style: style, colspan: 0, rowspan: 1, coveredByRowspan: false });
                colIdx++;
            }
            cellIdx++;
        } else {
            break;
        }
    }
    return result;
}

/**
 * Exporte un <table> HTML unique (thead + tbody) vers une feuille Excel.
 *
 * Utilisée par les pages de comparaison qui ont un seul tableau pleine largeur,
 * contrairement à la page Accueil qui utilise addSheetFromTables() pour combiner
 * le tableau sticky et le tableau scrollable.
 *
 * Le processus se déroule en 5 phases + écriture du corps :
 *
 *   PHASE 1 - Collecte des en-têtes dans une matrice
 *     On parcourt chaque <tr> du <thead> avec extractHeaderRowCells().
 *     Résultat : headerMatrix[ligne][colonne] = { value, style, colspan, rowspan, ... }
 *
 *   PHASE 2 - Normalisation de la matrice
 *     Certaines lignes peuvent avoir moins de colonnes que d'autres.
 *     On complète les lignes courtes avec des cellules vides pour
 *     que toutes les lignes aient le même nombre de colonnes.
 *
 *   PHASE 3 - Détection des fusions (merges)
 *     On parcourt la matrice pour identifier 3 types de fusions :
 *     a. Fusion horizontale : colspan > 1 (ex: année 2026 sur 4 trimestres)
 *     b. Fusion verticale   : rowspan > 1 (ex: "Produit" sur 2 lignes)
 *     c. Auto-fusion        : cellule avec du texte suivie de cellules vides en dessous
 *                             → on fusionne automatiquement (ex: colonnes sticky)
 *
 *   PHASE 4 - Écriture des en-têtes dans Excel
 *     On écrit les valeurs ligne par ligne dans la feuille Excel,
 *     puis on applique les styles (fond, police, bordures, alignement).
 *
 *   PHASE 5 - Application des fusions
 *     On applique toutes les fusions détectées en Phase 3 via ws.mergeCells().
 *
 *   CORPS - Écriture des lignes de données
 *     Pour chaque <tr> du <tbody> :
 *     1. On parcourt les <td> en récupérant texte + style + colspan
 *     2. Les nombres sont convertis en valeurs numériques (pour les calculs Excel)
 *     3. On écrit la ligne dans Excel et on applique les styles
 *     4. On gère les colspan éventuels dans le corps (fusions horizontales)
 *
 *   Enfin, on ajuste automatiquement la largeur des colonnes selon le contenu.
 *
 * @param {ExcelJS.Workbook} workbook - Classeur ExcelJS
 * @param {string} sheetName - Nom de la feuille (ex: "VX vs V1")
 * @param {HTMLTableSectionElement} thead - Élément <thead> du tableau
 * @param {HTMLTableSectionElement} tbody - Élément <tbody> du tableau
 * @returns {ExcelJS.Worksheet} - La feuille créée
 */
function addSheetFromSingleTable(workbook, sheetName, thead, tbody) {
    var ws = workbook.addWorksheet(sheetName);

    var headRows = thead.querySelectorAll('tr');
    var bodyRows = tbody.querySelectorAll('tr');

    // ================================================================
    // PHASE 1 : Collecter les données des en-têtes dans une matrice
    // ================================================================
    // On utilise un seul rowspanTracker car on a un seul <thead>
    // (contrairement à addSheetFromTables qui en combine deux)
    var headerMatrix = [];
    var rowspanTracker = {};

    for (var hi = 0; hi < headRows.length; hi++) {
        headerMatrix.push(extractHeaderRowCells(headRows[hi], rowspanTracker));
    }

    // ================================================================
    // PHASE 2 : Normaliser la matrice (toutes les lignes même largeur)
    // ================================================================
    var maxCols = 0;
    headerMatrix.forEach(function(row) { if (row.length > maxCols) maxCols = row.length; });
    var totalDataCols = maxCols;

    headerMatrix.forEach(function(row) {
        while (row.length < maxCols) {
            row.push({ value: '', style: {}, colspan: 1, rowspan: 1, coveredByRowspan: false });
        }
    });

    // ================================================================
    // PHASE 3 : Identifier les fusions (merges) à appliquer
    // ================================================================
    var merges = [];
    var merged = {}; // merged["row,col"] = true → cette cellule fait déjà partie d'un merge

    for (var r = 0; r < headerMatrix.length; r++) {
        for (var c = 0; c < headerMatrix[r].length; c++) {
            var key = r + ',' + c;
            if (merged[key]) continue; // Déjà fusionnée, on passe

            var cell = headerMatrix[r][c];

            // 3a. Fusion horizontale (colspan > 1)
            //     Ex: "2026" avec colspan=4 fusionne les colonnes Q1-Q2-Q3-Q4
            if (cell.colspan > 1) {
                merges.push({ startRow: r + 1, startCol: c + 1, endRow: r + 1, endCol: c + cell.colspan });
                for (var cc = c; cc < c + cell.colspan; cc++) {
                    merged[r + ',' + cc] = true;
                }
            }

            // 3b. Fusion verticale (rowspan > 1)
            //     Ex: "Produit" avec rowspan=2 fusionne 2 lignes
            if (cell.rowspan > 1) {
                var endRow = r + cell.rowspan;
                var endCol = c + (cell.colspan > 1 ? cell.colspan : 1);
                merges.push({ startRow: r + 1, startCol: c + 1, endRow: endRow, endCol: endCol });
                for (var rr = r; rr < endRow; rr++) {
                    for (var cc2 = c; cc2 < endCol; cc2++) {
                        merged[rr + ',' + cc2] = true;
                    }
                }
            }

            // 3c. Auto-fusion : cellule avec contenu + cellules vides en dessous
            //     → on fusionne verticalement pour éviter les trous visuels
            //     Condition : la cellule a du texte, n'est pas couverte par un rowspan,
            //     et n'a pas déjà un rowspan ou colspan
            if (cell.value !== '' && !cell.coveredByRowspan && cell.rowspan === 1 && cell.colspan <= 1) {
                var extendRows = 0;
                for (var rr2 = r + 1; rr2 < headerMatrix.length; rr2++) {
                    var belowCell = headerMatrix[rr2][c];
                    var belowKey = rr2 + ',' + c;
                    if (belowCell && belowCell.value === '' && !belowCell.coveredByRowspan && !merged[belowKey]) {
                        extendRows++;
                    } else {
                        break;
                    }
                }
                if (extendRows > 0) {
                    merges.push({ startRow: r + 1, startCol: c + 1, endRow: r + 1 + extendRows, endCol: c + 1 });
                    for (var rr3 = r; rr3 <= r + extendRows; rr3++) {
                        merged[rr3 + ',' + c] = true;
                    }
                }
            }
        }
    }

    // ================================================================
    // PHASE 4 : Écrire les en-têtes dans Excel avec leurs styles
    // ================================================================
    for (var r = 0; r < headerMatrix.length; r++) {
        // Créer un tableau de valeurs pour cette ligne
        var rowValues = headerMatrix[r].map(function(c) { return c.value; });
        var excelRow = ws.addRow(rowValues);

        // Appliquer les styles cellule par cellule
        for (var c = 0; c < headerMatrix[r].length; c++) {
            var xlCell = excelRow.getCell(c + 1);
            var st = headerMatrix[r][c].style;
            if (st.fill) xlCell.fill = st.fill;
            if (st.font) xlCell.font = st.font;
            if (st.border) xlCell.border = st.border;
            if (st.alignment) xlCell.alignment = st.alignment;
        }
    }

    // ================================================================
    // PHASE 5 : Appliquer les fusions dans Excel
    // ================================================================
    merges.forEach(function(m) {
        try {
            // ws.mergeCells(startRow, startCol, endRow, endCol)
            // Les indices sont 1-based dans ExcelJS
            ws.mergeCells(m.startRow, m.startCol, m.endRow, m.endCol);
        } catch (e) {
            // Ignorer les erreurs si une cellule est déjà fusionnée
        }
    });

    // ================================================================
    // CORPS : Écrire les lignes de données du <tbody>
    // ================================================================
    for (var bi = 0; bi < bodyRows.length; bi++) {
        var tr = bodyRows[bi];
        var rowValues = [];
        var rowStyles = [];
        var colSpans = [];

        // Parcourir toutes les cellules <td> de cette ligne
        var cells = tr.querySelectorAll('td, th');
        for (var c = 0; c < cells.length; c++) {
            var span = parseInt(cells[c].getAttribute('colspan')) || 1;
            var text = cells[c].textContent.trim();
            // Convertir en nombre si possible (pour que Excel traite comme nombre)
            var numVal = parseFloat(text);
            rowValues.push(!isNaN(numVal) && text !== '' ? numVal : text);
            rowStyles.push(extractCellStyle(cells[c]));
            colSpans.push(span);
            // Si colspan > 1, ajouter des cellules vides pour les colonnes couvertes
            for (var s = 1; s < span; s++) {
                rowValues.push('');
                rowStyles.push(extractCellStyle(cells[c]));
                colSpans.push(0);
            }
        }

        // Écrire la ligne dans Excel
        if (rowValues.length > totalDataCols) totalDataCols = rowValues.length;
        var excelRow = ws.addRow(rowValues);

        // Appliquer les styles cellule par cellule
        for (var ci = 0; ci < rowStyles.length; ci++) {
            var xlCell = excelRow.getCell(ci + 1);
            var st = rowStyles[ci];
            if (st.fill) xlCell.fill = st.fill;
            if (st.font) xlCell.font = st.font;
            if (st.border) xlCell.border = st.border;
            if (st.alignment) xlCell.alignment = st.alignment;
        }

        // Appliquer les fusions horizontales dans le corps (colspans)
        for (var ci = 0; ci < colSpans.length; ci++) {
            if (colSpans[ci] > 1) {
                var startCol = ci + 1;
                var endCol = ci + colSpans[ci];
                ws.mergeCells(excelRow.number, startCol, excelRow.number, endCol);
            }
        }
    }

    // ================================================================
    // Auto-ajustement de la largeur des colonnes
    // ================================================================
    ws.columns.forEach(function(col) {
        var maxLen = 0;
        col.eachCell({ includeEmpty: false }, function(cell) {
            var val = cell.value !== null && cell.value !== undefined ? String(cell.value) : '';
            var isBold = cell.font && cell.font.bold;
            var len = isBold ? Math.ceil(val.length * 1.15) : val.length;
            if (len > maxLen) maxLen = len;
        });
        // min 4, max 40 — reproduit le comportement d'Excel auto-fit (Calibri 11pt)
        col.width = Math.min(Math.max(4, maxLen + 2), 40);
    });

    // ================================================================
    // BORDURE EXTÉRIEURE NOIRE
    // ================================================================
    var totalRows = headerMatrix.length + bodyRows.length;
    var outerBorderStyle = { style: 'medium', color: { argb: 'FF000000' } };

    for (var row = 1; row <= totalRows; row++) {
        for (var col = 1; col <= totalDataCols; col++) {
            var isTop    = row === 1;
            var isBottom = row === totalRows;
            var isLeft   = col === 1;
            var isRight  = col === totalDataCols;
            if (!isTop && !isBottom && !isLeft && !isRight) continue;

            var xlCell = ws.getCell(row, col);
            var cur = xlCell.border || {
                top:    { style: 'thin', color: { argb: 'FFCCCCCC' } },
                left:   { style: 'thin', color: { argb: 'FFCCCCCC' } },
                bottom: { style: 'thin', color: { argb: 'FFCCCCCC' } },
                right:  { style: 'thin', color: { argb: 'FFCCCCCC' } }
            };
            xlCell.border = {
                top:    isTop    ? outerBorderStyle : cur.top,
                left:   isLeft   ? outerBorderStyle : cur.left,
                bottom: isBottom ? outerBorderStyle : cur.bottom,
                right:  isRight  ? outerBorderStyle : cur.right
            };
        }
    }

    return ws;
}

// ========================================
// EVENEMENTS
// ========================================

$(document).ready(function() {
    // Handler délégué unique pour le highlight au clic — remplace les .on() par cellule
    // Les cellules sont créées et détruites à chaque rebuild, mais ce handler persiste
    $(document).on('click',
        '#tbody-vx .period-cell, #tbody-compare .period-cell, ' +
        '#tbody-vx .extra-cell, #tbody-compare .extra-cell, ' +
        '#tbody-vx .total-cell, #tbody-compare .total-cell',
        function() { highlightCorrespondingCells($(this)); }
    );

    $('body').on('change', '.tableau-entite-realisatrice select', function() {
        var newValue = $(this).val();
        var contractId = $(this).closest('tr').attr('data-contract-id');
        console.log('Changement entité pour contrat', contractId, ':', newValue);
    });
});
