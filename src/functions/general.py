import numpy as np

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


def datetime_to_simstrat_time(time, reference_time):
    return (time - reference_time).days + (time - reference_time).seconds/24/3600


def air_pressure_from_elevation(elevation):
    return round(1013.25 * np.exp((-9.81 * 0.029 * elevation) / (8.314 * 283.15)), 0)


def seiche_from_surface_area(surface_area):
    # Surface area in km2
    return min(max(round(0.0017 * np.sqrt(surface_area), 3), 0.0005), 0.05)
