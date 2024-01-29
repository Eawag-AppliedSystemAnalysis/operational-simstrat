import pandas as pd


def grid_from_file(file_path):
    df = pd.read_csv(file_path)
    return int(df.values[0, 0])
