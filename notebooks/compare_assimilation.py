#!/usr/bin/env python
"""Compare a normal Simstrat run against a data-assimilation run at a given depth.

Reads the temperature NetCDF output of both runs, extracts the temperature
time series at the requested depth, and overlays the in-situ observations used
for the assimilation.

Example:
    python compare_assimilation.py --depth 10
    python compare_assimilation.py --depth 0 --start 2024-03-01 --end 2024-07-01 --out compare.png
"""
import os
import glob
import argparse

import numpy as np
import pandas as pd
import xarray as xr
import matplotlib.pyplot as plt

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Defaults: all the data in the NetCDF output of each run.
DEFAULT_NORMAL = os.path.join(REPO, "runs", "upperlugano", "Results", "netcdf")
DEFAULT_DA = os.path.join(REPO, "runs", "upperlugano_assimilate", "netcdf")
DEFAULT_OBS = os.path.join(REPO, "runs", "upperlugano_assimilate", "observations", "temperature.csv")


def load_run(netcdf_dir, depth):
    """Return (time, temperature, actual_depth) at the depth closest to `depth` (metres below
    surface) from all NetCDF files in `netcdf_dir`, or None if the directory has no files."""
    files = sorted(glob.glob(os.path.join(netcdf_dir, "*.nc")))
    if not files:
        return None
    with xr.open_mfdataset(files, combine="by_coords") as ds:
        # depth is stored as negative metres; match the requested (positive) depth.
        sel = ds["T"].sel(depth=-abs(depth), method="nearest")
        time = pd.to_datetime(ds["time"].values)
        temperature = np.asarray(sel.values)
        actual_depth = float(sel["depth"].values)
    order = np.argsort(time)
    return time[order], temperature[order], actual_depth


def load_observations(obs_csv, depth):
    """Return the observations (time, value, actual_depth) at the observed depth closest to
    `depth`, or None if the file is missing/empty."""
    if not os.path.exists(obs_csv):
        return None
    obs = pd.read_csv(obs_csv, parse_dates=["time"])
    if obs.empty:
        return None
    obs["time"] = pd.to_datetime(obs["time"], utc=True).dt.tz_localize(None)
    available = obs["depth"].unique()
    actual_depth = float(available[np.argmin(np.abs(available - abs(depth)))])
    at_depth = obs[obs["depth"] == actual_depth].sort_values("time")
    return at_depth["time"].values, at_depth["value"].values, actual_depth


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--depth", type=float, default=0.0,
                        help="Depth in metres below the surface (default: 0)")
    parser.add_argument("--normal", default=DEFAULT_NORMAL, help="NetCDF dir for the normal run")
    parser.add_argument("--da", default=DEFAULT_DA, help="NetCDF dir for the assimilation run")
    parser.add_argument("--obs", default=DEFAULT_OBS, help="Observation CSV (time,depth,value)")
    parser.add_argument("--start", default=None, help="Plot start date, e.g. 2024-03-01")
    parser.add_argument("--end", default=None, help="Plot end date, e.g. 2024-07-01")
    parser.add_argument("--out", default=None, help="Save the figure to this path instead of showing it")
    args = parser.parse_args()

    fig, ax = plt.subplots(figsize=(12, 6))

    normal = load_run(args.normal, args.depth)
    if normal is not None:
        time, temp, d = normal
        ax.plot(time, temp, label="Normal run ({:.1f} m)".format(abs(d)), color="tab:blue")
    else:
        print("No NetCDF files in {} (normal run skipped)".format(args.normal))

    da = load_run(args.da, args.depth)
    if da is not None:
        time, temp, d = da
        ax.plot(time, temp, label="Assimilation run ({:.1f} m)".format(abs(d)), color="tab:orange")
    else:
        print("No NetCDF files in {} (assimilation run skipped)".format(args.da))

    obs = load_observations(args.obs, args.depth)
    if obs is not None:
        otime, ovalue, d = obs
        ax.scatter(otime, ovalue, label="Observations ({:.1f} m)".format(d),
                   color="black", s=20, zorder=5)
    else:
        print("No observations found in {}".format(args.obs))

    if args.start:
        ax.set_xlim(left=pd.to_datetime(args.start))
    if args.end:
        ax.set_xlim(right=pd.to_datetime(args.end))

    ax.set_xlabel("Date")
    ax.set_ylabel("Temperature (°C)")
    ax.set_title("Normal vs. data-assimilation run at {:.1f} m".format(abs(args.depth)))
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    if args.out:
        fig.savefig(args.out, dpi=150)
        print("Saved figure to {}".format(args.out))
    else:
        plt.show()


if __name__ == "__main__":
    main()
