""" 
A dash app to visualize the results
"""
# from dash import dcc, html, Dash, Output, Input, State, ctx, MATCH, ALL, dash_table
import base64
from pathlib import Path
import threading
import pickle
import json

from dash import Dash, Input, Output, State, ctx, no_update
import dash_bootstrap_components as dbc
import pandas as pd
from tqdm import tqdm

from plots import plotaroute, violin
from utilities import getfilelist, convert_bytes
from app_data_functions import parse_and_cluster, get_data_from_pickle_session
from app_layout import serve_layout

dbc_css = "https://cdn.jsdelivr.net/gh/AnnMarieW/dash-bootstrap-templates/dbc.css"
dashapp = Dash(__name__, external_stylesheets=[dbc.themes.SLATE, dbc_css])
dashapp.title = "Bike route analyzer"

dashapp.layout = serve_layout


@dashapp.callback(
    Output("startend_cluster_dropdown", "options"),
    Output("startend_cluster_dropdown", "value"),
    Input("storedflag", "data"),
    State("sessionid", "data"),
    prevent_initial_call=True,
)
def update_startend_dropdown(storedflag, sessionid):
    """Initialize the dropdown for the startendcluster"""
    print("CALLBACK update_startend_dropdown: "+str(ctx.triggered_id))
    if storedflag == False:
        return [no_update] * 2
    with open(Path("sessions") / sessionid / "most_imp_clusters.pickle", "rb") as f:
        most_imp_clusters = pickle.load(f)
    startendcluster_dropdown_opts = {}
    for cat in list(most_imp_clusters.startendcluster.cat.categories):
        startendcluster_dropdown_opts[cat] = "Start/End-Combination" + str(cat)
    return startendcluster_dropdown_opts, [0]


@dashapp.callback(
    Output("cluster_dropdown", "options"),
    Input("startend_cluster_dropdown", "value"),
    Input("storedflag", "data"),
    State("sessionid", "data"),
    prevent_initial_call=True,
)
def update_cluster_dropdown(startendclusters, storedflag, sessionid):
    """Initialize the dropdown for the route cluster using startendcluster"""
    print("CALLBACK update_cluster_dropdown: "+str(ctx.triggered_id))
    if storedflag == False:
        return [no_update] * 2
    with open(Path("sessions") / sessionid / "most_imp_clusters.pickle", "rb") as f:
        most_imp_clusters = pickle.load(f)
    clusters = most_imp_clusters[
        most_imp_clusters.startendcluster.isin([int(se) for se in startendclusters])
    ].cluster
    cluster_dropdown_opts = {}
    for clu in list(clusters):
        cluster_dropdown_opts[clu] = "Route" + str(clu)
    return cluster_dropdown_opts


@dashapp.callback(
    Output("progressbar", "value"),
    Output("progressbar", "label"),
    Output("progressbar", "color"),
    Output("storedflag", "data"),
    Output("load_textarea", "value"),
    Output("progressinterval", "disabled"),
    Output("sessionid", "data"),
    Input("progressinterval", "n_intervals"),
    State("sessionid", "data"),
    Input("numberoffiles", "data"),
    Input("picksessionid", "value"),
    prevent_initial_call=True,
)
def update_progessbar(_, sessionid, numberoffiles, picksessionid):
    """update the progress bar from the number of files remaining"""
    if ctx.triggered_id == None:
        return [no_update] * 7
    elif ctx.triggered_id == "picksessionid":
        return (
            100,
            "loaded a sessionid",
            "#00FF18",
            True,
            f"files will be loaded from sessionid {sessionid}",
            True,
            picksessionid,
        )
    elif numberoffiles < 2:
        return (
            0,
            "ERROR",
            "red",
            True,
            "Upload at least 2 GPX files",
            False,
            no_update,
        )
    filelist = getfilelist(Path("sessions") / sessionid, "gpx")
    n = len(filelist)
    storedflag = n == 0
    # check if the parsing thread is finished, otherwise, remain in state "not stored"
    for thread in threading.enumerate():
        # print(f"update_progessbar({sessionid}): thread.names : {thread.name}")
        if thread.name == "read" and thread.is_alive():
            storedflag = False
    percentage = (numberoffiles - n) / numberoffiles * 100
    # print( f" numberoffiles={numberoffiles}, percentage={percentage}, storedflag={storedflag}")
    if storedflag:
        filesize = convert_bytes(
            (Path("sessions") / sessionid / "df.pickle").stat().st_size
        )
        textarea = f"Finished parsing {numberoffiles} GPX files\n"
        textarea += f"Session id: {sessionid}"
        textarea += f"Total file size: {filesize}"
    else:
        textarea = f"Remaining files to parse ({n} of {numberoffiles})\n"
        textarea += f"Session id: {sessionid}"
        textarea += "\n".join(filelist)
    return (
        percentage,
        f"{numberoffiles-n} of {numberoffiles}",
        "#FF9800" if storedflag == False else "#00FF18",
        no_update if storedflag == False else storedflag,
        textarea,
        storedflag,
        no_update,
    )


