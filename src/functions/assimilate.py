# -*- coding: utf-8 -*-
import os
import json
import glob
import shutil
from datetime import datetime, timezone

import numpy as np

from assimilator.models.simstrat import (read_snapshot_T_at, write_snapshot_T_at,
                                          _member_snapshot_paths, mean_traj_path)


def _date(dt):
    return dt.strftime("%Y%m%d")


def parse_day(day_str):
    """Parse a YYYYMMDD day string to a UTC-midnight datetime (the inverse of _date)."""
    return datetime.strptime(day_str, "%Y%m%d").replace(tzinfo=timezone.utc)


def run_arg(run_cfg, run_key, key, name):
    """Read a required per-run assimilation setting from the run's config block in
    lake_parameters.json; there is no global default."""
    if name not in run_cfg:
        raise ValueError('Missing assimilation parameter "{}" for run "{}" of lake "{}"; add it '
                         'to its entry in lake_parameters.json.'.format(name, run_key, key))
    return run_cfg[name]


def floor_to_day(dt):
    """Truncate a datetime to UTC midnight (the whole-day assimilation boundary)."""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


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
    Needed because the submodule's copy_model_inputs is skipped once the instances exist, so the
    ensembles would otherwise keep stale (short) forcing."""
    for i in range(n_members + 1):
        dst = os.path.join(ensemble_base, "ensemble{}".format(i))
        if os.path.isdir(dst):
            copy_master_inputs(model_inputs, dst)
    if log is not None:
        log.info("Refreshed ensemble0..{} inputs from master".format(n_members), indent=1)


def stage_perturbations(key, perturbations, out_json, da_dir):
    """First choice: the assimilation run's `perturbations` block from lake_parameters.json — a
    {variable: {phi, sigma}} map (or a full {variables: {...}} object), written to out_json under a
    'variables' key and used. Fallback: the committed <da_dir>/perturbations/<key>.json. Returns
    the perturbations_file path to set on the run config; the submodule's load_perturbations raises
    a clear error at run time if neither source exists."""
    if isinstance(perturbations, dict) and perturbations:
        os.makedirs(os.path.dirname(out_json), exist_ok=True)
        if isinstance(perturbations.get("variables"), dict):
            payload = dict(perturbations)
        else:
            payload = {"variables": dict(perturbations)}
        payload.setdefault("lake", key)
        with open(out_json, "w") as f:
            json.dump(payload, f, indent=4)
        return out_json
    return os.path.join(da_dir, "perturbations", "{}.json".format(key))


def _analysis_member_paths(member_id, da_cfg):
    """Member analysis snapshot + par file, resolving the engine's layout: native members live at
    ensemble{i}/<results_dir>/, OpenDA members at <openda_dir>/Results/work{i}/."""
    if da_cfg.get("engine") == "openda":
        work = os.path.join(da_cfg["openda_dir"], "Results", "work{}".format(member_id))
        return (os.path.join(work, "Results", "simulation-snapshot.dat"),
                os.path.join(work, "Settings.par"))
    return _member_snapshot_paths(member_id, da_cfg)


def write_analysis_snapshot(da_cfg, last_da_date, out_snapshot):
    """Build the deterministic warm-start for the forecast: the ensemble-mean analysis. Average
    the temperature column over members 1..N (the assimilated members) and inject it into a copy
    of member 1's analysis snapshot, written to out_snapshot as simulation-snapshot_<lastDA>.dat."""
    n_members = da_cfg["n_members"]
    cols = []
    for i in range(1, n_members + 1):
        snap, par = _analysis_member_paths(i, da_cfg)
        cols.append(read_snapshot_T_at(snap, par)[0])
    mean_T = np.mean(np.vstack(cols), axis=0)

    member_snap, member_par = _analysis_member_paths(1, da_cfg)
    shutil.copy(member_snap, out_snapshot)
    write_snapshot_T_at(out_snapshot, member_par, mean_T)


def merge_openda_mean(da_cfg, assimilation_dir):
    """Merge the OpenDA run's ensemble-mean trajectory (this window only, written to openda_dir)
    into the rolling mean file in assimilation_dir that post-processing reads, matching how the
    native engines accumulate the full trajectory across runs."""
    import pandas as pd
    src = mean_traj_path(da_cfg["openda_dir"], da_cfg["algorithm"])
    if not os.path.exists(src):
        raise FileNotFoundError("No ensemble-mean trajectory at {}".format(src))
    dst = mean_traj_path(assimilation_dir, da_cfg["algorithm"])
    new = pd.read_csv(src)
    new.columns = [c.strip().strip('"') for c in new.columns]
    if os.path.exists(dst):
        prev = pd.read_csv(dst)
        prev.columns = [c.strip().strip('"') for c in prev.columns]
        if list(prev.columns) == list(new.columns):
            time_col = new.columns[0]
            new = pd.concat([prev[~prev[time_col].isin(new[time_col])], new]).sort_values(time_col)
    new.to_csv(dst, index=False)
