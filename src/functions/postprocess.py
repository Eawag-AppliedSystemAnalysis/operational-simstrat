import os
import sys
import json
import pylake
import netCDF4
import traceback
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta

# Minimum half-spread (degC) used to normalise deviations when picking the ensemble median run, so points where all
# members agree (first forecast hours, deep water) do not decide the selection on numerical noise.
SPREAD_FLOOR = 0.05


def post_process(start_date, results, version, parameters, ensemble=None, forecast_source=None, forecast_start=None):
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

    # Attributes and band variables describing the forecast, only written to the monthly files that contain it
    forecast_attributes = {}
    if forecast_start is None and ensemble:
        forecast_start = ensemble["forecast_start"]
    if forecast_start is not None:
        forecast_start = datetime.fromisoformat(forecast_start)
        forecast_attributes["forecast_start"] = forecast_start.isoformat()
    if forecast_source:
        forecast_attributes["forecast_source"] = forecast_source
    if ensemble:
        try:
            data_dict, bands, ensemble_attributes = apply_ensemble(data_dict, time, depths, ensemble,
                                                                   parameters["reference_date"])
            data_dict.update(bands)
            forecast_attributes.update(ensemble_attributes)
            for key in bands:
                variables[key] = {'var_name': key, 'dim': ('depth', 'time',), 'unit': 'degC', 'calculated': True,
                                  'zlib': True, 'forecast_only': True,
                                  'long_name': 'Temperature {}th percentile of the {} ensemble'.format(
                                      key.split("_p")[-1], ensemble["model"])}
        except Exception as e:
            print("Failed to apply ensemble forecast, writing the deterministic forecast ({}) instead:".format(
                forecast_source), e)
            traceback.print_exc()

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
            contains_forecast = forecast_start is not None and file_end > forecast_start
            with netCDF4.Dataset(file_name, mode='w', format='NETCDF4') as nc:
                for key in general_attributes:
                    setattr(nc, key, general_attributes[key])
                if contains_forecast:
                    for key in forecast_attributes:
                        setattr(nc, key, forecast_attributes[key])
                for key, values in dimensions.items():
                    nc.createDimension(values['dim_name'], len(dimensions_data[key]))
                for key, values in variables.items():
                    if values.get("forecast_only") and not contains_forecast:
                        continue
                    var = nc.createVariable(values["var_name"], np.float64, values["dim"], fill_value=np.nan,
                                            zlib=values.get("zlib", False))
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


def apply_ensemble(data_dict, time, depths, ensemble, reference_date):
    """Replace the forecast rows of every output variable with the median run: the ensemble member whose temperature
    is closest to the pointwise median temperature of all members. Closeness is the RMS over all forecast depths and
    times of the deviation from the median divided by the local half-spread (p90 - p10) / 2, floored at
    SPREAD_FLOOR, so points where the ensemble is tight (early forecast) weigh as much as points where it is wide
    and the chosen member stays inside the p10-p90 band as much as possible.
    Also returns T_p10 / T_p90, the pointwise 10th / 90th percentiles of all members' temperature.

    Returns (new data_dict, bands, attributes) and leaves the input data_dict untouched."""
    forecast_start = datetime.fromisoformat(ensemble["forecast_start"])
    # The base leg ending at the members' restart snapshot (F + 1 h) writes an output the members do not have
    first_member_day = pd.read_csv(os.path.join(ensemble["results"][0], "T_out.dat"), usecols=[0]).iloc[0, 0]
    all_days = np.array([(t - reference_date).total_seconds() / 86400 for t in time])
    mask = (time >= forecast_start) & (all_days >= first_member_day - 1e-3)
    n_forecast = int(mask.sum())
    if n_forecast == 0:
        raise ValueError("No base output after the forecast start {}".format(forecast_start))
    base_days = all_days[mask]

    def read_member(folder, key):
        df = pd.read_csv(os.path.join(folder, "{}_out.dat".format(key)))
        member_days = df["Datetime"].to_numpy(dtype=float)
        if len(member_days) != n_forecast or not np.allclose(member_days, base_days, rtol=0, atol=1e-3):
            raise ValueError("{} output times in {} do not match the base run forecast times".format(key, folder))
        if key == "T" and not np.allclose(np.array(df.columns[1:]).astype(float), depths):
            raise ValueError("Output depths in {} do not match the base run".format(folder))
        return df.drop('Datetime', axis=1).values

    temperature = np.stack([read_member(folder, "T") for folder in ensemble["results"]])
    median = np.nanmedian(temperature, axis=0)
    p10, p90 = np.nanpercentile(temperature, (10, 90), axis=0)
    half_spread = np.maximum((p90 - p10) / 2, SPREAD_FLOOR)
    score = np.sqrt(np.nanmean(((temperature - median) / half_spread) ** 2, axis=(1, 2)))
    chosen = int(np.nanargmin(score))
    median_member = ensemble["members"][chosen]
    within = np.nanmean((temperature[chosen] >= p10) & (temperature[chosen] <= p90))
    print("Median run is member {} (spread-normalised RMS {:.3f}, {:.0%} of points within p10-p90)".format(
        median_member, score[chosen], within))

    output = {}
    for key, values in data_dict.items():
        member_values = temperature[chosen] if key == "T" else read_member(ensemble["results"][chosen], key)
        if member_values.shape != values[mask].shape:
            raise ValueError("{} output shape of member {} does not match the base run".format(key, median_member))
        merged = values.astype(float, copy=True)
        merged[mask] = member_values
        output[key] = merged

    bands = {}
    for percentile, values in ((10, p10), (90, p90)):
        band = np.full(data_dict["T"].shape, np.nan)
        band[mask] = values
        bands["T_p{}".format(percentile)] = band

    attributes = {
        "forecast_source": "{} median run".format(ensemble["model"]),
        "forecast_start": forecast_start.isoformat(),
        "ensemble_members": len(ensemble["members"]),
        "median_member": median_member,
    }
    return output, bands, attributes


def thermocline(temperature, time, depths):
    td = np.full(len(time), np.nan)
    try:
        thermocline_depth, thermocline_index = pylake.thermocline(temperature, depth=depths * -1, time=time)
        td = thermocline_depth.values
    except Exception as e:
        print("Failed to calculate thermocline", e)
    return td


if __name__ == "__main__":
    input_file = os.path.join(sys.argv[1], "inputs.json")
    if not os.path.exists(input_file):
        raise ValueError("Input file not found.")
    with open(input_file, 'r', encoding='utf-8') as file:
        inputs = json.load(file)
    inputs["start_date"] = datetime.fromisoformat(inputs["start_date"])
    inputs["parameters"]["reference_date"] = datetime.fromisoformat(inputs["parameters"]["reference_date"])
    post_process(inputs["start_date"], inputs["folder"], inputs["version"], inputs["parameters"],
                 ensemble=inputs.get("ensemble"), forecast_source=inputs.get("forecast_source"),
                 forecast_start=inputs.get("forecast_start"))
