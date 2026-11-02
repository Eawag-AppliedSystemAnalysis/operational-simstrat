# -*- coding: utf-8 -*-
"""Operational data assimilation: for each lake and each of its runs (static/assimilation/<lake>.json),
assimilate the new observations up to 00:00 of the run day and forecast from there. Rolling: each
run continues its own state in runs/<lake>_<run>/ instead of re-running the past."""
import os
import shutil
import argparse
from dateutil.relativedelta import relativedelta
from functions.verify import verify_arg_file
from functions.parallel import run_parallel_tasks
from functions.general import process_args, upload_files
from functions.log import Logger
from functions.assimilate import (assimilator, day_str, parse_day, floor_to_day, assimilation_end,
                                  latest_snapshot_date, phase_args, copy_master_inputs,
                                  refresh_ensemble_inputs, assimilation_run_config)
from functions.assimilation_observations import fetch_observations
from functions.assimilation_output import post_process_temperature
from configuration import AssimilatorConfig, load_assimilation_config
from model import Simstrat


def task(parameters, args):
    key = parameters["key"]
    lake_cfg = load_assimilation_config(args["repo_dir"], key)
    failures = []
    for run_key, run_cfg in lake_cfg["runs"].items():
        try:
            assimilate_run(key, run_key, run_cfg, lake_cfg, parameters, args)
        except Exception:
            if args["debug"]:
                raise
            failures.append(run_key)
    if failures:
        raise RuntimeError('Assimilation run(s) {} failed for lake "{}"; see their task logs.'
                           .format(", ".join(failures), key))


def assimilate_run(key, run_key, run_cfg, lake_cfg, parameters, args):
    run_name = "{}_{}".format(key, run_key)
    lake_dir = os.path.join(args["repo_dir"], "runs", run_name)
    os.makedirs(lake_dir, exist_ok=True)
    log = Logger(path=lake_dir) if args["log"] else Logger()
    log.initialise("Simstrat Operational Assimilation - {}".format(run_name))

    log.begin_stage("fetch_observations")
    log.info("Fetching and preprocessing in-situ observations for {}".format(key), indent=1)
    obs_csv, first_obs, last_obs = fetch_observations(key, lake_cfg["observations"], args,
                                                      os.path.join(lake_dir, "observations"), log)
    log.end_stage()

    end = assimilation_end(args)
    snapshot = latest_snapshot_date(os.path.join(lake_dir, "model_inputs"))
    continuing = snapshot is not None
    if not continuing:
        if last_obs is None:
            log.warning("No observations and no prior snapshot for {}, nothing to do.".format(key))
            return
        clear_run_outputs(run_name, lake_dir, log)
        snapshot = spin_up(key, parameters, args, lake_dir, first_obs, end, log)
    start = parse_day(snapshot)

    extend_inputs(key, parameters, args, lake_dir, start, log)
    if end > start:
        if last_obs is None or last_obs <= start:
            log.warning("No observations for {} since {}; advancing the ensemble without updates."
                        .format(key, snapshot))
        analysed = assimilate(key, run_name, run_cfg, lake_cfg, args, lake_dir, start, end, obs_csv, log)
    else:
        log.info("{} is already assimilated up to {}; forecasting only.".format(key, snapshot))
        analysed = start
    forecast(key, parameters, args, lake_dir, analysed, log)
    publish(run_name, parameters, args, lake_dir, start if continuing else None, log)


def clear_run_outputs(run_name, lake_dir, log):
    """Before a cold start, remove what an earlier run left: the members would otherwise resume from
    their old state, and the old mean trajectory and NetCDF months would be published. The
    observation record is kept."""
    stale = [os.path.join(lake_dir, name) for name in
             ("assimilation", "openda_{}".format(run_name), "forecast", "netcdf",
              "T_out_spinup.dat", "T_out_combined.dat")]
    stale = [p for p in stale if os.path.exists(p)]
    if stale:
        log.warning("Cold start: removing the previous run's {}".format(
            ", ".join(os.path.basename(p) for p in stale)), indent=1)
    for p in stale:
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)


def spin_up(key, parameters, args, lake_dir, first_obs, end, log):
    """Cold start: a free run up to the first assimilation day. Returns its snapshot's day."""
    model_inputs = os.path.join(lake_dir, "model_inputs")
    first_da = parse_day(args["first_da_date"]) if args["first_da_date"] else floor_to_day(first_obs)
    if first_da > end:
        raise ValueError("First assimilation day {} is after the run day {}.".format(day_str(first_da), day_str(end)))
    log.begin_stage("spin_up")
    spinup_start = day_str(first_da - relativedelta(years=args["spinup_years"])) if args["spinup_years"] else False
    log.info("Cold start: spinning up {} from {} to {}".format(
        key, spinup_start or "the start of the meteo data", day_str(first_da)), indent=1)
    Simstrat(key, parameters, phase_args(
        args, lake_dir, directory="model_inputs",
        snapshot=True, forecast=False, post_process=False, upload=False,
        overwrite_simulation=True, remove_existing_results=True,
        overwrite_start_date=spinup_start, overwrite_end_date=day_str(first_da))).process()
    spinup_results = os.path.join(model_inputs, "Results", "T_out.dat")
    if os.path.exists(spinup_results):
        shutil.copy(spinup_results, os.path.join(lake_dir, "T_out_spinup.dat"))
    else:
        log.warning("Spin-up produced no T_out.dat; pre-assimilation temperature will be omitted.", indent=1)
    snapshot = latest_snapshot_date(model_inputs)
    if snapshot is None:
        raise ValueError("No analysis snapshot in {} after spin-up; cannot assimilate.".format(model_inputs))
    log.end_stage()
    return snapshot


