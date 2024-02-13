import numpy as np


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


def absorption_from_observations(key, start_date, end_date):
    print("WARNING NOT YET IMPLEMENTED")
    return False


def default_absorption(trophic_state, elevation, start_date, end_date, absorption):
    if not absorption:
        if trophic_state.lower() == 'oligotrophic':
            absorption = 0.15
        elif trophic_state.lower() == 'eutrophic':
            absorption = 0.50
        else:
            absorption = 0.25
        if elevation > 2000:
            absorption = 1.00
    return {"time": [start_date, end_date], "depth": [1, 1], "absorption": [absorption, absorption]}
