# -*- coding: utf-8 -*-
"""Helpers of the assimilation workflow (src/data_assimilation.py), and the one place the
data-assimilation submodule is put on the import path."""
import os
import sys
import glob
import shutil
from datetime import datetime, timezone

DA_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data-assimilation", "src"))
if DA_SRC not in sys.path:
    sys.path.append(DA_SRC)
import assimilator                                        # noqa: E402
from assimilator import observations as da_observations  # noqa: E402,F401

MASTER_INPUTS = ["Forcing.dat", "Absorption.dat", "Qin.dat", "Tin.dat", "Sin.dat", "Qout.dat"]


def day_str(dt):
    return dt.strftime("%Y%m%d")


def parse_day(day):
    """A YYYYMMDD string as its 00:00 UTC."""
    return datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc)


def floor_to_day(dt):
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def assimilation_end(args):
    """00:00 UTC of the run day (args["da_end_date"], by default today). The run day is forecast,
    not hindcast: its observations are never assimilated, and every earlier one is."""
    if args.get("da_end_date"):
        return parse_day(args["da_end_date"])
    return floor_to_day(datetime.now(timezone.utc))


def latest_snapshot_date(directory):
    """The YYYYMMDD of the newest simulation-snapshot_*.dat in `directory`, or None."""
    snaps = sorted(glob.glob(os.path.join(directory, "simulation-snapshot_*.dat")))
    return os.path.basename(snaps[-1]).split(".")[0].split("_")[-1] if snaps else None


def phase_args(args, simulation_dir, **overrides):
    """args for one Simstrat phase (spin-up, extension, forecast) under the run's own folder."""
    return {**args, "simulation_dir": simulation_dir, **overrides}


def copy_master_inputs(src, dst):
    for name in MASTER_INPUTS:
        if os.path.exists(os.path.join(src, name)):
            shutil.copy(os.path.join(src, name), os.path.join(dst, name))


def refresh_ensemble_inputs(ensemble_base, model_inputs, n_members, log):
    """Copy the extended master inputs into ensemble0..N: the submodule copies the inputs only
    when it creates the members, so they would keep yesterday's forcing."""
    for i in range(n_members + 1):
        dst = os.path.join(ensemble_base, "ensemble{}".format(i))
        if os.path.isdir(dst):
            copy_master_inputs(model_inputs, dst)
    log.info("Refreshed ensemble0..{} inputs from master".format(n_members), indent=1)


def assimilation_run_config(key, run_cfg, lake_cfg, args, start, end, model_inputs, ensemble_base,
                            obs_csv, openda_dir):
    """The submodule's run config (data-assimilation/docs/configuration.md) for one run of one lake:
    the lake's sigma_obs and perturbation, overridden by the run's own keys, plus paths and runtime
    args."""
    cfg = {"lake": key, "perturbations": lake_cfg["perturbations"]}
    if lake_cfg.get("sigma_obs") is not None:
        cfg["sigma_obs"] = lake_cfg["sigma_obs"]
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
