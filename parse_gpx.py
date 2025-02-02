"""
All functions used to import, parse gpx files
"""
from pathlib import Path
import pickle
import logging

import gpxpy
import gpxpy.geo
import gpxpy.gpx
import numpy as np
import pandas as pd
from scipy.interpolate import UnivariateSpline, interp1d
import pytz

from utilities import getfilelist, season_of_date
from tqdm import tqdm
from timezonefinder import TimezoneFinder
from get_weather import get_weather_dict

log = logging.getLogger("gpxfun."+__name__)


def interpolateroutes(r: list) -> list:
    """
    interpolates and smoothes a route
    :param r: list of (lat,lon) tuples containing the route
    :type r: list
    """
    points = np.array([np.array(xx) for xx in r])
    _, idx = np.unique(points, axis=0, return_index=True)
    points = points[np.sort(idx)]
    distance = np.cumsum(np.sqrt(np.sum(np.diff(points, axis=0) ** 2, axis=1)))
    # these are some magic numbers that work best for cycle distances
    s = distance[-1] / 10000.0
    distance = np.insert(distance, 0, 0) / distance[-1]
    alpha = np.linspace(0, 1, 1000)
    # smooth the curve
    splines = [UnivariateSpline(distance, coords, s=s) for coords in points.T]
    points = np.vstack([spl(alpha) for spl in splines]).T
    # interpolate to get exactly 1000 points per curve
    distance = np.cumsum(np.sqrt(np.sum(np.diff(points, axis=0) ** 2, axis=1)))
    distance = np.insert(distance, 0, 0) / distance[-1]
    interpolator = interp1d(distance, points, kind="linear", axis=0)
    points = interpolator(alpha)
    points = [list(x) for x in list(points)]
    return points


def read_gpx_file(filename: Path, filehandle=None, weather: bool = True) -> dict:
    """read one gpxfile
    :param filename: Path containing the gpx file
    :type filename: Path
    :param filehandle: Optional file handle. If given, the data is read from the file
                        handle instead of the file given in filename
    :return: dictionary with the results of the parsing
    :rtype: dict
    """
    log.debug(f"read {filename} {'with' if weather else 'without'} weather data")
    p = {}
    p["filename"] = filename.name
    if filehandle is None:
        with open(filename) as fh:
            g = gpxpy.parse(fh)
    else:
        g = gpxpy.parse(filehandle)
    p["distance"] = gpxpy.gpx.GPX.length_3d(g)
    p["duration"] = gpxpy.gpx.GPX.get_duration(g) / 60  # pyright: ignore
    p["keywords"] = "" if g.keywords is None else g.keywords
    p["uphill"] = g.get_uphill_downhill().downhill
    x = g.get_points_data()[0]
    p["start"] = gpxpy.geo.Location(
        latitude=x.point.latitude,
        longitude=x.point.longitude,
        elevation=x.point.elevation,
    )
    tf = TimezoneFinder()  # reuse
    tz = tf.timezone_at(lng=x.point.longitude, lat=x.point.latitude)  #
    p["startdatetime"] = g.get_time_bounds().start_time.astimezone(  # pyright: ignore
        pytz.timezone(tz)
    )
    if weather:
        wd = get_weather_dict(
            p["startdatetime"], x.point.latitude, x.point.longitude, x.point.elevation
        )
        p = p | wd
    p["date"] = p["startdatetime"].date()  # pyright: ignore
    p["starttime"] = p["startdatetime"].time()  # pyright: ignore
    p["starttimefloat"] = p["starttime"].hour + p["starttime"].minute / 60.0
    p["month"] = p["date"].month
    p["weekday"] = p["date"].strftime("%A")
    p["season"] = season_of_date(p["date"])
    x = g.get_points_data()[-1]
    p["ende"] = gpxpy.geo.Location(
        latitude=x.point.latitude,
        longitude=x.point.longitude,
        elevation=x.point.elevation,
    )
    p["distance_crow"] = p["ende"].distance_3d(p["start"])
    route = list(
        map(lambda x: [x.point.longitude, x.point.latitude], g.get_points_data())
    )
    p["route_inter"] = interpolateroutes(route)
    p["speed"] = p["distance"]/1000/p["duration"]*60
    p["crowspeed"] = p["distance_crow"]/1000/p["duration"]*60
    return p


def read_gpx_file_list(
    filelist: list, delete: bool = False, weather: bool = True
) -> pd.DataFrame:
    """
    - Reads gpx files from a file list
    - Deletes the files if delete option is set
    - Returns a result DataFrame with information for each GPX file
    """
    log.info(f"{len(filelist)} Dateien lesen")
    r = []
    for f in (pbar := tqdm(filelist, colour="#ff00ff", desc="read GPX files")):
        pbar.set_postfix_str(f.name[0:20])
        if not str(f).endswith("gpx"):
            continue
        p = read_gpx_file(f, weather=weather)
        if delete:
            f.unlink()
        r.append(p)
    df = pd.DataFrame(r).convert_dtypes()
    df = df.astype(
        {
            "season": "category",
            "keywords": "category",
            "month": "category",
        }
    )
    df["weekday"] = pd.Categorical(
        df.weekday,
        categories=[
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ],
        ordered=True,
    )
    return df


def read_gpx_from_folder(infolder: str) -> pd.DataFrame:
    """read and parse all gpx files from a folder"""
    return read_gpx_file_list(getfilelist(infolder, suffix="gpx", withpath=True))


def update_pickle_from_list(
    filelist: list,
    mypickle: Path = Path("pickles/df.pickle"),
    delete: bool = False,
    weather: bool = True,
) -> tuple[pd.DataFrame, bool]:
    """update a pickle file of gpx data with a list of gpx files"""
    if not Path(mypickle).is_file():
        log.info(f"{mypickle} doesn't exist, I create it")
        d = pd.DataFrame()
        fl = filelist
    else:
        with open(mypickle, "rb") as f:
            d = pickle.load(f)
        fl = [f for f in filelist if f.name not in list(d["filename"])]
    log.info(f"{len(fl)} of {len(filelist)} have to be parsed")
    updated = len(fl) > 0
    if updated:
        d = pd.concat(
            [d, read_gpx_file_list(fl, delete=delete, weather=weather)], axis=0
        )
        mypickle.parents[0].mkdir(exist_ok=True)
        with open(mypickle, "wb") as f:
            pickle.dump(d, f)
    return d


def update_pickle_from_folder(
    infolder: str,
    mypickle: Path = Path("pickles/df.pickle"),
    delete: bool = False,
    weather: bool = True,
) -> tuple[pd.DataFrame, bool]:
    """update a pickle file of gpx data with a folder containing gpx files"""
    return update_pickle_from_list(
        getfilelist(infolder, suffix="gpx", withpath=True),
        mypickle=mypickle,
        delete=delete,
        weather=weather,
    )


if __name__ == "__main__":
    from mylog import get_log
    log = get_log()
    x = read_gpx_file(Path("./data/20220904T145953000.gpx"))
    x["route_inter"] = []
    import json

    print(json.dumps(x, sort_keys=True, indent=4, default=str))
