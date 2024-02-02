from .general import datetime_to_simstrat_time


def create_default_input_files(start_date, end_date, reference_date):
    inflow_mode = 1
    time = [datetime_to_simstrat_time(start_date, reference_date),
            datetime_to_simstrat_time(end_date, reference_date)]
    deep_inflows = [
        {"depth": 0.0, "data": [0.0, 0.0]}
    ]
    surface_inflows = []


    # If mode is 1 and surface inflows > 0 flow spread over depth
    # If mode is 1 and surface inflows == 0 zero point no inputs
    # If mode is 2 and deep inflows are rivers and surface_inflows are lakes
    print("Hello world")

