import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

from .general import call_url, datetime_to_simstrat_time


def _closest_profile(csv_path, start_date, log, max_days=32, direction="closest"):
    df = pd.read_csv(csv_path)
    df.columns = [c.strip().lower() for c in df.columns]
    if not {"time", "depth", "value"}.issubset(df.columns):
        return None
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.dropna(subset=["time", "depth", "value"])
    if df.empty:
        return None
    start = pd.Timestamp(start_date)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    times = df["time"].drop_duplicates().reset_index(drop=True)
    name = os.path.basename(csv_path)
    if direction == "forward":
        forward_times = times[times >= start].reset_index(drop=True)
        if forward_times.empty:
            log.warning("No profile in {} on or after start_date.".format(name))
            return None
        diffs = forward_times - start
        idx = diffs.idxmin()
        diff_days = diffs.iloc[idx].total_seconds() / 86400.0
        if diff_days > max_days:
            log.warning("No profile in {} within {} days after start_date (earliest is {:.1f} days away).".format(name, max_days, diff_days))
            return None
        chosen = forward_times.iloc[idx]
        log.info("Using {} profile {:.1f} days after start_date.".format(name, diff_days), indent=2)
    else:
        diffs = (times - start).abs()
        idx = diffs.idxmin()
        diff_days = diffs.iloc[idx].total_seconds() / 86400.0
        if diff_days > max_days:
            log.warning("No profile in {} within {} days of start_date (closest is {:.1f} days away).".format(name, max_days, diff_days))
            return None
        chosen = times.iloc[idx]
        log.info("Using {} profile {:.1f} days from start_date.".format(name, diff_days), indent=2)
    profile = df[df["time"] == chosen].sort_values("depth").reset_index(drop=True)
    return {"profile": profile, "time": chosen}


def initial_conditions_from_observations(observations_dir, key, start_date, max_depth, log, salinity=0.15):
    try:
        lake_dir = os.path.join(observations_dir, key)
        temp_path = os.path.join(lake_dir, "temperature.csv")
        if not os.path.isfile(temp_path):
            return False
        t_result = _closest_profile(temp_path, start_date, log, max_days=183, direction="forward")
        if t_result is None:
            return False
        t_profile = t_result["profile"]
        chosen_time = t_result["time"]
        if t_profile.empty:
            return False
        t_profile["depth"] = t_profile["depth"].abs()
        t_profile = t_profile.sort_values("depth").reset_index(drop=True)
        t_profile = t_profile[t_profile["depth"] <= max_depth].reset_index(drop=True)
        if t_profile.empty:
            return False
        if t_profile["depth"].iloc[0] > 0:
            log.warning("Shallowest temperature observation depth {:.3f} m > 0; snapping to 0.".format(t_profile["depth"].iloc[0]))
            t_profile.loc[0, "depth"] = 0.0
        depth_arr = t_profile["depth"].to_numpy()
        temperature_arr = t_profile["value"].to_numpy()

        salinity_arr = np.full(len(depth_arr), salinity)
        sal_path = os.path.join(lake_dir, "salinity.csv")
        if os.path.isfile(sal_path):
            s_result = _closest_profile(sal_path, chosen_time, log)
            if s_result is not None:
                s_profile = s_result["profile"]
                if not s_profile.empty:
                    s_profile["depth"] = s_profile["depth"].abs()
                    s_profile = s_profile.sort_values("depth")
                    salinity_arr = np.interp(depth_arr, s_profile["depth"].to_numpy(), s_profile["value"].to_numpy())

        return {"depth": depth_arr, "temperature": temperature_arr, "salinity": salinity_arr,
                "start_date": chosen_time.to_pydatetime()}
    except Exception:
        return False


def climatology_initial_conditions_from_observations(observations_dir, key, start_date, max_depth, log,
                                                     salinity=0.15, min_years=5, day_window=14):
    try:
        lake_dir = os.path.join(observations_dir, key)
        temp_path = os.path.join(lake_dir, "temperature.csv")
        if not os.path.isfile(temp_path):
            return False
        df = pd.read_csv(temp_path)
        df.columns = [c.strip().lower() for c in df.columns]
        if not {"time", "depth", "value"}.issubset(df.columns):
            return False
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.dropna(subset=["time", "depth", "value"])
        if df.empty:
            return False

        target_doy = start_date.timetuple().tm_yday
        doy = df["time"].dt.dayofyear.to_numpy()
        diff = np.abs(doy - target_doy)
        circ_diff = np.minimum(diff, 365 - diff)
        df = df.loc[circ_diff <= day_window].copy()
        if df.empty:
            log.warning("No temperature observations within ±{} days of day-of-year {}.".format(day_window, target_doy))
            return False

        df["depth"] = df["depth"].abs()

        standard_depths = np.array([0, 2, 5, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200, 300], dtype=float)
        depth_arr = np.append(standard_depths[standard_depths < max_depth], max_depth)

        years = set()
        interpolated = []
        for chosen_time, group in df.groupby("time"):
            prof = group.sort_values("depth").reset_index(drop=True)
            prof = prof[prof["depth"] <= max_depth].reset_index(drop=True)
            if len(prof) < 2:
                continue
            if prof["depth"].iloc[0] > 0:
                prof.loc[0, "depth"] = 0.0
            t_interp = np.interp(depth_arr, prof["depth"].to_numpy(), prof["value"].to_numpy())
            interpolated.append(t_interp)
            years.add(chosen_time.year)

        if len(years) < min_years:
            log.warning("Only {} distinct years of observations within ±{} days of day-of-year {} "
                        "(need {}); skipping climatology.".format(len(years), day_window, target_doy, min_years))
            return False

        temperature_arr = np.mean(np.vstack(interpolated), axis=0)
        salinity_arr = np.full(len(depth_arr), salinity)
        log.info("Built climatological initial profile from {} profiles across {} years.".format(
            len(interpolated), len(years)), indent=2)
        return {"depth": depth_arr, "temperature": temperature_arr, "salinity": salinity_arr}
    except Exception:
        return False


