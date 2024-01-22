import numpy as np


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
