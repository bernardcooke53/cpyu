#!/usr/bin/env python

import sys

from cpyu import __version__
from cpyu._system import _check_arch, _get_cpu_info_internal


def main():
    from argparse import ArgumentParser
    import json

    # Parse args
    parser = ArgumentParser(description="Get CPU info with pure Python")
    parser.add_argument(
        "--json", action="store_true", help="Return the info in JSON format"
    )
    parser.add_argument(
        "--version", action="store_true", help="Return the version of cpyu"
    )
    args = parser.parse_args()

    try:
        _check_arch()
    except Exception as err:
        sys.stderr.write(str(err) + "\n")
        sys.exit(1)

    info = _get_cpu_info_internal()

    if not info:
        sys.stderr.write("Failed to find cpu info\n")
        sys.exit(1)

    if args.json:
        print(json.dumps(info))
    elif args.version:
        print(__version__)
    else:
        print("Python Version: {}".format(info.get("python_version", "")))
        print("Cpuinfo Version: {}".format(info.get("cpuinfo_version_string", "")))
        print("Vendor ID Raw: {}".format(info.get("vendor_id_raw", "")))
        print("Hardware Raw: {}".format(info.get("hardware_raw", "")))
        print("Brand Raw: {}".format(info.get("brand_raw", "")))
        print(
            "Hz Advertised Friendly: {}".format(info.get("hz_advertised_friendly", ""))
        )
        print("Hz Actual Friendly: {}".format(info.get("hz_actual_friendly", "")))
        print("Hz Advertised: {}".format(info.get("hz_advertised", "")))
        print("Hz Actual: {}".format(info.get("hz_actual", "")))
        print("Arch: {}".format(info.get("arch", "")))
        print("Bits: {}".format(info.get("bits", "")))
        print("Count: {}".format(info.get("count", "")))
        print("Arch String Raw: {}".format(info.get("arch_string_raw", "")))
        print("L1 Data Cache Size: {}".format(info.get("l1_data_cache_size", "")))
        print(
            "L1 Instruction Cache Size: {}".format(
                info.get("l1_instruction_cache_size", "")
            )
        )
        print("L2 Cache Size: {}".format(info.get("l2_cache_size", "")))
        print("L2 Cache Line Size: {}".format(info.get("l2_cache_line_size", "")))
        print(
            "L2 Cache Associativity: {}".format(info.get("l2_cache_associativity", ""))
        )
        print("L3 Cache Size: {}".format(info.get("l3_cache_size", "")))
        print("Stepping: {}".format(info.get("stepping", "")))
        print("Model: {}".format(info.get("model", "")))
        print("Family: {}".format(info.get("family", "")))
        print("Processor Type: {}".format(info.get("processor_type", "")))
        print("Flags: {}".format(", ".join(info.get("flags", ""))))


if __name__ == "__main__":
    main()
else:
    _check_arch()
