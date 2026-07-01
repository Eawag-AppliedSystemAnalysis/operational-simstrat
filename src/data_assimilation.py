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
from functions.assimilate import (_date, floor_to_day, latest_snapshot_date, phase_args, stage_perturbations,
                                   write_analysis_snapshot, copy_master_inputs, refresh_ensemble_inputs)
from functions.postprocess import post_process_temperature
from assimilate import run as run_assimilation


def task(parameters, args):
    key = parameters["key"]
    lake_dir = os.path.join(args["repo_dir"], "runs", "{}_assimilate".format(key))
    os.makedirs(lake_dir, exist_ok=True)
    log = Logger(path=lake_dir) if args["log"] else Logger()
    log.initialise("Simstrat Operational Assimilation - {}".format(key))

    model_inputs = os.path.join(lake_dir, "model_inputs")
    forecast_dir = os.path.join(lake_dir, "forecast")
    obs_csv = os.path.join(lake_dir, "observations", "temperature.csv")
    assimilation_dir = os.path.join(lake_dir, "assimilation")
    perturb_json = os.path.join(assimilation_dir, "perturbations.json")

    def parse_day(day_str):
        return datetime.strptime(day_str, "%Y%m%d").replace(tzinfo=timezone.utc)

    log.begin_stage("fetch_observations")
    log.info("Fetching live in-situ observations for {}".format(key), indent=1)
    first_obs, last_obs = fetch_observations(key, parameters, args, obs_csv)
    prev_snapshot = latest_snapshot_date(model_inputs)
    log.end_stage()

    if last_obs is None:
        if prev_snapshot is None:
            log.warning("No observations and no prior snapshot for {}, nothing to do.".format(key))
            return
        log.info("No observations for {}; forecasting from last snapshot {}.".format(key, prev_snapshot))
        last_da = parse_day(prev_snapshot)
        assimilate = False
    else:
        if args["first_da_date"]:
            first_da = parse_day(args["first_da_date"])
        else:
            first_da = floor_to_day(first_obs)
        last_da = floor_to_day(last_obs)

        if prev_snapshot is None:
            log.begin_stage("spin_up")
            if args["spinup_years"]:
                spinup_start = _date(first_da - relativedelta(years=args["spinup_years"]))
                log.info("Cold start: spinning up {} from {} to {}".format(key, spinup_start, _date(first_da)), indent=1)
            else:
                spinup_start = False
                log.info("Cold start: spinning up {} from the start of the meteo data to {}".format(key, _date(first_da)), indent=1)
            spin = Simstrat(key, parameters, phase_args(
                args, lake_dir, directory="model_inputs",
                snapshot=True, forecast=False, post_process=False, upload=False,
                overwrite_simulation=True, remove_existing_results=True,
                overwrite_start_date=spinup_start, overwrite_end_date=_date(first_da)))
            spin.process()
            spinup_results = os.path.join(model_inputs, "Results", "T_out.dat")
            if os.path.exists(spinup_results):
                shutil.copy(spinup_results, os.path.join(lake_dir, "T_out_spinup.dat"))
            else:
                log.warning("Spin-up produced no T_out.dat; pre-assimilation temperature will be omitted.", indent=1)
            prev_snapshot = latest_snapshot_date(model_inputs)
            if prev_snapshot is None:
                raise ValueError("No analysis snapshot in {} after spin-up; cannot assimilate.".format(model_inputs))
            log.end_stage()

        assimilate = last_da > parse_day(prev_snapshot)
        if not assimilate:
            log.warning("No observations for {} newer than snapshot {}; forecasting only.".format(key, prev_snapshot))
            last_da = parse_day(prev_snapshot)

    snapshot_start = parse_day(prev_snapshot)

    log.begin_stage("extend_master_inputs")
    log.info("Extending master inputs for {} from {} to the forecast horizon".format(key, _date(snapshot_start)), indent=1)
    extend = Simstrat(key, parameters, phase_args(
        args, lake_dir, directory="model_inputs",
        run=False, forecast=True, post_process=False, upload=False,
        overwrite_simulation=False, remove_existing_results=False,
        overwrite_start_date=_date(snapshot_start), overwrite_end_date=False))
    extend.process()
    log.end_stage()

    if assimilate:
        log.begin_stage("assimilate")
        log.info("Assimilating {} from {} to {}".format(key, _date(snapshot_start), _date(last_da)), indent=1)
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
            "start_date": snapshot_start.strftime("%Y-%m-%d"),
            "end_date": last_da.strftime("%Y-%m-%d"),
            "lake": key,
            "ensemble_base": assimilation_dir,
            "model_inputs_path": model_inputs,
            "obs_file": obs_csv,
            "perturbations_file": stage_perturbations(key, parameters, perturb_json, da_dir),
            "progress": False,
            "max_workers": args["max_assimilation_workers"],
        }
        refresh_ensemble_inputs(assimilation_dir, model_inputs, args["n_members"], log)
        run_assimilation(da_cfg, model="simstrat")
        write_analysis_snapshot(da_cfg, last_da, os.path.join(model_inputs, "simulation-snapshot_{}.dat".format(_date(last_da))))
        log.end_stage()

    log.begin_stage("forecast")
    log.info("Forecasting {} from {} to the forecast horizon".format(key, _date(last_da)), indent=1)
    os.makedirs(forecast_dir, exist_ok=True)
    shutil.copy(os.path.join(model_inputs, "simulation-snapshot_{}.dat".format(_date(last_da))),
                os.path.join(forecast_dir, "simulation-snapshot_{}.dat".format(_date(last_da))))
    copy_master_inputs(model_inputs, forecast_dir)
    master_z_out = os.path.join(model_inputs, "z_out.dat")
    if os.path.exists(master_z_out):
        shutil.copy(master_z_out, os.path.join(forecast_dir, "z_out.dat"))
    forecast = Simstrat(key, parameters, phase_args(
        args, lake_dir, directory="forecast",
        snapshot=True, snapshot_date=_date(last_da), forecast=True,
        overwrite_simulation=False, remove_existing_results=True,
        overwrite_start_date=False, overwrite_end_date=False, post_process=False))
    forecast.process()
    log.end_stage()

    log.begin_stage("combine_temperature")
    log.info("Combining assimilation and forecast temperature into NetCDF for {}".format(key), indent=1)
    try:
        post_process_temperature(lake_dir, args["simstrat_version"], parameters)
    except Exception as e:
        log.warning("Failed to combine temperature outputs: {}".format(e), indent=1)
    log.end_stage()


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
