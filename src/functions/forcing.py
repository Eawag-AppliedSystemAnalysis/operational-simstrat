import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from .general import (call_url, adjust_temperature_for_altitude_difference, air_pressure_from_elevation, detect_gaps,
                      adjust_data_to_mean_and_std, clear_sky_solar_radiation, datetime_to_simstrat_time)


def metadata_from_forcing(forcing, api):
    if forcing[0]["type"].lower() == "meteoswiss_meteostation":
        start, end = metadata_from_meteoswiss_meteostation(forcing, api)
    else:
        raise ValueError("Not implemented for {}".format(forcing[0]["type"]))
    return start, end


def metadata_from_meteoswiss_meteostation(forcing, api):
    required = {key: {"start": [], "end": []} for key in
                ["fkl010h0", "dkl010h0", "rre150h0", "tre200h0", "gre000h0", "pva200h0"]}
    for f in forcing:
        if f["type"].lower() == "meteoswiss_meteostation":
            endpoint = "{}/meteoswiss/meteodata/metadata/{}".format(api, f["id"])
            data = call_url(endpoint)
            for parameter in data["parameters"]:
                if parameter["id"] in required:
                    parameter["start_date"] = datetime.strptime(parameter["start_date"], '%Y-%m-%dT%H:%M:%S%z')
                    parameter["end_date"] = datetime.strptime(parameter["end_date"], '%Y-%m-%dT%H:%M:%S%z')
                    required[parameter["id"]]["start"].append(parameter["start_date"])
                    required[parameter["id"]]["end"].append(parameter["end_date"])
            f["parameters"] = data["parameters"]
            f["elevation"] = data["elevation"]
            f["latlng"] = data["latlng"]

    for key in required.keys():
        if len(required[key]["start"]) == 0:
            raise ValueError("Parameter {} is required, no data can be found from the stations".format(key))
        else:
            required[key]["start"] = min(required[key]["start"])
            required[key]["end"] = max(required[key]["end"])
    start = max([r["start"] for r in required.values()])
    end = min([r["end"] for r in required.values()])
    return start, end


def download_forcing_data(start, end, forcing, elevation, latitude, longitude, reference_date, api, log):
    output = {
        "Time": {"unit": "d", "description": "Time in days since reference date"},
        "u": {"unit": "m/s", "description": "Wind component West to East"},
        "v": {"unit": "m/s", "description": "Wind component South to North"},
        "Tair": {"unit": "°C", "description": "Air temperature adjusted to lake altitude"},
        "sol": {"unit": "W/m2", "description": "Solar irradiance"},
        "vap": {"unit": "mbar", "description": "Vapor pressure"},
        "cloud": {"unit": "-", "description": "Cloud cover from 0 to 1"},
        "rain": {"unit": "m/hr", "description": "Precipitation"},
    }
    if forcing[0]["type"].lower() == "meteoswiss_meteostation":
        output = meteodata_from_meteoswiss_meteostations(start, end, forcing, elevation, latitude, longitude,
                                                         reference_date, output, api, log)
    return output