@dashapp.callback(
    Output("numberoffiles", "data"),
    Input("upload-data", "contents"),
    Input("upload-data", "filename"),
    State("sessionid", "data"),
    prevent_initial_call=True,
)
def upload(contents, filenames, sessionid):
    """upload gpx data to session folder and start parsing thread"""
    print("CALLBACK upload: "+str(ctx.triggered_id))
    if ctx.triggered_id == None:
        return no_update
    # create sessionid folder
    (Path("sessions") / sessionid).mkdir(parents=True, exist_ok=True)
    # store alle files in a tmp session directory
    for ii in tqdm(
        range(len(contents)), colour="#ffff00", desc="GPX -> session folder"
    ):
        cc = contents[ii]
        filename = filenames[ii]
        _, content_string = cc.split(",")
        strdata = base64.b64decode(content_string).decode("utf-8")
        with open(Path("sessions") / sessionid / filename, "w") as f:
            f.write(strdata)
    print(f"upload({sessionid}): number of files = {len(contents)}")
    mythread = threading.Thread(
        target=parse_and_cluster,
        name="read",
        kwargs={
            "infolder": Path("sessions") / sessionid,
            "mypickle": Path("sessions") / sessionid / "df.pickle",
            "delete": True,
        },
    )
    mythread.start()
    return len(contents)


@dashapp.callback(
    Output("clustermap", "figure"),
    Output("clusterinfo", "value"),
    Input("storedflag", "data"),
    Input("cluster_dropdown", "value"),
    State("sessionid", "data"),
    prevent_initial_call=True,
)
def showmap(storedflag, clusters, sessionid):
    """Draw a map with the most common routes"""
    print("CALLBACK showmap: "+ str(ctx.triggered_id))
    if storedflag == False:
        return no_update, no_update
    dr, most_imp_clusters = get_data_from_pickle_session(sessionid)
    dr = dr[dr.cluster.isin(clusters)]
    fig = plotaroute(
        dr,
        groupfield="cluster",
        title=None,
    )
    return fig, most_imp_clusters.to_string()


@dashapp.callback(
    Output("violinplot", "figure"),
    Input("violinfactor", "value"),
    Input("storedflag", "data"),
    State("sessionid", "data"),
    prevent_initial_call=True,
)
def showhists(violinfactor, storedflag, sessionid):
    """Show plots to analyze the times"""
    print("CALLBACK showhists: "+ str(ctx.triggered_id))
    if storedflag == False:
        return no_update
    dr, _ = get_data_from_pickle_session(sessionid)
    dr = dr[dr.cluster.isin(clusters)]
    fig = violin(dr, violinfactor)
    return fig


@dashapp.callback(
    Output("violinfactor_selected_file_txt", "value"),
    Input("violinplot", "clickData"),
    State("storedflag", "data"),
    State("sessionid", "data"),
    prevent_initial_call=True,
)
def clickondata(clickdata, storedflag, sessionid):
    """Show information on the clicked data point"""
    print("CALLBACK clickondata: "+ str(ctx.triggered_id))
    if storedflag == False:
        return no_update
    dr, _ = get_data_from_pickle_session(sessionid)
    if clickdata is not None and storedflag:
        import pdb; pdb.set_trace()
        # I don't know, why I need this, but the given clickdata is not a proper dict at first
        clickeddict = json.loads(json.dumps(clickdata))
        clicked_file = clickeddict["points"][0]["customdata"][0]
        clickedseries = dr[dr["dateiname"] == clicked_file].iloc[0]
        clickedseries = clickedseries.drop(["route", "route_inter"])
        return "\n".join(f"{clickedseries}".split("\n")[0:-1])
    else:
        return "Click on a data point to show filename and infos"


app = dashapp.server
app.secret_key = "super secret key"

if __name__ == "__main__":
    dashapp.run_server(debug=True)

# start with: gunicorn app:app -b :8000
