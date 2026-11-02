import os
import sys
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


if __name__ == "__main__":
    input_file = os.path.join(sys.argv[1], "inputs.json")
    if not os.path.exists(input_file):
        raise ValueError("Input file not found.")
    with open(input_file, 'r', encoding='utf-8') as file:
        inputs = json.load(file)
    inputs["start_date"] = datetime.fromisoformat(inputs["start_date"])
    inputs["parameters"]["reference_date"] = datetime.fromisoformat(inputs["parameters"]["reference_date"])
    post_process(inputs["start_date"], inputs["folder"], inputs["version"], inputs["parameters"])
