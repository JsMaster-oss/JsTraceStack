# -*- coding: utf-8 -*-

import json

import plotly
from flask import jsonify, redirect, request, url_for
from flask_login import login_required

# MODIF GRAPH-22 : le calcul de la figure est parti dans cascade_cyclee.py, que
# les deux routes partagent. Elles portaient chacune leur copie : 333 lignes
# utiles identiques, et trois des correctifs de ce chantier n'etaient que la
# reparation de leur divergence.
#
# Les constantes sont reexportees ici parce que du code appelant les lit
# encore sous ce nom -- verifier_hovertemplate.py et les tests. Elles n'ont
# qu'une seule definition, celle de cascade_cyclee.
from cascade_cyclee import (  # noqa: F401
    BLOC_REDUCTIBLE,
    DICO_COLOR,
    GROUPE_CYCLE_SAP,
    GROUPE_PROTEGE,
    LISTE_COL_INTERET_ANALYSE,
    tracer_cascade,
)


# Création graphique cycles en cascade cyclée

@main.route("/create_graph_analyse_CCC2", methods=["POST"])
@login_required
@roles_required("Admin", "Writer", "Reader")
def create_graph_analyse_CCC2():

    req = request.get_json()

    fig = tracer_cascade(
        get_donnee_power_bi(config),
        generate_data_cycle(config),
        req["designation_article"],
        req["b_ordonner"],
    )

    data = {"graph": json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)}

    log_action(
        action="Consultation",
        menu="Visualisation",
        detail="Visualisation en cascade cyclée de : "
        + str(req["designation_article"]),
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


# Route intermédiaire qui sert à rendre dynamique le clic de la légende en
# retraçant le graphique.
@main.route("/update_graph", methods=["POST"])
# MODIF GRAPH-9 : décorateurs inversés, @roles_required précédait @login_required
@login_required
@roles_required("Admin", "Writer", "Reader")
def update_graph():

    req = request.get_json()
    server_data = req["server_data"]

    # MODIF GRAPH-22 : tout le corps de cette route est parti dans
    # cascade_cyclee.tracer_cascade. Ce qui distinguait update_graph de
    # create_graph_analyse_CCC2 tient dans ce seul dictionnaire : l'état de
    # chaque trace au moment du clic.
    #
    # `clickedTraceName` est reçu et n'a jamais servi : `traceStatut` porte
    # déjà l'état de TOUTES les traces, celle qu'on vient de cliquer comprise.
    # Le champ reste dans le contrat d'entrée pour ne pas toucher au JavaScript.
    masques = {
        etat["name"]: etat["visible"] for etat in req["traceStatut"]
    }

    fig = tracer_cascade(
        get_donnee_power_bi(config),
        generate_data_cycle(config),
        server_data["designation_article"],
        server_data["b_ordonner"],
        masques,
    )

    data = {"graph": json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)}

    return jsonify(data)
