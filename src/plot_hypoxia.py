# -*- coding: utf-8 -*-
"""
Plot hypoxia probability heatmap from historical simulations.

For each (day-of-year, depth) cell, shows the percentage of years where
dissolved oxygen was below a threshold (default 4 mg/L).

Usage:
    python src/plot_hypoxia.py <lake_key> [options]

Options:
    --threshold MG_L    Oxygen threshold in mg/L (default: 4.0)
    --min-depth M       Minimum depth to show in metres (default: 15)
    --output PATH       Output file path (default: hypoxia_<lake>.png)
    --historical-dir    Path to historical runs root (default: runs/historical)
"""
import os
import sys
import argparse
import glob
from datetime import datetime, timedelta
import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# O2 molecular weight: 32 g/mol → 1 mmol/m³ = 0.032 mg/L
MMOL_PER_M3_TO_MG_PER_L = 32e-3


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("lake", type=str)
    parser.add_argument("--threshold", type=float, default=0.0, help="Oxygen threshold in mg/L")
    parser.add_argument("--min-depth", type=float, default=15.0, help="Minimum depth (m) to include")
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--historical-dir", type=str, default=None)
    return parser.parse_args()


def find_year_dirs(historical_dir, lake):
    """Return sorted list of (year_label, [nc_files]) tuples."""
    pattern = os.path.join(historical_dir, lake, "*", lake, "Results", "netcdf")
    netcdf_dirs = sorted(glob.glob(pattern))
    if not netcdf_dirs:
        raise FileNotFoundError(
            "No NetCDF output directories found matching: {}\n"
            "Have historical simulations been run for this lake?".format(pattern)
        )
    result = []
    for netcdf_dir in netcdf_dirs:
        # Path: .../historical/{lake}/{year_label}/{lake}/Results/netcdf
        parts = netcdf_dir.split(os.sep)
        results_idx = parts.index("Results")
        year_label = parts[results_idx - 3]
        files = sorted(glob.glob(os.path.join(netcdf_dir, "*.nc")))
        if files:
            result.append((year_label, files))
    return result


def load_year(year_label, nc_files, min_depth, threshold_mmol):
    """
    Load oxygen data for one simulation year.
    Returns xr.DataArray of bool (hypoxic) with dims (depth, dayofyear).
    """
    ds = xr.open_mfdataset(nc_files, combine="by_coords")
    oxy = ds["Oxygen"]  # mmol/m³, dims (depth, time)

    # Keep only depths <= -min_depth (depths are negative, e.g. -44 to 0)
    oxy = oxy.where(oxy.depth <= -min_depth, drop=True)

    # Daily mean grouped by day-of-year (handles sub-daily output)
    oxy_daily = oxy.groupby(oxy.time.dt.dayofyear).mean("time")
    # oxy_daily dims: (depth, dayofyear)

    hypoxic = oxy_daily < threshold_mmol
    ds.close()
    return hypoxic


def compute_hypoxia_probability(year_arrays):
    """
    Combine per-year boolean arrays into probability (0-100%).
    Uses outer join so leap-year day 366 is NaN for non-leap years.
    Returns xr.DataArray with dims (depth, dayofyear).
    """
    combined = xr.concat(year_arrays, dim="year", join="outer").astype(float)
    # mean over year dim, skipping NaN (e.g. day 366 in non-leap years)
    probability = combined.mean(dim="year", skipna=True) * 100.0
    return probability


def prepare_plot_data(probability):
    """Return (date_nums, depths, data_sorted) ready for plotting."""
    depths = -probability.depth.values  # negative → positive metres
    doys = probability.dayofyear.values.astype(int)
    data = probability.values  # shape (depth, dayofyear)

    # Map doy to real dates anchored at March of the current year.
    # doy >= march_start → this year; doy < march_start → next year.
    ref_year = datetime.now().year
    march_start_doy = datetime(ref_year, 3, 16).timetuple().tm_yday
    dates = np.array([
        datetime(ref_year if doy >= march_start_doy else ref_year + 1, 1, 1) + timedelta(days=int(doy) - 1)
        for doy in doys
    ])

    sort_idx = np.argsort(dates)
    dates_sorted = dates[sort_idx]
    data_sorted = data[:, sort_idx]
    return mdates.date2num(dates_sorted), depths, data_sorted


