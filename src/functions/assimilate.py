# -*- coding: utf-8 -*-
import os
import glob
import shutil
from datetime import datetime, timezone


def _date(dt):
    return dt.strftime("%Y%m%d")


def parse_day(day_str):
    """Parse a YYYYMMDD day string to a UTC-midnight datetime (the inverse of _date)."""
    return datetime.strptime(day_str, "%Y%m%d").replace(tzinfo=timezone.utc)


def floor_to_day(dt):
    """Truncate a datetime to UTC midnight (the whole-day assimilation boundary)."""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def assimilation_end(args):
    """The instant the assimilation runs up to: 00:00 UTC of the run day (args["da_end_date"], by
    default today). Observations from the run day are never assimilated -- that day is forecast,
    not hindcast -- and every observation from the days before it is."""
    if args.get("da_end_date"):
        return parse_day(args["da_end_date"])
    return floor_to_day(datetime.now(timezone.utc))


def latest_snapshot_date(directory):
    """Date (YYYYMMDD) of the most recent simulation-snapshot_*.dat in `directory`, or None."""
    snaps = sorted(glob.glob(os.path.join(directory, "simulation-snapshot_*.dat")))
    if not snaps:
        return None
    return os.path.basename(snaps[-1]).split(".")[0].split("_")[-1]


def phase_args(args, simulation_dir, **overrides):
    """A copy of args pointing Simstrat at `simulation_dir` (the base; Simstrat appends the run
    folder, which is `directory` when passed as an override, else the lake key), with the
    spin-up/forecast flags overridden. Keeps the assimilation pipeline independent of the
    operational forecast (runs/)."""
    phase = dict(args)
    phase["simulation_dir"] = simulation_dir
    phase.update(overrides)
    return phase


def copy_master_inputs(src, dst):
    """Copy the master time-series input files that exist in `src` into `dst`. Returns the list of
    files copied."""
    copied = []
    for name in ["Forcing.dat", "Absorption.dat", "Qin.dat", "Tin.dat", "Sin.dat", "Qout.dat"]:
        src_path = os.path.join(src, name)
        if os.path.exists(src_path):
            shutil.copy(src_path, os.path.join(dst, name))
            copied.append(name)
    return copied


def refresh_ensemble_inputs(ensemble_base, model_inputs, n_members, log=None):
    """Propagate the freshly extended master inputs from model_inputs/ into every ensemble0..N/.
    Needed because the data-assimilation pipeline skips copying the inputs once the members exist,
    so the ensembles would otherwise keep stale (short) forcing."""
    for i in range(n_members + 1):
        dst = os.path.join(ensemble_base, "ensemble{}".format(i))
        if os.path.isdir(dst):
            copy_master_inputs(model_inputs, dst)
    if log is not None:
        log.info("Refreshed ensemble0..{} inputs from master".format(n_members), indent=1)


def assimilation_run_config(key, run_cfg, lake_cfg, args, start, end, model_inputs, ensemble_base,
                            obs_csv, openda_dir=None):
    """The data-assimilation package's run config (a flat dict, see the submodule's
    docs/configuration.md) for one run of one lake: the run's own engine settings, the lake's
    observation error and forcing perturbation, and the operational paths and runtime args.
    A key set in the run overrides the lake-level one (e.g. a run-specific sigma_obs)."""
    cfg = {"lake": key}
    if lake_cfg.get("sigma_obs") is not None:
        cfg["sigma_obs"] = lake_cfg["sigma_obs"]
    cfg["perturbations"] = lake_cfg["perturbations"]
    cfg.update({k: v for k, v in run_cfg.items() if not k.startswith("_")})
    cfg.update({
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "model_inputs_path": model_inputs,
        "ensemble_base": ensemble_base,
        "obs_file": obs_csv,
        "progress": False,
        "max_workers": args["max_assimilation_workers"] or None,
        "docker_dir": args["docker_dir"],
        "repo_dir": args["repo_dir"],
    })
    if cfg["engine"] == "openda":
        cfg["openda_bin"] = args["openda_bin"] or None
        cfg["openda_dir"] = openda_dir
    return {k: v for k, v in cfg.items() if v is not None}
