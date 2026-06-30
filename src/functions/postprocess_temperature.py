import os
import sys
import json
import netCDF4
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta

# Combine the assimilation-period ensemble-mean temperature (T_out_enkf_mean.dat, written by the
# data-assimilation submodule's accumulate_mean) with the forecast-period temperature
# (forecast/Results/T_out.dat) into a single continuous series, write it as a combined .dat file
# and convert it to monthly NetCDF in the same layout as postprocess.post_process (temperature
# only). Designed to be re-run every day: it reads the full series each time and overwrites its
# outputs idempotently.

DEFAULT_SOURCES = (
    "T_out_enkf_mean.dat",                      # assimilation period (run root)
    os.path.join("forecast", "Results", "T_out.dat"),  # forecast period
)
COMBINED_DAT = "T_out_combined.dat"
DEPTH_TOL = 1e-3  # m; model-grid depths match DA columns within this


def _read_temperature_file(path):
    """Read a Simstrat-style T_out.dat / T_out_enkf_mean.dat file.

    Returns (times, depths, values): times is days since the reference date (float),
    depths is an increasing float array (deepest -> surface), values is [time, depth] degC.
    """
    df = pd.read_csv(path)
    df.columns = [c.strip().strip('"') for c in df.columns]
    times = df.iloc[:, 0].to_numpy(dtype=float)
    depths = np.array([float(c) for c in df.columns[1:]])
    values = df.iloc[:, 1:].to_numpy(dtype=float)
    return times, depths, values


def _read_z_out(path):
    """Model-grid depths (increasing, deepest -> surface) from a z_out.dat, or None."""
    if not os.path.exists(path):
        return None
    depths = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            try:
                depths.append(float(line))
            except ValueError:
                continue  # skip the 'Depths [m]' header
    if not depths:
        return None
    return np.unique(np.array(depths))


def _model_grid(run_dir, source_paths):
    """Canonical depth axis = the model output grid. The assimilation grid is the model grid plus
    extra observation depths (data-assimilation set_output_depths builds it as a union), so we use
    the forecast's model grid and drop the obs-only levels. Falls back to the first source's grid
    when no forecast output exists (e.g. an assimilation-only run)."""
    forecast_results = os.path.join(run_dir, "forecast", "Results", "T_out.dat")
    if os.path.exists(forecast_results):
        return _read_temperature_file(forecast_results)[1]
    grid = _read_z_out(os.path.join(run_dir, "forecast", "z_out.dat"))
    if grid is not None:
        return grid
    return _read_temperature_file(source_paths[0])[1]


def _align_to_grid(depths, values, grid):
    """Map a [time, depth] matrix onto the canonical grid. Model-grid depths are a subset of the
    source depths, so this is exact column selection; an interpolation fallback covers the
    defensive case of a grid depth with no matching source column."""
    idx = np.full(len(grid), -1, dtype=int)
    for j, g in enumerate(grid):
        match = np.where(np.isclose(depths, g, atol=DEPTH_TOL))[0]
        if len(match):
            idx[j] = match[0]
    if np.all(idx >= 0):
        return values[:, idx]
    out = np.empty((values.shape[0], len(grid)))
    for t in range(values.shape[0]):
        out[t, :] = np.interp(grid, depths, values[t, :])
    return out


def combine_temperature(run_dir, sources=None):
    """Combine the available temperature sources (in chronological order) onto a common model-grid
    depth axis. Returns (times, depths, values) sorted in time with duplicate timestamps removed —
    keeping the earlier source's value, so the assimilation analysis wins over the forecast at the
    seam. Robust to daily appends that can leave boundary-duplicate timestamps."""
    if sources is None:
        sources = [os.path.join(run_dir, s) for s in DEFAULT_SOURCES]
    sources = [s for s in sources if os.path.exists(s)]
    if not sources:
        raise FileNotFoundError("No temperature output files found under {}".format(run_dir))

    grid = _model_grid(run_dir, sources)
    times_parts, value_parts = [], []
    for s in sources:
        t, d, v = _read_temperature_file(s)
        times_parts.append(t)
        value_parts.append(_align_to_grid(d, v, grid))
    times = np.concatenate(times_parts)
    values = np.concatenate(value_parts, axis=0)

    # Stable sort keeps each source's order for tied timestamps (sources concatenated
    # chronologically), so the first of each duplicate is the earlier source -> DA over forecast.
    order = np.argsort(times, kind="stable")
    times, values = times[order], values[order]
    keep = np.concatenate(([True], np.round(np.diff(times), 6) > 0))
    return times[keep], grid, values[keep, :]


