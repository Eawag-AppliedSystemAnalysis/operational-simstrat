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
    """A copy of args pointing Simstrat at `simulation_dir` (the base; Simstrat appends /<key>),
    with the spin-up/forecast flags overridden. Keeps the assimilation pipeline independent of
    the operational forecast (runs/)."""
    phase = dict(args)
    phase["simulation_dir"] = simulation_dir
    phase.update(overrides)
    return phase


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
