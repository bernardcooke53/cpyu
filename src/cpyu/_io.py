from collections.abc import Callable, Sequence
from typing import Any, TypeVar
from cpyu._trace import g_trace


def _obj_to_b64(thing: object) -> str:
    import pickle
    import base64

    pkl = pickle.dumps(thing)
    b64 = base64.b64encode(pkl)
    return b64.decode("utf-8")


def _b64_to_obj(thing):
    import pickle
    import base64

    try:
        a = base64.b64decode(thing)
        b = pickle.loads(a)
        return b
    except Exception:
        return {}


def _utf_to_str(input):
    if isinstance(input, list):
        return [_utf_to_str(element) for element in input]
    elif isinstance(input, dict):
        return {_utf_to_str(key): _utf_to_str(value) for key, value in input.items()}
    else:
        return input


def _copy_new_fields(info: dict[str, Any], new_info: dict[str, Any]) -> None:
    keys = [
        "vendor_id_raw",
        "hardware_raw",
        "brand_raw",
        "hz_advertised_friendly",
        "hz_actual_friendly",
        "hz_advertised",
        "hz_actual",
        "arch",
        "bits",
        "count",
        "arch_string_raw",
        "uname_string_raw",
        "l2_cache_size",
        "l2_cache_line_size",
        "l2_cache_associativity",
        "stepping",
        "model",
        "family",
        "processor_type",
        "flags",
        "l3_cache_size",
        "l1_data_cache_size",
        "l1_instruction_cache_size",
    ]

    g_trace.keys(keys, info, new_info)

    # Update the keys with new values
    for key in keys:
        if new_info.get(key, None) and not info.get(key, None):
            info[key] = new_info[key]
        elif key == "flags" and new_info.get("flags"):
            for f in new_info["flags"]:
                if f not in info["flags"]:
                    info["flags"].append(f)
            info["flags"].sort()


def _get_field_actual(
    cant_be_number: bool, raw_string: str, field_names: Sequence[str]
) -> str | None:
    for line in raw_string.splitlines():
        for field_name in field_names:
            field_name = field_name.lower()
            if ":" in line:
                left, right = line.split(":", 1)
                left = left.strip().lower()
                right = right.strip()
                if left == field_name and len(right) > 0:
                    if cant_be_number:
                        if not right.isdigit():
                            return right
                    else:
                        return right

    return None


_T = TypeVar("_T")


def _get_field(
    cant_be_number: bool,
    raw_string: str,
    convert_to: Callable[[str], _T],
    default_value: Any,
    *field_names: str,
) -> Any:
    retval = _get_field_actual(cant_be_number, raw_string, field_names)

    # Convert the return value
    if retval and convert_to:
        try:
            retval = convert_to(retval)
        except Exception:
            retval = default_value

    # Return the default if there is no return value
    if retval is None:
        retval = default_value

    return retval


def _to_decimal_string(ticks: str) -> str:
    try:
        # Convert to string
        ticks = f"{ticks}"
        # Sometimes ',' is used as a decimal separator
        ticks = ticks.replace(",", ".")

        # Strip off non numbers and decimal places
        ticks = "".join(n for n in ticks if n.isdigit() or n == ".").strip()
        if ticks == "":
            ticks = "0"

        # Add decimal if missing
        if "." not in ticks:
            ticks = f"{ticks}.0"

        # Remove trailing zeros
        ticks = ticks.rstrip("0")

        # Add one trailing zero for empty right side
        if ticks.endswith("."):
            ticks = f"{ticks}0"

        # Make sure the number can be converted to a float
        ticks_f = float(ticks)
        return f"{ticks_f}"
    except Exception:
        return "0.0"


