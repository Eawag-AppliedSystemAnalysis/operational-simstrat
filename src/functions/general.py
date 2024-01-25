
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
