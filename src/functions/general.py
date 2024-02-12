import requests
import subprocess
import numpy as np
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


def process_input(input_text):
    # Convert input to a list if it's not already a list and check for empty string
    output_list = [] if (not input_text) or (input_text == [""]) else [input_text] if isinstance(input_text, str) else input_text
    return output_list


def datetime_to_simstrat_time(time, reference_date):
    return (time - reference_date).days + (time - reference_date).seconds/24/3600


def simstrat_time_to_datetime(time, reference_date):
    return reference_date + timedelta(days=time)


def air_pressure_from_elevation(elevation):
    return round(1013.25 * np.exp((-9.81 * 0.029 * elevation) / (8.314 * 283.15)), 0)


def seiche_from_surface_area(surface_area):
    # Surface area in km2
    return min(max(round(0.0017 * np.sqrt(surface_area), 3), 0.0005), 0.05)


def adjust_temperature_for_altitude_difference(temperature, difference):
    t = np.array(temperature)
    return t - 0.0065 * difference


def call_url(url):
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data
    else:
        raise ValueError("Unable to access url {}".format(url))


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
    Td = (243.5 * np.log(vapour_pressure / 6.112)) / (17.67 - np.log(vapour_pressure / 6.112))  # Dew point temperature [°C]
    pw = np.exp(0.1133 - np.log(G + 1) + 0.0393 * (1.8 * Td + 32))  # Precipitable water
    Tw = 1 - 0.077 * (pw * m) ** 0.3  # Attenuation coefficient for water vapour
    Ta = 0.935 ** m  # Attenuation coefficient for aerosols
    TrTpg = 1.021 - 0.084 * (m * (
            0.000949 * air_pressure + 0.051)) ** 0.5  # Attenuation coefficient for Rayleigh scattering and permanent gases
    effective_solar_constant = 1353 * (1 + 0.034 * np.cos(2 * np.pi / 365.24 * doy))
    return effective_solar_constant * cos_zenith * TrTpg * Tw * Ta


def adjust_data_to_mean_and_std(arr, std, mean):
    arr = np.array(arr)
    return (arr - np.nanmean(arr)) / np.nanstd(arr) * std + mean


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
            t = time[start_index:end_index+1]
            d = data[start_index:end_index+1]
            nan_indices = np.isnan(d)
            d[nan_indices] = np.interp(t[nan_indices], t[~nan_indices], d[~nan_indices])
            data[start_index:end_index+1] = d
    return data


def calculate_mean_wind_direction(wind_direction):
    mean_wind_direction = np.arctan2(np.nanmean(np.sin(np.radians(wind_direction))), np.nanmean(np.cos(np.radians(wind_direction))))
    if mean_wind_direction < 0:
        mean_wind_direction += 360
    return mean_wind_direction


def run_subprocess(command):
    result = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        error_message = f"Command failed with return code {result.returncode}\n"
        error_message += f"Command: {command}\n"
        error_message += f"Standard Output: {result.stdout}\n"
        error_message += f"Standard Error: {result.stderr}\n"
        raise RuntimeError(error_message)
