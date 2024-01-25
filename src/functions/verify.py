import os
import sys
from datetime import datetime


def verify_arg_file(value):
    arg_folder = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "../args"))
    for file in os.listdir(arg_folder):
        if os.path.splitext(file)[0] == value or file == value:
            return os.path.join(arg_folder, file)
    raise ValueError("Argument file {} not found in the args folder.".format(value))


def verify_path(value):
    try:
        os.makedirs(value, exist_ok=True)
    except Exception as e:
        raise ValueError("{} is not a valid path.".format(value))


def verify_bool(value):
    if not isinstance(value, bool):
        raise ValueError("{} is not a valid bool.".format(value))


def verify_dict(value):
    if not isinstance(value, dict):
        raise ValueError("{} is not a valid dictionary.".format(value))


def verify_integer(value):
    if not isinstance(value, int):
        raise ValueError("{} is not a valid bool.".format(value))


def verify_string(value):
    if not isinstance(value, str):
        raise ValueError("{} is not a valid string.".format(value))


def verify_list(value):
    if not isinstance(value, list):
        raise ValueError("{} is not a valid list.".format(value))


def verify_float(value):
    float_value = float(value)
    if not isinstance(float_value, float):
        raise ValueError


def verify_date(value):
    try:
        return datetime.strptime(value, '%Y%m%d')
    except:
        raise ValueError("A valid key: {} format YYYYMMDD must be provided.".format(value))


def verify_meteo_stations(stations):
    if not isinstance(stations, list):
        raise ValueError("Required input meteo_stations must be a list of dicts")
    for station in stations:
        if not isinstance(station, dict):
            raise ValueError("Required input meteo_stations must be a list of dicts")
        if "station_id" not in station or "source" not in station:
            raise ValueError("Required input meteo_stations dicts must contain station_id and source")


def verify_meteo_forecast(value):
    if not isinstance(value, dict):
        raise ValueError("meteo_forecast must be a dict")
    if "source" not in value or "model" not in value or "days" not in value:
        raise ValueError("meteo_forecast dict must contain source, days and model")