def default_initial_conditions(doy, elevation, max_depth, salinity=0.15):
    depths = np.array([0, 10, 20, 30, 40, 50, 100, 150, 200, 300])
    depth_arr = np.append(depths[depths < max_depth], max_depth)
    salinity_arr = [salinity] * len(depth_arr)
    temperature_profile_500m = np.array(
        [[5.5, 5.5, 5.0, 5.0, 5.0, 4.5, 4.5, 4.5, 4.5, 4.5],  # ~Jan 1st
         [8., 6.0, 5.0, 5.0, 5.0, 4.5, 4.5, 4.5, 4.5, 4.5],  # ~Apr 1st
         [20., 18., 14., 8.0, 6.0, 4.5, 4.5, 4.5, 4.5, 4.5],  # ~Jul 1st
         [9.5, 9.5, 9.0, 8.0, 7.0, 5.0, 4.5, 4.5, 4.5, 4.5],  # ~Oct 1st
         [5.5, 5.5, 5.0, 5.0, 5.0, 4.5, 4.5, 4.5, 4.5, 4.5]])  # ~Dec 31st
    temperature_profile_1500m = np.array(
        [[0.0, 2.5, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0],  # ~Jan 1st
         [0.0, 2.5, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0],  # ~Apr 1st
         [14., 9.0, 6.0, 4.5, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0],  # ~Jul 1st
         [8.0, 8.0, 7.0, 6.0, 5.0, 4.0, 4.0, 4.0, 4.0, 4.0],  # ~Oct 1st
         [0.0, 2.5, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0]])  # ~Dec 31st

    t_500 = np.concatenate(
        [np.interp([doy], [0, 91, 182, 273, 365], temperature_profile_500m[:, i]) for i in range(len(depths))])
    t_1500 = np.concatenate(
        [np.interp([doy], [0, 91, 182, 273, 365], temperature_profile_1500m[:, i]) for i in range(len(depths))])
    temperature_arr = np.concatenate(
        [np.interp([elevation], [500, 1500], [t_500[k], t_1500[k]]) for k in range(len(depths))])
    temperature_arr = np.interp(depth_arr, depths, temperature_arr)
    return {"depth": depth_arr, "temperature": temperature_arr, "salinity": salinity_arr}


def absorption_from_observations(key, start_date, end_date, api, reference_date, days_from_observation=60):
    try:
        data = call_url("{}/insitu/secchi/{}".format(api, key))
        df = pd.DataFrame({"time": data["time"], "value": data["variable"]["data"]})
        df.loc[df['value'] < 0.05, 'value'] = 0.05 # Prevent zero values from becoming infinite
        df["value"] = 1.7 / df["value"]  # Convert from Secchi depth [m] to absorption [m-1]
        df["time"] = pd.to_datetime(df["time"])
        secchi_mean = df['value'].mean()

        # Create monthly secchi depth array
        df["month"] = df["time"].dt.month
        month_dict = df.groupby(['month'])['value'].mean().to_dict()
        monthly_values = [month_dict[m] if m in month_dict else secchi_mean for m in range(1, 13)]

        df = df[(df['time'] >= start_date) & (df['time'] <= end_date)]
        time = np.array([datetime(year=start_date.year, month=1, day=15).replace(tzinfo=timezone.utc) + relativedelta(months=n) for n in range((end_date.year + 1 - start_date.year) * 12)])
        time = time[(time > start_date) & (time < end_date)]
        value = [monthly_values[t.month - 1] for t in time]
        df_ave = pd.DataFrame({"time": time, "value": value})

        # Replace monthly values with real data where available
        if not df.empty:
            df_ave = df_ave[df_ave['time'].apply(lambda x: all(abs((x - ref).days) > days_from_observation for ref in df['time']))]
        df_m = pd.concat([df, df_ave], ignore_index=True)
        df_m = df_m.sort_values(by='time')

        start = datetime_to_simstrat_time(start_date, reference_date)
        end = datetime_to_simstrat_time(end_date, reference_date)

        if not df_m.empty:
            t = [start] + [datetime_to_simstrat_time(d, reference_date) for d in df_m["time"].tolist()] + [end]
            v = [df_m['value'].iloc[0]] + df_m["value"].tolist() + [df_m['value'].iloc[-1]]
        else:
            t = [start, end]
            v = [monthly_values[start_date.month - 1], monthly_values[end_date.month - 1]]

        return {"Time": np.array(t), "Value": np.array(v)}
    except Exception as e:
        return False



def default_absorption(trophic_state, elevation, start_date, end_date, absorption, reference_date):
    if not absorption:
        if trophic_state.lower() == 'oligotrophic':
            absorption = 0.15
        elif trophic_state.lower() == 'eutrophic':
            absorption = 0.50
        else:
            absorption = 0.25
        if elevation > 2000:
            absorption = 1.00
    start = datetime_to_simstrat_time(start_date, reference_date)
    end = datetime_to_simstrat_time(end_date, reference_date)
    return {"Time": [start, end], "Value": [absorption, absorption]}
