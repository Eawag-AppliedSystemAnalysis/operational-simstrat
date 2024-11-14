import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

from .general import call_url, datetime_to_simstrat_time


def initial_conditions_from_observations(key, start_date, salinity=0.15):
    print("WARNING NOT YET IMPLEMENTED")
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
            df_ave = df_ave[df_ave['time'].apply(lambda x: any(abs((x - ref).days) > days_from_observation for ref in df['time']))]
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
