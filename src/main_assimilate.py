# -*- coding: utf-8 -*-
import os
import sys
import shutil
import argparse
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from functions.verify import verify_arg_file
from functions.parallel import run_parallel_tasks
from functions.general import process_args
from functions.observations import fetch_observations
from functions.log import Logger
from configuration import AssimilatorConfig
from model import Simstrat

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data-assimilation", "src")))
from functions.assimilate import _date, floor_to_day, latest_snapshot_date, phase_args, stage_perturbations, write_analysis_snapshot
from functions.postprocess_temperature import post_process_temperature
from assimilate import run as run_assimilation


def task(parameters, args):
    key = parameters["key"]
    lake_dir = os.path.join(args["repo_dir"], "runs", "{}_assimilate".format(key))
    os.makedirs(lake_dir, exist_ok=True)
    log = Logger(path=lake_dir) if args["log"] else Logger()
    log.initialise("Simstrat Operational Assimilation - {}".format(key))
    model_inputs = os.path.join(lake_dir, "model_inputs")                 # model_inputs_path
    forecast_dir = os.path.join(lake_dir, "forecast")
    obs_csv = os.path.join(lake_dir, "observations", "temperature.csv")
    perturb_json = os.path.join(lake_dir, "perturbations.json")

    log.info("Fetching live in-situ observations for {}".format(key))
    first_obs, last_obs = fetch_observations(key, parameters, args, obs_csv)

    prev_last_da_str = latest_snapshot_date(model_inputs)

    if last_obs is None:
        if prev_last_da_str is None:
            log.info("No observations and no prior snapshot for {}, skipping.".format(key))
            return
        log.info("No observations available for {}; forecasting from last snapshot {}.".format(key, prev_last_da_str))
        last_da = datetime.strptime(prev_last_da_str, "%Y%m%d").replace(tzinfo=timezone.utc)
    else:
        if args["first_da_date"]:
            first_da = datetime.strptime(args["first_da_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
        else:
            first_da = floor_to_day(first_obs)
        last_da = floor_to_day(last_obs)

        if prev_last_da_str is None:
            if args["spinup_years"]:
                spinup_start = first_da - relativedelta(years=args["spinup_years"])
                overwrite_start_date = _date(spinup_start)
                log.info("Cold start: spinning up {} from {} to {}".format(key, overwrite_start_date, _date(first_da)))
            else:
                overwrite_start_date = False
                log.info("Cold start: spinning up {} from the start of the meteo data to {}".format(key, _date(first_da)))
            spin = Simstrat("model_inputs", parameters, phase_args(
                args, lake_dir,
                snapshot=True, forecast=False, post_process=False, upload=False,
                overwrite_simulation=True, remove_existing_results=True,
                overwrite_start_date=overwrite_start_date, overwrite_end_date=_date(first_da)))
            spin.process()
            prev_last_da_str = latest_snapshot_date(model_inputs)
            if prev_last_da_str is None:
                raise ValueError("No analysis snapshot in {} after spin-up; cannot assimilate.".format(model_inputs))
        prev_last_da = datetime.strptime(prev_last_da_str, "%Y%m%d").replace(tzinfo=timezone.utc)

        if last_da > prev_last_da:
            log.info("Assimilating {} from {} to {}".format(key, _date(prev_last_da), _date(last_da)))
            da_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data-assimilation"))
            da_cfg = {
                "engine": args["engine"],
                "model": "simstrat",
                "algorithm": args["algorithm"],
                "results_dir": "Results_{}".format(args["algorithm"]),
                "par_file": "Settings_{}.par".format(args["algorithm"]),
                "inflation": args["inflation"],
                "n_members": args["n_members"],
                "sigma_obs": args["sigma_obs"],
                "sigma_scale": args["sigma_scale"],
                "rng_seed": args["rng_seed"],
                "start_date": prev_last_da.strftime("%Y-%m-%d"),
                "end_date": last_da.strftime("%Y-%m-%d"),
                "lake": key,
                "ensemble_base": lake_dir,
                "model_inputs_path": model_inputs,
                "obs_file": obs_csv,
                "perturbations_file": stage_perturbations(key, parameters, perturb_json, da_dir),
                "progress": False,
                "max_workers": args["max_assimilation_workers"],
            }
            run_assimilation(da_cfg, model="simstrat")

            write_analysis_snapshot(da_cfg, last_da, os.path.join(model_inputs, "simulation-snapshot_{}.dat".format(_date(last_da))))
        else:
            log.info("No observations newer than {} for {}, skipping assimilation.".format(prev_last_da_str, key))
            last_da = prev_last_da

    log.info("Forecasting {} from {} to the forecast horizon".format(key, _date(last_da)))
    os.makedirs(forecast_dir, exist_ok=True)
    shutil.copy(os.path.join(model_inputs, "simulation-snapshot_{}.dat".format(_date(last_da))),
                os.path.join(forecast_dir, "simulation-snapshot_{}.dat".format(_date(last_da))))
    forecast = Simstrat("forecast", parameters, phase_args(
        args, lake_dir,
        snapshot=True, snapshot_date=_date(last_da), forecast=True,
        overwrite_simulation=False, remove_existing_results=True,
        overwrite_start_date=False, overwrite_end_date=False))
    forecast.process()

    log.info("Combining assimilation and forecast temperature into NetCDF for {}".format(key))
    try:
        post_process_temperature(lake_dir, args["simstrat_version"], parameters)
    except Exception as e:
        log.info("Failed to combine temperature outputs: {}".format(e), indent=1)


def main(arg_file=False, overwrite_args=False):
    config = AssimilatorConfig()
    args, lake_parameters = config.load(arg_file, overwrite_args)
    run_dir = os.path.join(args["repo_dir"], "runs")
    os.makedirs(run_dir, exist_ok=True)
    if args["log"]:
        log = Logger(path=run_dir)
    else:
        log = Logger()
    log.initialise("Simstrat Operational Assimilation")
    log.inputs("Arguments", args)
    run_parallel_tasks(lake_parameters, args, task, log)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run operational data assimilation for Simstrat lakes')
    parser.add_argument('arg_file', type=verify_arg_file, help='Name of the argument file in /args')
    parser.add_argument('args', nargs='*', metavar='key=value', help='Additional args in key=value format. This overwrites values in the argument file.')
    args = parser.parse_args()
    arg_file = args.arg_file
    overwrite_args = process_args(args)
    main(arg_file=arg_file, overwrite_args=overwrite_args)
