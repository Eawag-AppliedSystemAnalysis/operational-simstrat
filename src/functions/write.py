import os
import json
import numpy as np
from datetime import datetime
from .general import datetime_to_simstrat_time


def write_grid(grid_cells, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('Number of grid points\n')
        f.write('%d\n' % grid_cells)


def write_bathymetry(bathymetry, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('%s    %s\n' % ('Depth [m]', 'Area [m2]'))
        for i in range(len(bathymetry["depth"])):
            f.write('%6.1f    %9.0f\n' % (-abs(bathymetry["depth"][i]), bathymetry["area"][i]))


def write_output_depths(output_depths, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('Depths [m]\n')
        for z in -np.abs(output_depths):
            f.write('%.2f\n' % z)


def write_output_time_resolution(output_time_steps, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('Number of time steps\n')
        f.write('%d\n' % np.floor(output_time_steps))


def write_initial_conditions(depth_arr, temperature_arr, salinity_arr, file_path):
    if len(depth_arr) != len(temperature_arr) or len(temperature_arr) != len(salinity_arr):
        raise ValueError("All input arrays must be the same length")
    if depth_arr[0] != 0:
        raise ValueError("First depth must be zero")
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('%s    %s    %s    %s    %s    %s    %s\n' % ('Depth [m]', 'U [m/s]', 'V [m/s]', 'T [°C]', 'S [‰]', 'k [J/kg]', 'eps [W/kg]'))
        for i in range(len(depth_arr)):
            if not np.isnan(temperature_arr[i]):
                if np.isnan(salinity_arr[i]):
                    salinity_arr[i] = np.nanmean(salinity_arr)
                f.write('%7.2f    %7.3f    %7.3f    %7.3f    %7.3f    %6.1e    %6.1e\n' % (-abs(depth_arr[i]), 0, 0, temperature_arr[i], salinity_arr[i], 3E-6, 5E-10))


def write_absorption(time_arr, depth_arr, absorption_arr, reference_time, file_path):
    if len(depth_arr) != len(time_arr) or len(time_arr) != len(absorption_arr):
        raise ValueError("All input arrays must be the same length")
    with open(file_path,'w',encoding='utf-8') as f:
        f.write('Time [d] (1.col)    z [m] (1.row)    Absorption [m-1] (rest)\n')
        depths = set([abs(z) for z in depth_arr])
        f.write('%d\n' % len(depths))
        f.write('-1         ' + ' '.join(['%5.2f' % -z for z in depths]) + '\n')
        for t in time_arr:
            f.write('%10.4f' % datetime_to_simstrat_time(t, reference_time))
            for z in depths:
                ind = np.logical_and(np.array(time_arr)==t,np.abs(depth_arr)==z)
                if sum(ind)>1:
                    raise Exception('Error: time %s seems to be repeated in the Secchi data; check the source file.' % datetime.strftime(t,"%d.%m.%Y %H:%M"))
                f.write(' %5.3f' % np.array(absorption_arr)[ind])
            f.write('\n')


def write_par_file_303(par, simulation_dir):
    with open(os.path.join(simulation_dir, "settings.par"), 'w') as f:
        json.dump(par, f, indent=4)


def write_inflow(parameter, inflow_mode, simulation_dir, time=None, deep_inflows=None, surface_inflows=None):
    if surface_inflows is None:
        surface_inflows = []
    if deep_inflows is None:
        deep_inflows = []
    if time is None:
        time = []
    if parameter == "Q":
        deep_unit = "m3/s"
        surface_unit = "m2/s"
    elif parameter == "T":
        deep_unit = "°C"
        surface_unit = "°C m2/s"
    elif parameter == "S":
        deep_unit = "ppt"
        surface_unit = "ppt m2/s"
    else:
        raise ValueError("Parameter {} not recognised".format(parameter))
    file_path = os.path.join(simulation_dir, "{}in.dat".format(parameter))
    if inflow_mode == 0:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write("No inflows")
    elif inflow_mode == 2:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write('%10s %10s %10s %10s\n' % ('Time [d]', 'Depth [m]', 'Deep Inflows [{}]'.format(deep_unit),
                                               'Surface Inflows [{}]'.format(surface_unit)))
            f.write('%10d %10d\n' % (len(deep_inflows), len(surface_inflows)))
            f.write('-1         ' + ' '.join(['%10.2f' % z["depth"] for z in deep_inflows]) + ' '.join(['%10.2f' % z["depth"] for z in surface_inflows]) + '\n')
            for i in range(len(time)):
                f.write('%10.4f ' % time[i])
                f.write(' '.join(['%10.2f' % z["data"][i] for z in deep_inflows]))
                f.write(' '.join(['%10.2f' % z["data"][i] for z in surface_inflows]))
                f.write('\n')


def write_outflow(simulation_dir):
    with open(os.path.join(simulation_dir, "Qout.dat"), 'w', encoding='utf-8') as f:
        f.write("Outflow not used, lake overflows to maintain water level")
