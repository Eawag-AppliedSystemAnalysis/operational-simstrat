import json
import numpy as np
from .general import datetime_to_simstrat_time, air_pressure_from_elevation, seiche_from_surface_area


def update_par_file_303(file_path, start_date, end_date, snapshot, parameters, args):
    with open(file_path) as f:
        par = json.load(f)

    par["Input"]['Grid'] = parameters["grid_cells"]
    par["ModelConfig"]["InflowMode"] = parameters["inflow_mode"]
    par["ModelConfig"]["CoupleAED2"] = args["couple_aed2"]

    par["Simulation"]["Start d"] = datetime_to_simstrat_time(start_date, parameters["reference_date"])
    par["Simulation"]["End d"] = datetime_to_simstrat_time(end_date, parameters["reference_date"])
    par["Simulation"]["Continue from last snapshot"] = snapshot
    par["Simulation"]["Reference year"] = parameters["reference_date"].year

    par["ModelParameters"]['lat'] = parameters["latitude"]
    par["ModelParameters"]['p_air'] = air_pressure_from_elevation(parameters["elevation"])
    par["ModelParameters"]['a_seiche'] = seiche_from_surface_area(parameters["surface_area"])

    for key in par["ModelParameters"].keys():
        if key in parameters:
            par["ModelParameters"][key] = parameters[key]

    return par
