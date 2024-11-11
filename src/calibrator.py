# -*- coding: utf-8 -*-
import os
import sys
import argparse
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from functions.verify import verify_arg_file
from functions.parallel import run_parallel_tasks
from functions.general import process_args, edit_parameters, download_observations
from functions.log import Logger
from configuration import CalibratorConfig
from model import Simstrat

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', "lake-calibrator", "src")))
from calibrate import calibrator


def task(parameters, args):
    model = Simstrat(parameters["key"], parameters, args)
    model.process()
    default_calibration_parameters = [
        {"name": "a_seiche", "min": 1e-5, "max": 0.5, "adjust": "relative"},
        {"name": "f_wind", "min": 0.10, "max": 1.75},
        {"name": "p_lw", "min": 0.80, "max": 1.20},
        {"name": "snow_temp", "min": 0.50, "max": 10.00},
        {"name": "p_absorb", "min": 0.50, "max": 1.50}
    ]
    calibration_parameters = []
    for cp in default_calibration_parameters:
        if cp["name"] in parameters:
            cp["initial"] = parameters[cp["name"]]
            calibration_parameters.append(cp)
    calibration_args = {
        "simulation_folder": model.simulation_dir,
        "calibration_folder": os.path.join(args["calibration_dir"], parameters["key"]),
        "observations": [{
            "file": os.path.join(args["observation_dir"], parameters["key"], "temperature.csv"),
            "parameter": "temperature",
            "unit": "degC",
            "start": (model.start_date + relativedelta(years=1)).isoformat(),
            "end": model.end_date.isoformat()
        }],
        "simulation": "simstrat",
        "execute": "docker run --rm --user $(id -u):$(id -g) -v {calibration_folder}:/simstrat/run eawag/simstrat:3.0.4 Calibration.par",
        "parameters": calibration_parameters,
        "calibration_framework": "PEST",
        "calibration_options": {
            "objective_function": "rms",
            "objective_variables": ["temperature"],
            "objective_weights": [1],
            "time_mode": "nearest",
            "depth_mode": "linear_interpolation",
            "agents": args["agents"],
            "port": parameters["port"],
            "debug": False
        }
    }
    if args["docker_dir"] != False:
        repo_name = os.path.basename(args["docker_dir"])
        dhcf = args["docker_dir"] + args["calibration_dir"].split(repo_name)[-1] + "/" + parameters["key"]
        calibration_args["docker_host_calibration_folder"] = dhcf
    results = calibrator(calibration_args)
    new_parameters = edit_parameters(args["lake_parameters_dir"], parameters["key"], results)
    model = Simstrat(new_parameters["key"], new_parameters, args)
    model.process()


def main(arg_file=False, overwrite_args={}):
    config = CalibratorConfig()
    overwrite_args["overwrite_simulation"] = True
    overwrite_args["run"] = True
    overwrite_args["upload"] = False
    overwrite_args["forecast"] = False
    overwrite_args["post_process"] = False
    args, lake_parameters = config.load(arg_file, overwrite_args)
    for i, lp in enumerate(lake_parameters):
        lp["port"] = 4005 + i
    if args["log"]:
        log = Logger(path=args["simulation_dir"])
    else:
        log = Logger()
    log.initialise("Simstrat Operational Calibration")
    log.inputs("Arguments", args)
    if args["download_observations"]:
        log.info("Downloading observations from {}".format(args["observations_url"]))
        download_observations(args["observations_url"], args["observation_dir"])
    run_parallel_tasks(lake_parameters, args, task, log)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run Simstrat on an operational basis')
    parser.add_argument('arg_file', type=verify_arg_file, help='Name of the argument file in /args')
    parser.add_argument('args', nargs='*', metavar='key=value', help='Additional args in key=value format. This overwrites values in the argument file.')
    args = parser.parse_args()
    arg_file = args.arg_file
    overwrite_args = process_args(args)
    main(arg_file=arg_file, overwrite_args=overwrite_args)

