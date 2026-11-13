"""Make the hourly free run the hour and sigma fits compare the observations with.

    python src/assimilation_tools/make_free_run.py upperlugano --start 2024-06-01 --end 2026-07-01

The operational run (runs/<lake>/) is the right model -- same setup, forcing and calibration, never
assimilated -- but it outputs every 3 h on a coarse depth grid, while the fits need every hour at
the observed depths. This copies its inputs to runs/assimilation_tools/<lake>/free_run/ and runs
Simstrat (Docker) twice:

  1. spin-up from the setup's own start to --start, with daily output (only its end state is kept)
  2. --start to --end from that state, hourly output, on the setup's output depths plus the depths
     the lake is observed at

AED2 is switched off in the copy, as in the assimilation runs, whatever the operational setup uses.

The result, runs/assimilation_tools/<lake>/free_run/Results/T_out.dat, is what select_hour.py and
fit_sigma.py read by default.
"""
import os
import json
import shutil
import subprocess

import numpy as np
import pandas as pd

import common
from assimilator.models.simstrat.model import read_z_out, write_z_out, extend_depths

FREE_RUN_DIR = "free_run"


def free_run_dir(key):
    return os.path.join(common.CACHE, key, FREE_RUN_DIR)


def _sim_days(ts, ref_year):
    return (ts - pd.Timestamp("{}-01-01".format(ref_year), tz="UTC")).total_seconds() / 86400.0


def _write_t_out(path, steps):
    with open(path, "w") as f:
        f.write("Number of time steps\n{}\n".format(int(steps)))


def _run(work, version, docker_dir=None):
    mount = work
    if docker_dir:
        mount = os.path.join(docker_dir, os.path.relpath(work, common.REPO))
    cmd = ["docker", "run", "--rm", "-v", "{}:/simstrat/run".format(mount),
           "eawag/simstrat:{}".format(version), "Settings.par"]
    common.logger.info("running %s", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit("Simstrat failed:\n{}".format((res.stderr or res.stdout)[-2000:]))


def main():
    ap = common.parser(__doc__.split("\n")[0])
    ap.add_argument("--start", required=True, help="first day of the hourly free run (YYYY-MM-DD)")
    ap.add_argument("--end", required=True, help="last day (must be inside the forcing)")
    ap.add_argument("--raw", help="raw observations, for the output depths (default: as the fit tools)")
    ap.add_argument("--simstrat-version", default="3.0.4")
    ap.add_argument("--docker-dir", help="repository path as Docker sees it, if different")
    cli = ap.parse_args()
    common.setup_logging()

    src = os.path.join(common.REPO, "runs", cli.lake)
    if not os.path.isfile(os.path.join(src, "Settings.par")):
        raise SystemExit("no operational setup at {} — run the operational simulation first".format(src))
    work = free_run_dir(cli.lake)
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work)
    for name in os.listdir(src):
        if name == "Results" or name.startswith("simulation-snapshot") or name.endswith(".log"):
            continue
        path = os.path.join(src, name)
        if os.path.isfile(path):
            shutil.copy2(path, work)
        else:
            shutil.copytree(path, os.path.join(work, name))

    par_path = os.path.join(work, "Settings.par")
    with open(par_path) as f:
        par = json.load(f)
    ref_year = par["Simulation"]["Reference year"]
    dt = par["Simulation"]["Timestep s"]
    start = pd.Timestamp(cli.start, tz="UTC")
    end = pd.Timestamp(cli.end, tz="UTC")
    setup_start = par["Simulation"]["Start d"]
    if _sim_days(start, ref_year) <= setup_start:
        raise SystemExit("--start is before the setup's own start ({:.1f} d)".format(setup_start))

    cfg = common.lake_config(cli.lake)
    raw = common.raw_observations(cli.lake, cfg, cli.raw)
    z_out = os.path.join(work, par["Output"]["Depths"])
    depths = extend_depths(read_z_out(z_out), {round(float(d), 2) for d in np.unique(raw["depth"])})
    write_z_out(z_out, depths)
    common.logger.info("output depths: %d (setup grid + observed depths)", len(depths))
    par["Output"]["Times"] = "t_out.dat"
    par["Simulation"]["Continue from last snapshot"] = True
    # Physics only, as in the assimilation runs (couple_aed2 false).
    par.setdefault("ModelConfig", {})["CoupleAED2"] = False

    # 1. spin-up to --start, daily output, ending in a snapshot
    _write_t_out(os.path.join(work, "t_out.dat"), 86400 // dt)
    par["Simulation"]["End d"] = _sim_days(start, ref_year)
    with open(par_path, "w") as f:
        json.dump(par, f, indent=4)
    _run(work, cli.simstrat_version, cli.docker_dir)
    results = os.path.join(work, par["Output"]["Path"])
    if not os.path.isfile(os.path.join(results, "simulation-snapshot.dat")):
        raise SystemExit("the spin-up left no simulation-snapshot.dat in {}".format(results))
    for name in os.listdir(results):
        if name.endswith("_out.dat"):
            os.remove(os.path.join(results, name))

    # 2. the free run proper, hourly, continuing from the spin-up state
    _write_t_out(os.path.join(work, "t_out.dat"), 3600 // dt)
    par["Simulation"]["Start d"] = _sim_days(start, ref_year)
    par["Simulation"]["End d"] = _sim_days(end, ref_year)
    with open(par_path, "w") as f:
        json.dump(par, f, indent=4)
    _run(work, cli.simstrat_version, cli.docker_dir)
    out = os.path.join(results, "T_out.dat")
    common.logger.info("hourly free run %s .. %s -> %s", start.date(), end.date(),
                       os.path.relpath(out, common.REPO))


if __name__ == "__main__":
    main()
