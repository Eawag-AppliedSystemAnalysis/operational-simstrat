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


def verify_forcing(forcing):
    if not isinstance(forcing, list):
        raise ValueError("Required input forcing must be a list of dicts")
    for f in forcing:
        if not isinstance(f, dict):
            raise ValueError("Required input forcing must be a list of dicts")
        if "id" not in f or "type" not in f:
            raise ValueError("Required input forcing dicts must contain id and type")


def verify_inputs(inputs):
    if not isinstance(inputs, list):
        raise ValueError("Required input forcing must be a list of dicts")
    for i in inputs:
        if not isinstance(i, dict):
            raise ValueError("Required input forcing must be a list of dicts")
        if "discharge" not in i or "temperature" not in i:
            raise ValueError("Required input dicts must contain discharge, and temperature")


def verify_forcing_forecast(value):
    if not isinstance(value, dict):
        raise ValueError("meteo_forecast must be a dict")
    if "source" not in value or "model" not in value or "days" not in value:
        raise ValueError("meteo_forecast dict must contain source, days and model")