def meteodata_from_meteoswiss_meteostations(start, end, forcing, elevation, latitude, longitude, reference_date, output,
                                            api, log):
    endpoint = api + "/meteoswiss/meteodata/measured/{}/{}/{}/{}"

    time = start + np.arange(0, (end - start).total_seconds() / 3600 + 1, 1).astype(int) * timedelta(hours=1)
    df_t = pd.DataFrame({'time': time})
    df_t['time'] = pd.to_datetime(df_t['time'])
    output["Time"]["data"] = np.array([datetime_to_simstrat_time(t, reference_date) for t in time])

    parameter_ids = ["fkl010h0", "dkl010h0", "rre150h0", "tre200h0", "gre000h0", "pva200h0"]
    raw_data = {}
    for p_id in parameter_ids:
        gaps = False
        df = False
        for f in forcing:
            if p_id in [p["id"] for p in f["parameters"]]:
                parameter = next((d for d in f["parameters"] if d.get("id") == p_id), None)
                if not gaps:
                    start_date = max(start, parameter["start_date"])
                    end_date = min(end, parameter["end_date"])
                    log.info(
                        "{}: Using data from station {} : {} - {}".format(p_id, f["id"], start_date.strftime('%Y%m%d'),
                                                                          end_date.strftime('%Y%m%d')), indent=1)
                    data = call_url(
                        endpoint.format(f["id"], p_id, start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d')))
                    values = np.array(data[p_id])
                    if p_id == "tre200h0":
                        values = adjust_temperature_for_altitude_difference(values, elevation - f["elevation"])
                    df = pd.DataFrame({'time': data["Time"], 'values': values})
                    df['time'] = pd.to_datetime(df['time'])
                    df['values'] = pd.to_numeric(df['values'], errors='coerce')
                    df = df.dropna()
                    df = df.sort_values(by='time')
                    mean = df["values"].mean()
                    std = df["values"].std()
                    gaps = detect_gaps(df["time"], start, end)
                elif len(gaps) > 0:
                    for gap in gaps:
                        if gap[0] >= parameter["start_date"] and gap[1] <= parameter["end_date"]:
                            data = call_url(
                                endpoint.format(f["id"], p_id, gap[0].strftime('%Y%m%d'), gap[1].strftime('%Y%m%d')))
                            log.info("{}: Completing with data from station {} : {} - {}".format(p_id, f["id"],
                                                                                                 gap[0].strftime(
                                                                                                     '%Y%m%d'),
                                                                                                 gap[1].strftime(
                                                                                                     '%Y%m%d')),
                                     indent=2)
                            df_new = pd.DataFrame({'time': data["Time"],
                                                   'values_new': adjust_data_to_mean_and_std(data[p_id], std, mean)})
                            df_new['time'] = pd.to_datetime(df_new['time'])
                            df_new['values_new'] = pd.to_numeric(df_new['values_new'], errors='coerce')
                            df = pd.merge(df, df_new, on='time', how='outer')
                            df['values'] = df['values'].combine_first(df['values_new'])
                            df = df[["time", "values"]]
                            df = df.dropna()
                            df = df.sort_values(by='time')
                            df.reset_index(inplace=True)
                    gaps = detect_gaps(df["time"], start, end)
        if not isinstance(df, pd.core.frame.DataFrame):
            raise ValueError("Failed to collect values for {}".format(p_id))
        df_m = pd.merge(df_t, df, on='time', how='left')
        raw_data[p_id] = np.array(df_m["values"])

    log.info("Processing wind from magnitude and direction to components", indent=1)
    wind_direction = raw_data["dkl010h0"]
    wind_magnitude = raw_data["fkl010h0"]
    log.info("Set missing direction values to average wind direction", indent=2)
    wind_direction_mean = np.arctan2(np.mean(np.sin(wind_direction)), np.mean(np.cos(wind_direction)))
    wind_direction[np.isnan(wind_direction)] = wind_direction_mean
    log.info("Enforce wind magnitude to between 0 and 20 m/s", indent=1)
    wind_magnitude[(wind_magnitude < 0.0) | (wind_magnitude > 20.0)] = np.nan
    output["u"]["data"] = -wind_magnitude * np.sin(wind_direction * np.pi / 180)
    output["v"]["data"] = -wind_magnitude * np.cos(wind_direction * np.pi / 180)

    v = raw_data["tre200h0"]
    log.info("Enforce air temperature to between -42 and 42 °C", indent=1)
    v[(v < -42.0) | (v > 42.0)] = np.nan
    output["Tair"]["data"] = v

    v = raw_data["gre000h0"]
    log.info("Enforce solar radiation to between 0 and 1000 W/m2", indent=1)
    v[v < 0.0] = 0.0
    v[v > 1000.0] = np.nan
    output["sol"]["data"] = v

    v = raw_data["pva200h0"]
    log.info("Enforce vapour pressure to between 1 and 70 mbar (1hPa == 1mbar)", indent=1)
    v[(v < 1.0) | (v > 70.0)] = np.nan
    output["vap"]["data"] = v

    v = raw_data["rre150h0"]
    log.info("Enforce rainfall to greater than 0 and convert from mm to m", indent=1)
    v[v < 0] = 0
    v = v * 0.001
    output["rain"]["data"] = v

    log.info("Estimate cloudiness based on ratio between measured and theoretical solar radiation", indent=1)
    air_pressure = air_pressure_from_elevation(elevation)
    cssr = clear_sky_solar_radiation(time, air_pressure, output["vap"]["data"], latitude, longitude)
    df = pd.DataFrame({"cssr": cssr, "swr": output["sol"]["data"]})
    cssr_rolling = df['cssr'].rolling(window=24, center=True).mean()
    swr_rolling = df['swr'].rolling(window=24, center=True).mean()
    solar_index = np.interp(swr_rolling / cssr_rolling, [0, 1], [0, 1])  # Flerchinger et al. (2009), Crawford and Duchon (1999)
    output["cloud"]["data"] = 1 - solar_index

    return output
