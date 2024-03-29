import json
from datetime import timedelta
import numpy as np
from .general import datetime_to_simstrat_time, air_pressure_from_elevation, seiche_from_surface_area


def update_par_file(simstrat_version, file_path, start_date, end_date, snapshot, parameters, args, log):
    if simstrat_version in ["3.0.3", "3.0.4"]:
        with open(file_path) as f:
            par = json.load(f)

        par["Input"]['Grid'] = parameters["grid_cells"]
        par["ModelConfig"]["InflowMode"] = parameters["inflow_mode"]
        par["ModelConfig"]["CoupleAED2"] = args["couple_aed2"]

        par["Simulation"]["Start d"] = datetime_to_simstrat_time(start_date + timedelta(hours=1), parameters["reference_date"])
        par["Simulation"]["End d"] = datetime_to_simstrat_time(end_date - timedelta(hours=1), parameters["reference_date"])
        par["Simulation"]["Continue from last snapshot"] = snapshot
        par["Simulation"]["Reference year"] = parameters["reference_date"].year

        par["ModelParameters"]['lat'] = parameters["latitude"]
        par["ModelParameters"]['p_air'] = air_pressure_from_elevation(parameters["elevation"])
        par["ModelParameters"]['a_seiche'] = seiche_from_surface_area(parameters["surface_area"])

        for key in par["ModelParameters"].keys():
            if key in parameters:
                log.info("Overwriting default {} value with calibrated value: {}".format(key, parameters[key]), indent=2)
                par["ModelParameters"][key] = parameters[key]
    else:
        raise ValueError("Par file creation not implemented for Simstrat version {}".format(simstrat_version))

    return par


def overwrite_par_file_dates(file_path, start_date, end_date, reference_date):
    with open(file_path) as f:
        par = json.load(f)

    par["Simulation"]["Start d"] = datetime_to_simstrat_time(start_date + timedelta(hours=1), reference_date)
    par["Simulation"]["End d"] = datetime_to_simstrat_time(end_date - timedelta(hours=1), reference_date)

    with open(file_path, 'w') as f:
        json.dump(par, f, indent=4)


