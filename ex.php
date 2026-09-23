<?php
/*
 * Export de la table vers un CSV, en reutilisant l'ancienne appli PHP :
 * elle sait deja dialoguer avec ce serveur MySQL/MariaDB ancien,
 * donc aucun probleme d'authentification.
 *
 * A placer dans l'appli (ou a cote), a lancer en ligne de commande :
 *     php export_table.php
 * ou via le navigateur si c'est plus simple.
 *
 * LECTURE SEULE sur la base : uniquement un SELECT.
 */

// ---------- Config ----------
$HOST  = 'localhost';
$USER  = 'lecteur';
$PASS  = 'motdepasse';
$BASE  = 'mabase';
$TABLE = 'ma_table';
$SORTIE = __DIR__ . '/export_table.csv';
// ----------------------------

// mysqli ou l'ancienne extension mysql selon ce que supporte le serveur
if (function_exists('mysqli_connect')) {
    $conn = @mysqli_connect($HOST, $USER, $PASS, $BASE);
    if (!$conn) {
        die("Connexion impossible : " . mysqli_connect_error() . "\n");
    }
    @mysqli_set_charset($conn, 'utf8');
    $sql = "SELECT num, FichierJoint, FichierJoint2, photoContenu, PhotoContenant FROM `$TABLE`";
    $res = mysqli_query($conn, $sql);
    if (!$res) {
        die("Requete echouee : " . mysqli_error($conn) . "\n");
    }
    $fh = fopen($SORTIE, 'w');
    // BOM UTF-8 pour qu'Excel lise correctement les accents
    fwrite($fh, "\xEF\xBB\xBF");
    fputcsv($fh, ['num', 'FichierJoint', 'FichierJoint2', 'photoContenu', 'PhotoContenant'], ';');
    $n = 0;
    while ($row = mysqli_fetch_assoc($res)) {
        fputcsv($fh, $row, ';');
        $n++;
        if ($n % 5000 === 0) { echo "  $n lignes\n"; }
    }
    fclose($fh);
    mysqli_close($conn);
} else {
    // Tres vieux PHP : extension mysql d'origine
    $conn = @mysql_connect($HOST, $USER, $PASS);
    if (!$conn) { die("Connexion impossible : " . mysql_error() . "\n"); }
    mysql_select_db($BASE, $conn);
    $sql = "SELECT num, FichierJoint, FichierJoint2, photoContenu, PhotoContenant FROM `$TABLE`";
    $res = mysql_query($sql, $conn) or die("Requete echouee : " . mysql_error() . "\n");
    $fh = fopen($SORTIE, 'w');
    fwrite($fh, "\xEF\xBB\xBF");
    fputcsv($fh, ['num', 'FichierJoint', 'FichierJoint2', 'photoContenu', 'PhotoContenant'], ';');
    $n = 0;
    while ($row = mysql_fetch_assoc($res)) {
        fputcsv($fh, $row, ';');
        $n++;
    }
    fclose($fh);
    mysql_close($conn);
}

echo "Termine : $n lignes ecrites dans $SORTIE\n";
