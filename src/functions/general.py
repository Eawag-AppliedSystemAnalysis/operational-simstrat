import os
import json
import shutil
import zipfile
import requests
import subprocess
import numpy as np
import pandas as pd
from scipy import interpolate
from datetime import datetime, timezone, timedelta


def process_args(input_args):
    output_args = {}
    for arg in input_args.args:
        if "=" not in arg:
            raise ValueError('Invalid additional argument, arguments must be in the form key=value. Values '
                             'that contain spaces must be enclosed in quotes.'.format(arg))
        key, value = arg.split("=")
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        elif key == "lakes":
            value = value.split(",")
        output_args[key] = value
    return output_args


def serializer(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()


def process_input(input_text):
    # Convert input to a list if it's not already a list and check for empty string
    output_list = [] if (not input_text) or (input_text == [""]) else [input_text] if isinstance(input_text,
                                                                                                 str) else input_text
    return output_list


def datetime_to_simstrat_time(time, reference_date):
    return (time - reference_date).days + (time - reference_date).seconds / 24 / 3600


def simstrat_time_to_datetime(time, reference_date):
    return reference_date + timedelta(days=time)


def oxygen_saturation(temperature, altitude):
    # Benson & Krause, 1984; ignoring salinity effects
    t_k = temperature + 273.15  # Convert to kelvin
    capac = -139.34411 + 1.575701e5 / t_k - 6.642308e7 / t_k ** 2 + 1.243800e10 / t_k ** 3 - 8.621949e11 / t_k ** 4
    o2 = np.exp(capac) * pressure_correction(altitude)  # mgL
    return o2 / 32 * 1000  # mmolm3


def pressure_correction(altitude):
    standard_pressure_sea_level = 101325  # Pa
    standard_temperature_sea_level = 288.16  # K
    gravitational_acceleration = 9.80665  # m2/s
    air_molar_mass = 0.02896968  # kg/mol
    universal_gas_constant = 8.314462681  # J/mol/K
    press_corr = np.exp((-gravitational_acceleration * air_molar_mass * altitude) / (universal_gas_constant * standard_temperature_sea_level))
    return press_corr


def air_pressure_from_elevation(elevation):
    return round(1013.25 * np.exp((-9.81 * 0.029 * elevation) / (8.314 * 283.15)), 0)


def vapor_pressure_from_relative_humidity_and_temperature(temperature, relative_humidity):
    """
    Calculate vapor pressure using the Magnus formula.

    Parameters:
    - temperature (float or numpy array): Temperature in degrees Celsius
    - relative_humidity (float or numpy array): Relative humidity as a percentage (e.g., 60 for 60%)

    Returns:
    - vapor_pressure (float or numpy array): Vapor pressure in hPa (hectopascals)
    """
    a = 17.27
    b = 237.7
    rh_fraction = relative_humidity / 100.0
    saturation_vapor_pressure = 6.112 * np.exp((a * temperature) / (temperature + b))
    vapor_pressure = rh_fraction * saturation_vapor_pressure
    return vapor_pressure


def seiche_from_surface_area(surface_area):
    # Surface area in km2
    return min(max(round(0.0017 * np.sqrt(surface_area), 3), 0.0005), 0.05)


def adjust_temperature_for_altitude_difference(temperature, difference):
    t = np.array(temperature, dtype=float)
    return t - 0.0065 * difference


def call_url(url):
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data
    else:
        raise ValueError(f"Unable to access url {url}. Status code: {response.status_code}. Message: {response.text}")


def upload_files(local_folder, remote_folder, host, username, password, port=22):
    mkdir = 'sshpass -p {} ssh -p {} {}@{} "mkdir -p {}"'.format(password, port, username, host, remote_folder)
    subprocess.run(mkdir, check=True, shell=True)
    failed = []
    cmd = "sshpass -p {} scp -P {} {} {}@{}:{}"
    for file in os.listdir(local_folder):
        if ".nc" in file:
            remote_file = os.path.join(remote_folder, file)
            try:
                subprocess.run(
                    cmd.format(password, port, os.path.join(local_folder, file), username, host, remote_file),
                    check=True, shell=True)
            except Exception as e:
                print(e)
                failed.append(file)

    if len(failed) > 0:
        raise ValueError("Failed to upload: {}".format(", ".join(failed)))


def get_elevation_swisstopo(latitude, longitude):
    endpoint = "https://api3.geo.admin.ch/rest/services/height?easting={}&northing={}"
    easting, northing = latlng_to_ch1903(latitude, longitude)
    data = call_url(endpoint.format(easting, northing))
    return float(data["height"])


def latlng_to_ch1903(lat, lng):
    lat = lat * 3600
    lng = lng * 3600
    lat_aux = (lat - 169028.66) / 10000
    lng_aux = (lng - 26782.5) / 10000
    x = 2600072.37 + 211455.93 * lng_aux - 10938.51 * lng_aux * lat_aux - 0.36 * lng_aux * lat_aux ** 2 - 44.54 * lng_aux ** 3 - 2000000
    y = 1200147.07 + 308807.95 * lat_aux + 3745.25 * lng_aux ** 2 + 76.63 * lat_aux ** 2 - 194.56 * lng_aux ** 2 * lat_aux + 119.79 * lat_aux ** 3 - 1000000
    return x, y


def ch1903_to_latlng(x, y):
    x_aux = (x - 600000) / 1000000
    y_aux = (y - 200000) / 1000000
    lat = 16.9023892 + 3.238272 * y_aux - 0.270978 * x_aux ** 2 - 0.002528 * y_aux ** 2 - 0.0447 * x_aux ** 2 * y_aux - 0.014 * y_aux ** 3
    lng = 2.6779094 + 4.728982 * x_aux + 0.791484 * x_aux * y_aux + 0.1306 * x_aux * y_aux ** 2 - 0.0436 * x_aux ** 3
    lat = (lat * 100) / 36
    lng = (lng * 100) / 36
    return lat, lng


def get_day_of_year(datetime_array):
    datetime_array = np.asarray(datetime_array)
    day_of_year = np.array([dt.timetuple().tm_yday for dt in datetime_array], dtype=int)
    return day_of_year


def clear_sky_solar_radiation(time, air_pressure, vapour_pressure, lat, lon):
    vapour_pressure[vapour_pressure < 1] = np.nan
    hour_of_day = np.array([t.hour + t.minute / 60 + t.second / 3600 for t in time])
    doy = get_day_of_year(time) + hour_of_day / 24
    doy_winter = doy + 10
    doy_winter[doy_winter >= 365.24] = doy_winter[doy_winter >= 365.24] - 365.24
    phi = np.arcsin(-0.39779 * np.cos(2 * np.pi / 365.24 * doy_winter))  # Declination of the sun (Wikipedia)
    gamma = 2 * np.pi * (doy + 0.5) / 365  # Fractional year [rad]
    eq_time = 229.18 / 60 * (0.000075 + 0.001868 * np.cos(gamma) - 0.032077 * np.sin(gamma) - 0.014615 * np.cos(
        2 * gamma) - 0.040849 * np.sin(
        2 * gamma))  # Equation of time [hr] (https://www.esrl.noaa.gov/gmd/grad/solcalc/solareqns.PDF)
    solar_noon = 12 - 4 / 60 * lon - eq_time  # Solar noon [hr] (https://www.esrl.noaa.gov/gmd/grad/solcalc/solareqns.PDF)
    cos_zenith = np.sin(lat * np.pi / 180) * np.sin(phi) + np.cos(lat * np.pi / 180) * np.cos(phi) * np.cos(
        np.pi / 12 * (hour_of_day - solar_noon))  # Cosine of the solar zenith angle (Wikipedia)
    cos_zenith[cos_zenith < 0] = 0
    m = 35 * cos_zenith * (1244 * cos_zenith ** 2 + 1) ** -0.5  # Air mass thickness coefficient
    fG = interpolate.interp2d([-10, 81, 173, 264, 355], [5, 15, 25, 35, 45, 55, 65, 75, 85],
                              [[3.37, 2.85, 2.8, 2.64, 3.37], [2.99, 3.02, 2.7, 2.93, 2.99], [3.6, 3, 2.98, 2.93, 3.6],
                               [3.04, 3.11, 2.92, 2.94, 3.04], [2.7, 2.95, 2.77, 2.71, 2.7],
                               [2.52, 3.07, 2.67, 2.93, 2.52], [1.76, 2.69, 2.61, 2.61, 1.76],
                               [1.6, 1.67, 2.24, 2.63, 1.6], [1.11, 1.44, 1.94, 2.02, 1.11]])
    G = np.array([fG(lat, d)[0] for d in doy])  # Empirical constant
    Td = (243.5 * np.log(vapour_pressure / 6.112)) / (
                17.67 - np.log(vapour_pressure / 6.112))  # Dew point temperature [°C]
    pw = np.exp(0.1133 - np.log(G + 1) + 0.0393 * (1.8 * Td + 32))  # Precipitable water
    Tw = 1 - 0.077 * (pw * m) ** 0.3  # Attenuation coefficient for water vapour
    Ta = 0.935 ** m  # Attenuation coefficient for aerosols
    TrTpg = 1.021 - 0.084 * (m * (
            0.000949 * air_pressure + 0.051)) ** 0.5  # Attenuation coefficient for Rayleigh scattering and permanent gases
    effective_solar_constant = 1353 * (1 + 0.034 * np.cos(2 * np.pi / 365.24 * doy))
    return effective_solar_constant * cos_zenith * TrTpg * Tw * Ta


def adjust_data_to_mean_and_std(arr, std, mean):
    arr = np.array(arr)
    data_mean = np.nanmean(arr)
    data_std = np.nanstd(arr)
    if np.isnan(data_mean) or np.isnan(data_std) or data_std == 0:
        print("Forcing data not adjusted")
        return arr

    return (arr - data_mean) / data_std * std + mean


def detect_gaps(arr, start, end, max_allowable_gap=86400):
    arr = np.array(arr)
    datetime_objects = np.concatenate([[start], arr, [end]])
    timestamps = np.array([dt.timestamp() for dt in datetime_objects])
    sorted_timestamps = np.sort(timestamps)
    gaps = np.diff(sorted_timestamps)
    large_gap_indices = np.where(gaps > max_allowable_gap)[0]
    result = []
    for index in large_gap_indices:
        start_date = datetime.utcfromtimestamp(sorted_timestamps[index]).replace(tzinfo=timezone.utc)
        end_date = datetime.utcfromtimestamp(sorted_timestamps[index + 1]).replace(tzinfo=timezone.utc)
        result.append((start_date, end_date))
    return result


def interpolate_timeseries(time, data, max_gap_size=None):
    if max_gap_size is None:
        max_gap_size = time[-1] - time[0]
    non_nan_indices = np.arange(len(data))[~np.isnan(data)]
    for i in range(1, len(non_nan_indices)):
        start_index = non_nan_indices[i - 1]
        end_index = non_nan_indices[i]
        gap_size = time[end_index] - time[start_index]
        if gap_size <= max_gap_size:
            t = time[start_index:end_index + 1]
            d = data[start_index:end_index + 1]
            nan_indices = np.isnan(d)
            d[nan_indices] = np.interp(t[nan_indices], t[~nan_indices], d[~nan_indices])
            data[start_index:end_index + 1] = d
    return data


def fill_day_of_year(time, data, time_full, data_full, reference_date):
    df_full = pd.DataFrame({"simstrat_time": time_full, "data": data_full})
    df_full['time'] = reference_date + pd.to_timedelta(df_full['simstrat_time'], unit='D')
    doy_avg = df_full.groupby(df_full['time'].dt.dayofyear)['data'].mean()
    df = pd.DataFrame({"simstrat_time": time, "data": data})
    df['time'] = reference_date + pd.to_timedelta(df['simstrat_time'], unit='D')
    data = df.apply(lambda row: doy_avg[row['time'].dayofyear] if pd.isna(row['data']) else row['data'], axis=1).values
    return data


def calculate_mean_wind_direction(wind_direction):
    mean_wind_direction = np.arctan2(np.nanmean(np.sin(np.radians(wind_direction))),
                                     np.nanmean(np.cos(np.radians(wind_direction))))
    if mean_wind_direction < 0:
        mean_wind_direction += 360
    return mean_wind_direction


def interpolate_arrays(x1, x2, y1, y2, x):
    return ((x - x1)/(x2 - x1)) * (y2 - y1) + y1


def run_subprocess(command):
    result = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        error_message = f"Command failed with return code {result.returncode}\n"
        error_message += f"Command: {command}\n"
        error_message += f"Standard Output: {result.stdout}\n"
        error_message += f"Standard Error: {result.stderr}\n"
        raise RuntimeError(error_message)


def edit_parameters(file, key, results):
    if "parameters" not in results:
        raise ValueError("Calibration failed, run in debug mode to see full output.")
    with open(file, 'r') as f:
        lake_parameters = json.load(f)
    for lake in lake_parameters:
        if lake["key"] == key:
            for p in results["parameters"].keys():
                lake[p] = results["parameters"][p]
            lake["performance"] = {}
            lake["performance"]["rmse"] = results["error"]
            for obv in results["observations"].keys():
                lake["performance"][obv] = results["observations"][obv]
            with open(file, 'w') as file:
                json.dump(lake_parameters, file, indent=4)
            return lake

def download_observations(url, folder):
    for item in os.listdir(folder):
        item_path = os.path.join(folder, item)
        if os.path.isdir(item_path):
            shutil.rmtree(item_path)
    file = os.path.join(folder, "observations.zip")
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(file, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    with zipfile.ZipFile(file, 'r') as zip_ref:
        zip_ref.extractall(folder)
    os.remove(file)
