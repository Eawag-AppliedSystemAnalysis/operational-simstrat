
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
        output_args[key] = value
    return output_args