def _hz_short_to_full(ticks: str, scale: int) -> tuple[int, int]:
    try:
        # Make sure the number can be converted to a float
        ticks_f = float(ticks)
        ticks = f"{ticks_f}"

        # Scale the numbers
        hz = ticks.lstrip("0")
        old_index = hz.index(".")
        hz = hz.replace(".", "")
        hz = hz.ljust(scale + old_index + 1, "0")
        new_index = old_index + scale
        hz = f"{hz[:new_index]}.{hz[new_index:]}"
        left, right = hz.split(".")
        left, right = int(left), int(right)
        return (left, right)
    except Exception:
        return (0, 0)


def _hz_friendly_to_full(hz_string: str) -> tuple[int, int]:
    try:
        hz_string = hz_string.strip().lower()
        hz, scale = (None, None)

        if hz_string.endswith("ghz"):
            scale = 9
        elif hz_string.endswith("mhz"):
            scale = 6
        elif hz_string.endswith("hz"):
            scale = 0

        hz = "".join(n for n in hz_string if n.isdigit() or n == ".").strip()
        if "." not in hz:
            hz += ".0"

        return _hz_short_to_full(hz, scale)

    except Exception:
        return (0, 0)


def _hz_short_to_friendly(ticks: int, scale: int) -> str:
    try:
        # Get the raw Hz as a string
        left, right = _hz_short_to_full(ticks, scale)
        result = f"{left}.{right}"

        # Get the location of the dot, and remove said dot
        dot_index = result.index(".")
        result = result.replace(".", "")

        # Get the Hz symbol and scale
        symbol = "Hz"
        scale = 0
        if dot_index > 9:
            symbol = "GHz"
            scale = 9
        elif dot_index > 6:
            symbol = "MHz"
            scale = 6
        elif dot_index > 3:
            symbol = "KHz"
            scale = 3

        # Get the Hz with the dot at the new scaled point
        result = f"{result[: -scale - 1]}.{result[-scale - 1 :]}"

        # Format the ticks to have 4 numbers after the decimal
        # and remove any superfluous zeroes.
        result = f"{float(result):.4f} {symbol}"
        result = result.rstrip("0")
        return result
    except Exception:
        return "0.0000 Hz"


def _to_friendly_bytes(input: Any) -> str:
    import re

    if not input:
        return input
    input = f"{input}"

    formats = {
        r"^[0-9]+B$": "B",
        r"^[0-9]+K$": "KB",
        r"^[0-9]+M$": "MB",
        r"^[0-9]+G$": "GB",
    }

    for pattern, friendly_size in formats.items():
        if re.match(pattern, input):
            return f"{input[:-1].strip()} {friendly_size}"

    return input


def _friendly_bytes_to_int(friendly_bytes: str) -> int:
    input = friendly_bytes.lower()

    formats = [
        {"gib": 1024 * 1024 * 1024},
        {"mib": 1024 * 1024},
        {"kib": 1024},
        {"gb": 1024 * 1024 * 1024},
        {"mb": 1024 * 1024},
        {"kb": 1024},
        {"g": 1024 * 1024 * 1024},
        {"m": 1024 * 1024},
        {"k": 1024},
        {"b": 1},
    ]

    try:
        for entry in formats:
            pattern = list(entry.keys())[0]
            multiplier = list(entry.values())[0]
            if input.endswith(pattern):
                return int(input.split(pattern)[0].strip()) * multiplier

    except Exception:
        pass

    return friendly_bytes


def _filter_dict_keys_with_empty_values(
    info: dict[str, Any], acceptable_values: dict[str, Any] = {}
) -> dict[str, Any]:
    filtered_info = {}
    for key in info:
        value = info[key]

        # Keep if value is acceptable
        if key in acceptable_values:
            if acceptable_values[key] == value:
                filtered_info[key] = value
                continue

        # Filter out None, 0, "", (), {}, []
        if not value:
            continue

        # Filter out (0, 0)
        if value == (0, 0):
            continue

        # Filter out -1
        if value == -1:
            continue

        # Filter out strings that start with "0.0"
        if type(value) is str and value.startswith("0.0"):
            continue

        filtered_info[key] = value

    return filtered_info


def _is_bit_set(reg, bit):
    mask = 1 << bit
    is_set = reg & mask > 0
    return is_set