def _write_combined_dat(path, times, depths, values):
    df = pd.DataFrame(values, columns=["{:.3f}".format(d) for d in depths])
    df.insert(0, "Datetime", times)
    df.to_csv(path, index=False, float_format="%.4f")


def post_process_temperature(run_dir, version, parameters, sources=None):
    """Combine assimilation + forecast temperature, write <run_dir>/T_out_combined.dat and monthly
    NetCDF files in <run_dir>/netcdf/ (temperature only), matching postprocess.post_process."""
    reference_date = parameters.get("reference_date", "19810101")
    if isinstance(reference_date, str):
        try:
            reference_date = datetime.fromisoformat(reference_date)
        except ValueError:
            reference_date = datetime.strptime(reference_date, "%Y%m%d")
    if reference_date.tzinfo is None:
        reference_date = reference_date.replace(tzinfo=timezone.utc)

    print("Combining assimilation and forecast temperature")
    times_days, depths, temperature = combine_temperature(run_dir, sources)
    _write_combined_dat(os.path.join(run_dir, COMBINED_DAT), times_days, depths, temperature)
    time = np.array([reference_date + timedelta(days=float(t)) for t in times_days])

    general_attributes = {
        "title": "Operational Simstrat Assimilation Temperature for {}".format(parameters.get("name", run_dir)),
        "simstrat_version": version,
        "institution": "Eawag",
        "conventions": "CF 1.7",
        "produced": str(datetime.now()),
    }
    for k in parameters.keys():
        if k != "name":
            general_attributes[k] = str(parameters[k])

    dimensions = {
        "time": {"dim_name": "time", "dim_size": None},
        "depth": {"dim_name": "depth", "dim_size": None},
    }
    variables = {
        "time": {"var_name": "time", "dim": ("time",), "unit": "seconds since 1970-01-01 00:00:00", "long_name": "time"},
        "depth": {"var_name": "depth", "dim": ("depth",), "unit": "m", "long_name": "depth"},
        "T": {"var_name": "T", "dim": ("depth", "time",), "unit": "degC", "long_name": "Temperature"},
    }

    min_time, max_time = min(time), max(time)
    start = datetime(min_time.year, min_time.month, 1).replace(tzinfo=timezone.utc)
    end = datetime(max_time.year, max_time.month, 1).replace(tzinfo=timezone.utc) + relativedelta(months=1)
    delta = relativedelta(end, start)

    out_dir = os.path.join(run_dir, "netcdf")
    os.makedirs(out_dir, exist_ok=True)

    print("Writing temperature NetCDF files")
    for i in range(delta.years * 12 + delta.months):
        file_start = start + relativedelta(months=i)
        file_end = file_start + relativedelta(months=1)
        time_mask = (time >= file_start) & (time < file_end)
        if not time_mask.any():
            continue
        dimensions_data = {"time": [datetime.timestamp(t) for t in time[time_mask]], "depth": depths}
        file_name = os.path.join(out_dir, "{}.nc".format(file_start.strftime("%Y%m")))
        with netCDF4.Dataset(file_name, mode="w", format="NETCDF4") as nc:
            for key in general_attributes:
                setattr(nc, key, general_attributes[key])
            for key, values in dimensions.items():
                nc.createDimension(values["dim_name"], len(dimensions_data[key]))
            for key, values in variables.items():
                var = nc.createVariable(values["var_name"], np.float64, values["dim"], fill_value=np.nan)
                var.units = values["unit"]
                var.long_name = values["long_name"]
                if key in dimensions:
                    var[:] = dimensions_data[key]
                else:
                    data = temperature[time_mask, :]
                    if len(values["dim"]) > 1:
                        data = data.T
                    var[:] = data
    print("Wrote {} and NetCDF to {}".format(COMBINED_DAT, out_dir))


if __name__ == "__main__":
    # Usage: python postprocess_temperature.py <run_dir> [reference_date YYYYMMDD] [version]
    # <run_dir> e.g. runs/<key>_assimilate (holds T_out_enkf_mean.dat and forecast/Results/).
    run_dir = sys.argv[1]
    reference_date = sys.argv[2] if len(sys.argv) > 2 else "19810101"
    version = sys.argv[3] if len(sys.argv) > 3 else "3.0.4"

    key = os.path.basename(os.path.normpath(run_dir)).replace("_assimilate", "")
    parameters = {"name": key, "reference_date": reference_date}
    repo_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    lake_parameters_file = os.path.join(repo_dir, "static", "lake_parameters.json")
    if os.path.exists(lake_parameters_file):
        with open(lake_parameters_file) as f:
            for lake in json.load(f):
                if lake.get("key") == key:
                    parameters = dict(lake)
                    parameters["reference_date"] = reference_date
                    break

    post_process_temperature(run_dir, version, parameters)