def _style_axes(ax):
    ax.invert_yaxis()
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    ax.set_ylabel("Depth (m)", fontsize=11)


def plot_heatmap(probability, lake, threshold_mgl, min_depth, output_path):
    date_nums, depths, data = prepare_plot_data(probability)

    fig, ax = plt.subplots(figsize=(14, 6))

    mesh = ax.pcolormesh(date_nums, depths, data, cmap="YlOrRd", vmin=0, vmax=100, shading="auto")

    cbar = fig.colorbar(mesh, ax=ax, pad=0.02)
    cbar.set_label("Probability O\u2082 < {} mg/L (%)".format(threshold_mgl), fontsize=11)

    _style_axes(ax)
    ax.set_title(
        "{} \u2014 Hypoxia probability heatmap (O\u2082 < {} mg/L)".format(lake, threshold_mgl),
        fontsize=13,
    )

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    print("Saved: {}".format(output_path))


def plot_contour(probability, lake, threshold_mgl, min_depth, output_path):
    date_nums, depths, data = prepare_plot_data(probability)

    fig, ax = plt.subplots(figsize=(14, 6))

    levels = np.arange(0, 101, 10)
    cf = ax.contourf(date_nums, depths, data, levels=levels, cmap="YlOrRd", vmin=0, vmax=100)
    cs = ax.contour(date_nums, depths, data, levels=levels, colors="k", linewidths=0.4, alpha=0.4)
    ax.clabel(cs, fmt="%g%%", fontsize=7, inline=True)

    cbar = fig.colorbar(cf, ax=ax, pad=0.02)
    cbar.set_label("Probability O\u2082 < {} mg/L (%)".format(threshold_mgl), fontsize=11)

    _style_axes(ax)
    ax.set_title(
        "{} \u2014 Hypoxia probability contour (O\u2082 < {} mg/L)".format(lake, threshold_mgl),
        fontsize=13,
    )

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    print("Saved: {}".format(output_path))


def main():
    args = parse_args()
    lake = args.lake
    threshold_mmol = args.threshold / MMOL_PER_M3_TO_MG_PER_L

    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    historical_dir = args.historical_dir or os.path.join(repo_dir, "runs", "historical")
    output_path = args.output or os.path.join(repo_dir, "hypoxia_{}.png".format(lake))

    print("Finding simulation years for '{}'...".format(lake))
    year_dirs = find_year_dirs(historical_dir, lake)
    print("  Found {} years: {}".format(len(year_dirs), ", ".join(y for y, _ in year_dirs)))

    print("Loading oxygen data (threshold: {} mg/L = {:.1f} mmol/m3)...".format(
        args.threshold, threshold_mmol
    ))
    year_arrays = []
    for year_label, nc_files in year_dirs:
        print("  {}...".format(year_label), end=" ", flush=True)
        hypoxic = load_year(year_label, nc_files, args.min_depth, threshold_mmol)
        year_arrays.append(hypoxic)
        print("ok")

    print("Computing hypoxia probability...")
    probability = compute_hypoxia_probability(year_arrays)

    print("Plotting...")
    base = output_path.rsplit(".", 1)
    heatmap_path = "{}_heatmap.{}".format(base[0], base[1]) if len(base) == 2 else output_path + "_heatmap"
    contour_path = "{}_contour.{}".format(base[0], base[1]) if len(base) == 2 else output_path + "_contour"
    plot_heatmap(probability, lake, args.threshold, args.min_depth, heatmap_path)
    plot_contour(probability, lake, args.threshold, args.min_depth, contour_path)


if __name__ == "__main__":
    main()
