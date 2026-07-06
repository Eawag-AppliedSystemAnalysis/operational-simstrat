import os
import sys
import glob
import json
import pylake
import netCDF4
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta


def post_process(start_date, results, version, parameters):
    dimensions = {
        'time': {'dim_name': 'time', 'dim_size': None},
        'depth': {'dim_name': 'depth', 'dim_size': None}
    }
    general_attributes = {
        "title": "Operation Simstrat Simulation for {}".format(parameters["name"]),
        "simstrat_version": version,
        "institution": "Eawag",
        "conventions": "CF 1.7",
        "produced": str(datetime.now()),
    }
    for k in parameters.keys():
        if k != "name":
            general_attributes[k] = str(parameters[k])

    variables = {
        'time': {'var_name': 'time', 'dim': ('time',), 'unit': 'seconds since 1970-01-01 00:00:00', 'long_name': 'time'},
        'depth': {'var_name': 'depth', 'dim': ('depth',), 'unit': 'm', 'long_name': 'depth'},
        'U': {'var_name': 'U', 'dim': ('depth', 'time',), 'unit': 'm/s', 'long_name': 'Water velocity (East direction)'},
        'V': {'var_name': 'V', 'dim': ('depth', 'time',), 'unit': 'm/s', 'long_name': 'Water velocity (North direction)'},
        'T': {'var_name': 'T', 'dim': ('depth', 'time',), 'unit': 'degC', 'long_name': 'Temperature'},
        'S': {'var_name': 'S', 'dim': ('depth', 'time',), 'unit': 'ppt', 'long_name': 'Salinity'},
        'k': {'var_name': 'k', 'dim': ('depth', 'time',), 'unit': 'J/kg', 'long_name': 'Turbulent kinetic energy'},
        'eps': {'var_name': 'eps', 'dim': ('depth', 'time',), 'unit': 'W/kg', 'long_name': 'Dissipation rate of turbulent kinetic energy'},
        'nuh': {'var_name': 'nuh', 'dim': ('depth', 'time',), 'unit': 'Js/kg', 'long_name': 'Turbulent diffusivity of temperature'},
        'num': {'var_name': 'num', 'dim': ('depth', 'time',), 'unit': 'm2/s', 'long_name': 'Turbulent diffusivity of momentum'},
        'NN': {'var_name': 'NN', 'dim': ('depth', 'time',), 'unit': 's-2', 'long_name': 'Brunt-Väisälä frequency (stratification coefficient)'},
        'B': {'var_name': 'B', 'dim': ('depth', 'time',), 'unit': 'W/kg', 'long_name': 'Production rate of buoyancy'},
        'P': {'var_name': 'P', 'dim': ('depth', 'time',), 'unit': 'W/kg', 'long_name': 'Production rate of shear stress'},
        'Ps': {'var_name': 'Ps', 'dim': ('depth', 'time',), 'unit': 'W/kg', 'long_name': 'Production rate of seiche energy'},
        'HA': {'var_name': 'HA', 'dim': ('time',), 'unit': 'W/m2', 'long_name': 'Long-wave radiation from sky'},
        'HW': {'var_name': 'HW', 'dim': ('time',), 'unit': 'W/m2', 'long_name': 'Long-wave radiation from water'},
        'HK': {'var_name': 'HK', 'dim': ('time',), 'unit': 'W/m2', 'long_name': 'Sensible heat flux'},
        'HV': {'var_name': 'HV', 'dim': ('time',), 'unit': 'W/m2', 'long_name': 'Latent heat flux'},
        'Rad0': {'var_name': 'Rad0', 'dim': ('time',), 'unit': 'W/m2', 'long_name': 'Solar radiation penetrating lake'},
        'TotalIceH': {'var_name': 'TotalIceH', 'dim': ('time',), 'unit': 'm', 'long_name': 'Total ice thickness'},
        'BlackIceH': {'var_name': 'BlackIceH', 'dim': ('time',), 'unit': 'm', 'long_name': 'Black ice thickness'},
        'WhiteIceH': {'var_name': 'WhiteIceH', 'dim': ('time',), 'unit': 'm', 'long_name': 'White ice thickness'},
        'SnowH': {'var_name': 'SnowH', 'dim': ('time',), 'unit': 'm', 'long_name': 'Snow height above ice'},
        'WaterH': {'var_name': 'WaterH', 'dim': ('time',), 'unit': 'm', 'long_name': 'Water depth (positive height above sediment)'},
        'Qvert': {'var_name': 'Qvert', 'dim': ('depth', 'time',), 'unit': 'm3/s', 'long_name': 'Vertical advection'},
        'Eseiche': {'var_name': 'Eseiche', 'dim': ('time',), 'unit': 'J', 'long_name': 'Total seiche energy'},
        'OXY_oxy': {'var_name': 'Oxygen', 'dim': ('depth', 'time',), 'unit': 'mmol/m3', 'long_name': 'Dissolved oxygen'},
        'OXY_sat': {'var_name': 'OxygenSat', 'dim': ('depth', 'time',), 'unit': '%', 'long_name': 'Oxygen saturation'},
        'Thermocline': {'var_name': 'Thermocline', 'dim': ('time',), 'unit': 'm', 'long_name': 'Thermocline depth', 'calculated': True}
    }
    result_files = os.listdir(results)
    keys = list(variables.keys())
    for var in keys:
        if not (var in dimensions or "{}_out.dat".format(var) in result_files) and 'calculated' not in variables[var]:
            variables.pop(var)

    print("Reading data from simulation files")
    df = pd.read_csv(os.path.join(results, "T_out.dat"))
    time = np.array([parameters["reference_date"] + timedelta(days=t) for t in np.array(df["Datetime"])])
    depths = np.array(df.columns[1:]).astype(float)
    min_time = min(time)
    max_time = max(time)
    start = datetime(min_time.year, min_time.month, 1).replace(tzinfo=timezone.utc)
    end = datetime(max_time.year, max_time.month, 1).replace(tzinfo=timezone.utc) + relativedelta(months=1)
    delta = relativedelta(end, start)

    os.makedirs(os.path.join(results, "netcdf"), exist_ok=True)

    data_dict = {}
    for key, values in variables.items():
        if key not in dimensions and 'calculated' not in values:
            df = pd.read_csv(os.path.join(results, "{}_out.dat".format(key)))
            df = df.drop('Datetime', axis=1)
            data_dict[key] = df.values

    print("Calculating products")
    data_dict["Thermocline"] = thermocline(data_dict["T"], time, depths)

    print("Writing outputs to NetCDF")
    for i in range(delta.years * 12 + delta.months):
        file_start = start + relativedelta(months=i)
        if file_start >= start_date:
            file_end = file_start + relativedelta(months=1)
            time_mask = (time >= file_start) & (time < file_end)
            dimensions_data = {"time": [datetime.timestamp(t) for t in time[time_mask]], "depth": depths}
            file_name = os.path.join(results, "netcdf", "{}.nc".format(file_start.strftime('%Y%m')))
            with netCDF4.Dataset(file_name, mode='w', format='NETCDF4') as nc:
                for key in general_attributes:
                    setattr(nc, key, general_attributes[key])
                for key, values in dimensions.items():
                    nc.createDimension(values['dim_name'], len(dimensions_data[key]))
                for key, values in variables.items():
                    var = nc.createVariable(values["var_name"], np.float64, values["dim"], fill_value=np.nan)
                    var.units = values["unit"]
                    var.long_name = values["long_name"]
                    if key in dimensions:
                        var[:] = dimensions_data[key]
                    else:
                        if len(data_dict[key].shape) == 2:
                            data = data_dict[key][time_mask, :]
                        else:
                            data = data_dict[key][time_mask]
                        if len(values["dim"]) > 1:
                            data = data.T
                        var[:] = data


