# -*- coding: utf-8 -*-
import os
import json
import glob
import shutil

import numpy as np

from assimilator.models.simstrat import read_snapshot_T, write_snapshot_T_at, _member_snapshot_paths


def _date(dt):
    return dt.strftime("%Y%m%d")


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


def stage_perturbations(key, parameters, out_json, da_dir):
    """First choice: a `perturbations` block in lake_parameters.json (written to out_json and
    used). Fallback: the committed <da_dir>/perturbations/<key>.json. Returns the
    perturbations_file path to set on the run config; the submodule's load_perturbations raises a
    clear error at run time if neither source exists."""
    block = parameters.get("perturbations")
    if isinstance(block, dict) and isinstance(block.get("variables"), dict):
        os.makedirs(os.path.dirname(out_json), exist_ok=True)
        payload = dict(block)
        payload.setdefault("lake", key)
        with open(out_json, "w") as f:
            json.dump(payload, f, indent=4)
        return out_json
    return os.path.join(da_dir, "perturbations", "{}.json".format(key))


def write_analysis_snapshot(da_cfg, last_da_date, out_snapshot):
    """Build the deterministic warm-start for the forecast: the ensemble-mean analysis. Average
    the temperature column over members 1..N (the assimilated members) and inject it into a copy
    of member 1's analysis snapshot, written to out_snapshot as simulation-snapshot_<lastDA>.dat."""
    n_members = da_cfg["n_members"]
    cols = [read_snapshot_T(i, da_cfg)[0] for i in range(1, n_members + 1)]
    mean_T = np.mean(np.vstack(cols), axis=0)

    member_snap, member_par = _member_snapshot_paths(1, da_cfg)
    shutil.copy(member_snap, out_snapshot)
    write_snapshot_T_at(out_snapshot, member_par, mean_T)
