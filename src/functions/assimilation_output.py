"""The assimilation run's published output: spin-up, assimilation and forecast temperature
combined into one series, written as NetCDF like the operational forecast."""
import os
import glob
import netCDF4
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta


def _read_temperature_file(path):
    """A T_out.dat-style file -> (days since the reference date, depths, values[time, depth])."""
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
                continue                    # the "Depths [m]" header
    if not depths:
        return None
    return np.unique(np.array(depths))


def _model_output_resolution(max_depth):
    """Regular output-depth spacing (m), mirroring model.create_output_depths_file."""
    if max_depth > 20:
        return 1.0
    if max_depth > 10:
        return 0.5
    if max_depth > 5:
        return 0.25
    return 0.1


def _model_grid(run_dir, source_paths, parameters=None):
    """The published depth axis: the model's regular output grid. The assimilation and forecast
    also output the observation depths, which are dropped here. Without the lake's max_depth, or a
    forecast, the forecast's or first source's grid as it is."""
    forecast_results = os.path.join(run_dir, "forecast", "Results", "T_out.dat")
    if os.path.exists(forecast_results):
        grid = _read_temperature_file(forecast_results)[1]
    else:
        grid = _read_z_out(os.path.join(run_dir, "forecast", "z_out.dat"))
        if grid is None:
            grid = _read_temperature_file(source_paths[0])[1]
    if parameters is None or "max_depth" not in parameters:
        return grid
    res = _model_output_resolution(parameters["max_depth"])
    lattice = np.isclose(grid / res, np.round(grid / res), atol=1e-3 / res, rtol=0)
    return grid[lattice]


def _align_to_grid(depths, values, grid):
    """A [time, depth] matrix on `grid`: its columns where the depths match, else interpolated."""
    idx = np.full(len(grid), -1, dtype=int)
    for j, g in enumerate(grid):
        match = np.where(np.isclose(depths, g, atol=1e-3))[0]
        if len(match):
            idx[j] = match[0]
    if np.all(idx >= 0):
        return values[:, idx]
    out = np.empty((values.shape[0], len(grid)))
    for t in range(values.shape[0]):
        out[t, :] = np.interp(grid, depths, values[t, :])
    return out


SOURCES = ("T_out_spinup.dat",                                   # the cold start's spin-up
           os.path.join("assimilation", "T_out_*_mean.dat"),     # the assimilation's ensemble mean
           os.path.join("forecast", "Results", "T_out.dat"))     # the forecast


def combine_temperature(run_dir, parameters=None):
    """The spin-up, assimilation and forecast temperatures on one depth axis, in time order:
    (days, depths, values). Where two overlap the earlier wins, so the analysis beats the forecast."""
    sources = [p for s in SOURCES
               for p in sorted(glob.glob(os.path.join(run_dir, s)), key=os.path.getmtime, reverse=True)]
    if not sources:
        raise FileNotFoundError("No temperature output files found under {}".format(run_dir))

    grid = _model_grid(run_dir, sources, parameters)
    times_parts, value_parts = [], []
    for s in sources:
        t, d, v = _read_temperature_file(s)
        times_parts.append(t)
        value_parts.append(_align_to_grid(d, v, grid))
    times = np.concatenate(times_parts)
    values = np.concatenate(value_parts, axis=0)

    # Stable: of two equal times, the earlier source's row comes first and is kept.
    order = np.argsort(times, kind="stable")
    times, values = times[order], values[order]
    keep = np.concatenate(([True], np.round(np.diff(times), 6) > 0))
    return times[keep], grid, values[keep, :]


def _write_combined_dat(path, times, depths, values):
    df = pd.DataFrame(values, columns=["{:.3f}".format(d) for d in depths])
    df.insert(0, "Datetime", times)
    df.to_csv(path, index=False, float_format="%.4f")


def post_process_temperature(run_dir, version, parameters, changed_since=None, clear_netcdf=False):
    """Write the combined temperature to <run_dir>/T_out_combined.dat and as monthly NetCDF files in
    <run_dir>/netcdf/, as post_process does for the forecast. With `changed_since`, only the months
    from its month onward are written; `clear_netcdf` empties the folder first."""
    reference_date = parameters.get("reference_date", "19810101")
    if isinstance(reference_date, str):
        try:
            reference_date = datetime.fromisoformat(reference_date)
        except ValueError:
            reference_date = datetime.strptime(reference_date, "%Y%m%d")
    if reference_date.tzinfo is None:
        reference_date = reference_date.replace(tzinfo=timezone.utc)

    print("Combining assimilation and forecast temperature")
    times_days, depths, temperature = combine_temperature(run_dir, parameters)
    _write_combined_dat(os.path.join(run_dir, "T_out_combined.dat"), times_days, depths, temperature)
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

    if clear_netcdf:
        for f in glob.glob(os.path.join(out_dir, "*.nc")):
            os.remove(f)

    changed_start = None
    if changed_since is not None:
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        changed_start = datetime(changed_since.year, changed_since.month, 1).replace(tzinfo=timezone.utc)

    print("Writing temperature NetCDF files")
    for i in range(delta.years * 12 + delta.months):
        file_start = start + relativedelta(months=i)
        if changed_start is not None and file_start < changed_start:
            continue
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
    print("Wrote {} and NetCDF to {}".format("T_out_combined.dat", out_dir))
