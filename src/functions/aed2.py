import os
import numpy as np
from .general import oxygen_saturation


def create_aed_configuration_file(simulation_dir, sediment_oxygen_uptake_rate):
    static_file = os.path.join(simulation_dir, "../..", "static", "aed2.nml")
    output_file = os.path.join(simulation_dir, "aed2.nml")
    with open(static_file, 'r') as file:
        lines = file.readlines()

    with open(output_file, 'w') as file:
        for line in lines:
            if "Fsed_oxy = " in line:
                line = "   Fsed_oxy = {}	! From Müller et al. (2019)\n".format(sediment_oxygen_uptake_rate)
            file.write(line)


def compute_oxygen_inflows(inflow_data, elevation):
    for inflow in inflow_data["deep_inflows"]:
        inflow["oxygen"] = oxygen_saturation(inflow["T"], elevation)
    if len(inflow_data["surface_inflows"]) > 0:
        for inflow in inflow_data["surface_inflows"]:
            if np.all(inflow["T"] == 0):
                inflow["oxygen"] = inflow["T"]
            else:
                inflow["oxygen"] = oxygen_saturation(inflow["T"] / inflow["Q"], elevation) * inflow["Q"]
    return inflow_data


def compute_initial_oxygen(surface_temperature, max_depth, elevation):
    oxygen = oxygen_saturation(surface_temperature, elevation)
    return [0, max_depth], [oxygen, oxygen]