def thermocline(temperature, time, depths):
    td = np.full(len(time), np.nan)
    try:
        thermocline_depth, thermocline_index = pylake.thermocline(temperature, depth=depths * -1, time=time)
        td = thermocline_depth.values
    except Exception as e:
        print("Failed to calculate thermocline", e)
    return td

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
    """Canonical depth axis = the original model output grid. The assimilation and obs-aligned
    forecast grids are the model grid plus observation-only depths (data-assimilation
    set_output_depths builds them as a union), so restrict to depths on the regular output lattice
    and drop the obs-only levels. Falls back to the forecast/first-source grid unchanged when the
    resolution is unknown or no forecast output exists (e.g. an assimilation-only run)."""
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
    """Map a [time, depth] matrix onto the canonical grid. Model-grid depths are a subset of the
    source depths, so this is exact column selection; an interpolation fallback covers the
    defensive case of a grid depth with no matching source column."""
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


def combine_temperature(run_dir, sources=None, parameters=None):
    """Combine the available temperature sources (in chronological order) onto a common model-grid
    depth axis. Returns (times, depths, values) sorted in time with duplicate timestamps removed —
    keeping the earlier source's value, so the assimilation analysis wins over the forecast at the
    seam. Robust to daily appends that can leave boundary-duplicate timestamps."""
    DEFAULT_SOURCES = (
        "T_out_spinup.dat",                                       # pre-assimilation spin-up (run root)
        os.path.join("assimilation", "T_out_*_mean.dat"),        # assimilation period (assimilation/)
        os.path.join("forecast", "Results", "T_out.dat"),        # forecast period
    )
    if sources is None:
        sources = [p for s in DEFAULT_SOURCES
                   for p in sorted(glob.glob(os.path.join(run_dir, s)), key=os.path.getmtime, reverse=True)]
    else:
        sources = [s for s in sources if os.path.exists(s)]
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


def post_process_temperature(run_dir, version, parameters, sources=None, changed_since=None, clear_netcdf=False):
    """Combine assimilation + forecast temperature, write <run_dir>/T_out_combined.dat and monthly
    NetCDF files in <run_dir>/netcdf/ (temperature only), matching post_process.

    On a continuing run only the data from the assimilation start onward is recomputed; pass that
    date as ``changed_since`` and ``clear_netcdf=True`` to empty <run_dir>/netcdf/ and only rewrite
    monthly files from the first of ``changed_since``'s month onward (the changed data)."""
    reference_date = parameters.get("reference_date", "19810101")
    if isinstance(reference_date, str):
        try:
            reference_date = datetime.fromisoformat(reference_date)
        except ValueError:
            reference_date = datetime.strptime(reference_date, "%Y%m%d")
    if reference_date.tzinfo is None:
        reference_date = reference_date.replace(tzinfo=timezone.utc)

    print("Combining assimilation and forecast temperature")
    times_days, depths, temperature = combine_temperature(run_dir, sources, parameters)
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


if __name__ == "__main__":
    input_file = os.path.join(sys.argv[1], "inputs.json")
    if not os.path.exists(input_file):
        raise ValueError("Input file not found.")
    with open(input_file, 'r', encoding='utf-8') as file:
        inputs = json.load(file)
    inputs["start_date"] = datetime.fromisoformat(inputs["start_date"])
    inputs["parameters"]["reference_date"] = datetime.fromisoformat(inputs["parameters"]["reference_date"])
    post_process(inputs["start_date"], inputs["folder"], inputs["version"], inputs["parameters"])