def extend_inputs(key, parameters, args, lake_dir, start, log):
    """Rebuild the model inputs from `start` to the forecast horizon with the latest meteo data."""
    log.begin_stage("extend_master_inputs")
    log.info("Extending master inputs for {} from {} to the forecast horizon".format(key, day_str(start)), indent=1)
    Simstrat(key, parameters, phase_args(
        args, lake_dir, directory="model_inputs",
        run=False, forecast=True, post_process=False, upload=False,
        overwrite_simulation=False, remove_existing_results=False,
        overwrite_start_date=day_str(start), overwrite_end_date=False)).process()
    log.end_stage()


def assimilate(key, run_name, run_cfg, lake_cfg, args, lake_dir, start, end, obs_csv, log):
    """Assimilate (start, end] and write the ensemble-mean analysis as the forecast's snapshot.
    Returns the day the analysis reached."""
    model_inputs = os.path.join(lake_dir, "model_inputs")
    assimilation_dir = os.path.join(lake_dir, "assimilation")
    log.begin_stage("assimilate")
    log.info("Assimilating {} from {} to {}".format(key, day_str(start), day_str(end)), indent=1)
    cfg = assimilation_run_config(key, run_cfg, lake_cfg, args, start, end, model_inputs, assimilation_dir,
                                  obs_csv, openda_dir=os.path.join(lake_dir, "openda_{}".format(run_name)))
    refresh_ensemble_inputs(assimilation_dir, model_inputs, cfg["n_members"], log)
    result = assimilator.run(cfg)
    reached = result["analysis_time"]
    if reached > end or reached != floor_to_day(reached):
        raise RuntimeError("The assimilation ended at {}, not at {} or a day before it; the snapshot "
                           "would be misdated.".format(reached, end))
    if reached < end:
        # An OpenDA restart chain does not run on a window without observations.
        log.warning("{} is assimilated up to {} only (no observations after it); forecasting from there."
                    .format(key, day_str(reached)), indent=1)
    if result["output_grid"]:
        # The forecast then also outputs every observation depth.
        shutil.copy(result["output_grid"], os.path.join(model_inputs, "z_out.dat"))
    if reached > start:
        if cfg["engine"] == "openda":
            assimilator.merge_mean_trajectory(cfg, assimilation_dir)
        assimilator.analysis_mean_state(
            cfg, os.path.join(model_inputs, "simulation-snapshot_{}.dat".format(day_str(reached))))
    log.end_stage()
    return reached


def forecast(key, parameters, args, lake_dir, start, log):
    """A free run from the analysis at `start` to the forecast horizon."""
    model_inputs = os.path.join(lake_dir, "model_inputs")
    forecast_dir = os.path.join(lake_dir, "forecast")
    snapshot = "simulation-snapshot_{}.dat".format(day_str(start))
    log.begin_stage("forecast")
    log.info("Forecasting {} from {} to the forecast horizon".format(key, day_str(start)), indent=1)
    os.makedirs(forecast_dir, exist_ok=True)
    shutil.copy(os.path.join(model_inputs, snapshot), os.path.join(forecast_dir, snapshot))
    copy_master_inputs(model_inputs, forecast_dir)
    if os.path.exists(os.path.join(model_inputs, "z_out.dat")):
        shutil.copy(os.path.join(model_inputs, "z_out.dat"), os.path.join(forecast_dir, "z_out.dat"))
    Simstrat(key, parameters, phase_args(
        args, lake_dir, directory="forecast",
        snapshot=True, snapshot_date=day_str(start), forecast=True,
        overwrite_simulation=False, remove_existing_results=True,
        overwrite_start_date=False, overwrite_end_date=False, post_process=False)).process()
    log.end_stage()


def publish(run_name, parameters, args, lake_dir, changed_since, log):
    """Write the combined temperature as NetCDF (only the months from `changed_since` on a
    continuing run) and upload it."""
    log.begin_stage("combine_temperature")
    log.info("Combining assimilation and forecast temperature into NetCDF for {}".format(run_name), indent=1)
    try:
        post_process_temperature(lake_dir, args["simstrat_version"], parameters,
                                 changed_since=changed_since, clear_netcdf=changed_since is not None)
    except Exception as e:
        log.warning("Failed to combine temperature outputs: {}".format(e), indent=1)
    log.end_stage()

    if args["upload"]:
        log.begin_stage("upload")
        local_folder = os.path.join(lake_dir, "netcdf")
        if os.path.isdir(local_folder):
            remote_folder = os.path.join(args["results_folder_api"], run_name)
            log.info("Uploading {} results to {}".format(run_name, remote_folder), indent=1)
            upload_files(local_folder, remote_folder, args["server_host"], args["server_user"],
                         args["server_password"], log)
        else:
            log.warning("No NetCDF output at {}; nothing to upload.".format(local_folder), indent=1)
        log.end_stage()


def main(arg_file=False, overwrite_args=False):
    config = AssimilatorConfig()
    args, lake_parameters = config.load(arg_file, overwrite_args)
    run_dir = os.path.join(args["repo_dir"], "runs")
    os.makedirs(run_dir, exist_ok=True)
    log = Logger(path=run_dir) if args["log"] else Logger()
    log.initialise("Simstrat Operational Assimilation")
    log.inputs("Arguments", args)
    run_parallel_tasks(lake_parameters, args, task, log)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run operational data assimilation for Simstrat lakes')
    parser.add_argument('arg_file', type=verify_arg_file, help='Name of the argument file in /args')
    parser.add_argument('args', nargs='*', metavar='key=value', help='Additional args in key=value format. This overwrites values in the argument file.')
    args = parser.parse_args()
    main(arg_file=args.arg_file, overwrite_args=process_args(args))
