import contextlib
from multiprocessing import Process, Queue
import os
import sys
import logging
from io import StringIO
from typing import Any

from cpyu import __version__
from cpyu._cpuid import CPUID
from cpyu._datasource import DataSource
from cpyu._io import (
    _to_friendly_bytes,
    _b64_to_obj,
    _obj_to_b64,
    _friendly_bytes_to_int,
    _get_field,
    _copy_new_fields,
    _is_bit_set,
    _filter_dict_keys_with_empty_values,
    _to_decimal_string,
    _hz_short_to_friendly,
    _hz_short_to_full,
    _utf_to_str,
)
from cpyu._trace import log_fail

log = logging.getLogger(__name__)

CAN_CALL_CPUID_IN_SUBPROCESS = True


def _program_paths(program_name: str) -> list[str]:
    paths = []
    exts = filter(None, os.environ.get("PATHEXT", "").split(os.pathsep))
    for p in os.environ["PATH"].split(os.pathsep):
        p = os.path.join(p, program_name)
        if os.access(p, os.X_OK):
            paths.append(p)
        for e in exts:
            pext = p + e
            if os.access(pext, os.X_OK):
                paths.append(pext)
    return paths


def _run_and_get_stdout(
    command: list[str], pipe_command: list[str] | None = None
) -> tuple[int, str]:
    from subprocess import Popen, PIPE

    log.info('Running command "' + " ".join(command) + '" ...')

    # Run the command normally
    if not pipe_command:
        p1 = Popen(command, stdout=PIPE, stderr=PIPE, stdin=PIPE)
    # Run the command and pipe it into another command
    else:
        p2 = Popen(command, stdout=PIPE, stderr=PIPE, stdin=PIPE)
        p1 = Popen(pipe_command, stdin=p2.stdout, stdout=PIPE, stderr=PIPE)
        p2.stdout.close()

    # Get the stdout and stderr
    stdout_output, stderr_output = p1.communicate()
    stdout_output = stdout_output.decode(encoding="UTF-8")
    stderr_output = stderr_output.decode(encoding="UTF-8")

    # Send the result to the logger
    log.info("return code:", str(p1.returncode))
    log.info("stdout:", stdout_output)

    # Return the return code and stdout
    return p1.returncode, stdout_output


def _read_windows_registry_key(key_name: str, field_name: str) -> str:
    log.info(f'Reading Registry key "{key_name}" field "{field_name}" ...')

    try:
        import _winreg as winreg
    except ImportError:
        import winreg

    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_name)
    value = winreg.QueryValueEx(key, field_name)[0]
    winreg.CloseKey(key)
    log.info("value:", str(value))
    return value


# Make sure we are running on a supported system
def _check_arch() -> None:
    arch, _ = _parse_arch(DataSource.arch_string_raw)
    if arch not in [
        "ARM_7",
        "ARM_8",
        "LOONG_32",
        "LOONG_64",
        "MIPS_32",
        "MIPS_64",
        "PPC_32",
        "PPC_64",
        "RISCV_32",
        "RISCV_64",
        "SPARC_32",
        "SPARC_64",
        "S390X",
        "X86_32",
        "X86_64",
    ]:
        raise Exception(
            "py-cpuinfo currently only works on X86 "
            "and some ARM/LoongArch/MIPS/PPC/RISCV/SPARC/S390X CPUs."
        )


def _parse_cpu_brand_string(cpu_string: str) -> tuple[str, int]:
    # Just return 0 if the processor brand does not have the Hz
    if "hz" not in cpu_string.lower():
        return ("0.0", 0)

    hz = cpu_string.lower()
    scale = 0

    if hz.endswith("mhz"):
        scale = 6
    elif hz.endswith("ghz"):
        scale = 9
    if "@" in hz:
        hz = hz.split("@")[1]
    else:
        hz = hz.rsplit(None, 1)[1]

    hz = hz.rstrip("mhz").rstrip("ghz").strip()
    hz = _to_decimal_string(hz)

    return (hz, scale)


def _parse_cpu_brand_string_dx(
    cpu_string: str,
) -> tuple[
    str | None, int | None, str | None, str | None, int | None, int | None, int | None
]:
    import re

    # Find all the strings inside brackets ()
    starts = [m.start() for m in re.finditer(r"\(", cpu_string)]
    ends = [m.start() for m in re.finditer(r"\)", cpu_string)]
    insides = {k: v for k, v in zip(starts, ends)}
    insides = [cpu_string[start + 1 : end] for start, end in insides.items()]

    # Find all the fields
    vendor_id, stepping, model, family = (None, None, None, None)
    for inside in insides:
        for pair in inside.split(","):
            pair = [n.strip() for n in pair.split(":")]
            if len(pair) > 1:
                name, value = pair[0], pair[1]
                if name == "origin":
                    vendor_id = value.strip('"')
                elif name == "stepping":
                    stepping = int(value.lstrip("0x"), 16)
                elif name == "model":
                    model = int(value.lstrip("0x"), 16)
                elif name in ["fam", "family"]:
                    family = int(value.lstrip("0x"), 16)

    # Find the Processor Brand
    # Strip off extra strings in brackets at end
    brand = cpu_string.strip()
    is_working = True
    while is_working:
        is_working = False
        for inside in insides:
            full = f"({inside})"
            if brand.endswith(full):
                brand = brand[: -len(full)].strip()
                is_working = True

    # Find the Hz in the brand string
    hz_brand, scale = _parse_cpu_brand_string(brand)

    # Find Hz inside brackets () after the brand string
    if hz_brand == "0.0":
        for inside in insides:
            hz = inside
            for entry in {"GHz", "MHz", "Hz"}:
                if entry in hz:
                    hz = "CPU @ " + hz[: hz.find(entry) + len(entry)]
                    hz_brand, scale = _parse_cpu_brand_string(hz)
                    break

    return (hz_brand, scale, brand, vendor_id, stepping, model, family)


def _parse_dmesg_output(output: str) -> dict[str, Any]:
    try:
        # Get all the dmesg lines that might contain a CPU string
        lines = (
            output.split(" CPU0:")[1:]
            + output.split(" CPU1:")[1:]
            + output.split(" CPU:")[1:]
            + output.split("\nCPU0:")[1:]
            + output.split("\nCPU1:")[1:]
            + output.split("\nCPU:")[1:]
        )
        lines = [l.split("\n")[0].strip() for l in lines]  # noqa: E741

        # Convert the lines to CPU strings
        cpu_strings = [_parse_cpu_brand_string_dx(l) for l in lines]  # noqa: E741

        # Find the CPU string that has the most fields
        best_string = None
        highest_count = 0
        for cpu_string in cpu_strings:
            count = sum([n is not None for n in cpu_string])
            if count > highest_count:
                highest_count = count
                best_string = cpu_string

        # If no CPU string was found, return {}
        if not best_string:
            return {}

        hz_actual, scale, processor_brand, vendor_id, stepping, model, family = (
            best_string
        )

        # Origin
        if "  Origin=" in output:
            fields = output[output.find("  Origin=") :].split("\n")[0]
            fields = fields.strip().split()
            fields = [n.strip().split("=") for n in fields]
            fields = [{n[0].strip().lower(): n[1].strip()} for n in fields]

            for field in fields:
                name = list(field.keys())[0]
                value = list(field.values())[0]

                if name == "origin":
                    vendor_id = value.strip('"')
                elif name == "stepping":
                    stepping = int(value.lstrip("0x"), 16)
                elif name == "model":
                    model = int(value.lstrip("0x"), 16)
                elif name in ["fam", "family"]:
                    family = int(value.lstrip("0x"), 16)

        # Features
        flag_lines = []
        for category in [
            "  Features=",
            "  Features2=",
            "  AMD Features=",
            "  AMD Features2=",
        ]:
            if category in output:
                flag_lines.append(output.split(category)[1].split("\n")[0])

        flags = []
        for line in flag_lines:
            line = line.split("<")[1].split(">")[0].lower()
            for flag in line.split(","):
                flags.append(flag)
        flags.sort()

        # Convert from GHz/MHz string to Hz
        hz_advertised, scale = _parse_cpu_brand_string(processor_brand)

        # If advertised hz not found, use the actual hz
        if hz_advertised == "0.0":
            scale = 6
            hz_advertised = _to_decimal_string(hz_actual)

        info = {
            "vendor_id_raw": vendor_id,
            "brand_raw": processor_brand,
            "stepping": stepping,
            "model": model,
            "family": family,
            "flags": flags,
        }

        if hz_advertised and hz_advertised != "0.0":
            info["hz_advertised_friendly"] = _hz_short_to_friendly(hz_advertised, scale)
            info["hz_actual_friendly"] = _hz_short_to_friendly(hz_actual, scale)
            info["hz_advertised"] = _hz_short_to_full(hz_advertised, scale)
            info["hz_actual"] = _hz_short_to_full(hz_actual, scale)

        return {k: v for k, v in info.items() if v}
    except Exception as err:
        log_fail(err)
        # raise

    return {}


def _parse_arch(arch_string_raw: str) -> tuple[str | None, int | None]:
    import re

    arch, bits = None, None
    arch_string_raw = arch_string_raw.lower()

    # X86
    if re.match(
        r"^i\d86$|^x86$|^x86_32$|^i86pc$|^ia32$|^ia-32$|^bepc$", arch_string_raw
    ):
        arch = "X86_32"
        bits = 32
    elif re.match(
        r"^x64$|^x86_64$|^x86_64t$|^i686-64$|^amd64$|^ia64$|^ia-64$", arch_string_raw
    ):
        arch = "X86_64"
        bits = 64
    # ARM
    elif re.match(r"^armv8-a|aarch64|arm64$", arch_string_raw):
        arch = "ARM_8"
        bits = 64
    elif re.match(r"^armv7$|^armv7[a-z]$|^armv7-[a-z]$|^armv6[a-z]$", arch_string_raw):
        arch = "ARM_7"
        bits = 32
    elif re.match(r"^armv8$|^armv8[a-z]$|^armv8-[a-z]$", arch_string_raw):
        arch = "ARM_8"
        bits = 32
    # PPC
    elif re.match(r"^ppc32$|^prep$|^pmac$|^powermac$", arch_string_raw):
        arch = "PPC_32"
        bits = 32
    elif re.match(r"^powerpc$|^ppc64$|^ppc64le$", arch_string_raw):
        arch = "PPC_64"
        bits = 64
    # SPARC
    elif re.match(r"^sparc32$|^sparc$", arch_string_raw):
        arch = "SPARC_32"
        bits = 32
    elif re.match(r"^sparc64$|^sun4u$|^sun4v$", arch_string_raw):
        arch = "SPARC_64"
        bits = 64
    # S390X
    elif re.match(r"^s390x$", arch_string_raw):
        arch = "S390X"
        bits = 64
    # MIPS
    elif re.match(r"^mips$", arch_string_raw):
        arch = "MIPS_32"
        bits = 32
    elif re.match(r"^mips64$", arch_string_raw):
        arch = "MIPS_64"
        bits = 64
    # RISCV
    elif re.match(r"^riscv$|^riscv32$|^riscv32be$", arch_string_raw):
        arch = "RISCV_32"
        bits = 32
    elif re.match(r"^riscv64$|^riscv64be$", arch_string_raw):
        arch = "RISCV_64"
        bits = 64
    # LoongArch
    elif re.match(r"^loongarch32$", arch_string_raw):
        arch = "LOONG_32"
        bits = 32
    elif re.match(r"^loongarch64$", arch_string_raw):
        arch = "LOONG_64"
        bits = 64

    return (arch, bits)


def _is_selinux_enforcing() -> bool:
    # Just return if the SE Linux Status Tool is not installed
    if not DataSource.has_sestatus():
        log_fail("Failed to find sestatus.")
        return False

    # Run the sestatus, and just return if it failed to run
    returncode, output = DataSource.sestatus_b()
    if returncode != 0:
        log_fail("Failed to run sestatus. Skipping ...")
        return False

    # Figure out if explicitly in enforcing mode
    for line in output.splitlines():
        line = line.strip().lower()
        if line.startswith("current mode:"):
            if line.endswith("enforcing"):
                return True
            else:
                return False

    # Figure out if we can execute heap and execute memory
    can_selinux_exec_heap = False
    can_selinux_exec_memory = False
    for line in output.splitlines():
        line = line.strip().lower()
        if line.startswith("allow_execheap") and line.endswith("on"):
            can_selinux_exec_heap = True
        elif line.startswith("allow_execmem") and line.endswith("on"):
            can_selinux_exec_memory = True

    log.info("can_selinux_exec_heap:", can_selinux_exec_heap)
    log.info("can_selinux_exec_memory:", can_selinux_exec_memory)

    return not can_selinux_exec_heap or not can_selinux_exec_memory


def _get_cpu_info_from_cpuid_actual() -> dict[str, Any]:
    """
    Warning! This function has the potential to crash the Python runtime.
    Do not call it directly. Use the _get_cpu_info_from_cpuid function instead.
    It will safely call this function in another process.
    """

    info = {}

    # Pipe stdout and stderr to strings
    stdout = StringIO()
    stderr = StringIO()

    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            # Get the CPU arch and bits
            arch, _ = _parse_arch(DataSource.arch_string_raw)

            # Return none if this is not an X86 CPU
            if arch not in {"X86_32", "X86_64"}:
                log_fail("Not running on X86_32 or X86_64. Skipping ...")

                return {
                    ## TODO: BC - do we need this?
                    # "output": output.getvalue(),
                    "stdout": stdout.getvalue(),
                    "stderr": stderr.getvalue(),
                    "info": info,
                    "err": None,
                    "is_fail": True,
                }

            # Return none if SE Linux is in enforcing mode
            cpuid = CPUID()
            if cpuid.is_selinux_enforcing:
                log_fail("SELinux is enforcing. Skipping ...")
                return {
                    ## TODO: BC - do we need this?
                    # "output": output.getvalue(),
                    "stdout": stdout.getvalue(),
                    "stderr": stderr.getvalue(),
                    "info": info,
                    "err": None,
                    "is_fail": True,
                }

            # Get the cpu info from the CPUID register
            max_extension_support = cpuid.get_max_extension_support()
            cache_info = cpuid.get_cache(max_extension_support)
            info = cpuid.get_info()

            processor_brand = cpuid.get_processor_brand(max_extension_support)

            # Get the Hz and scale
            hz_actual = cpuid.get_raw_hz()
            hz_actual = _to_decimal_string(hz_actual)

            # Get the Hz and scale
            hz_advertised, scale = _parse_cpu_brand_string(processor_brand)
            info = {
                "vendor_id_raw": cpuid.get_vendor_id(),
                "hardware_raw": "",
                "brand_raw": processor_brand,
                "hz_advertised_friendly": _hz_short_to_friendly(hz_advertised, scale),
                "hz_actual_friendly": _hz_short_to_friendly(hz_actual, 0),
                "hz_advertised": _hz_short_to_full(hz_advertised, scale),
                "hz_actual": _hz_short_to_full(hz_actual, 0),
                "l2_cache_size": cache_info["size_b"],
                "l2_cache_line_size": cache_info["line_size_b"],
                "l2_cache_associativity": cache_info["associativity"],
                "stepping": info["stepping"],
                "model": info["model"],
                "family": info["family"],
                "processor_type": info["processor_type"],
                "flags": cpuid.get_flags(max_extension_support),
            }

            info = _filter_dict_keys_with_empty_values(info)
    except Exception as err:
        log_fail(err)
        from traceback import format_exc

        err_string = "".join([f"\t\t{n}\n" for n in format_exc().split("\n")]) + "\n"

        log_fail(err_string)
        return {
            ## TODO: BC - do we need this?
            # "output": output.getvalue(),
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "info": info,
            "err": err_string,
            "is_fail": True,
        }

    return {
        ## TODO: BC - do we need this?
        # "output": output.getvalue(),
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
        "info": info,
        "err": None,
        "is_fail": False,
    }


def _get_cpu_info_from_cpuid_subprocess_wrapper(queue: Queue) -> None:
    orig_stdout = sys.stdout
    orig_stderr = sys.stderr

    output = _get_cpu_info_from_cpuid_actual()

    sys.stdout = orig_stdout
    sys.stderr = orig_stderr

    queue.put(_obj_to_b64(output))


def _get_cpu_info_from_cpuid() -> dict[str, Any]:
    """
    Returns the CPU info gathered by querying the X86 cpuid register in a new process.
    Returns {} on non X86 cpus.
    Returns {} if SELinux is in enforcing mode.
    """

    log.info("Tying to get info from CPUID ...")

    # Return {} if can't cpuid
    if not DataSource.can_cpuid:
        log_fail("Can't CPUID. Skipping ...")
        return {}

    # Get the CPU arch and bits
    arch, _ = _parse_arch(DataSource.arch_string_raw)

    # Return {} if this is not an X86 CPU
    if arch not in {"X86_32", "X86_64"}:
        log_fail("Not running on X86_32 or X86_64. Skipping ...")
        return {}

    try:
        if CAN_CALL_CPUID_IN_SUBPROCESS:
            # Start running the function in a subprocess
            queue = Queue()
            p = Process(
                target=_get_cpu_info_from_cpuid_subprocess_wrapper, args=(queue,)
            )
            p.start()

            # Wait for the process to end, while it is still alive
            while p.is_alive():
                p.join(0)

            # Return {} if it failed
            if p.exitcode != 0:
                log_fail("Failed to run CPUID in process. Skipping ...")
                return {}

            # Return {} if no results
            if queue.empty():
                log_fail("Failed to get anything from CPUID process. Skipping ...")
                return {}
            # Return the result, only if there is something to read
            else:
                output = _b64_to_obj(queue.get())
                # import pprint

                # pp = pprint.PrettyPrinter(indent=4)
                # pp.pprint(output)

                if "output" in output and output["output"]:
                    log.info(output["output"])

                if "stdout" in output and output["stdout"]:
                    sys.stdout.write("{}\n".format(output["stdout"]))
                    sys.stdout.flush()

                if "stderr" in output and output["stderr"]:
                    sys.stderr.write("{}\n".format(output["stderr"]))
                    sys.stderr.flush()

                if "is_fail" not in output:
                    log_fail("Failed to get is_fail from CPUID process. Skipping ...")
                    return {}

                # Fail if there was an exception
                if "err" in output and output["err"]:
                    log_fail("Failed to run CPUID in process. Skipping ...")
                    log.error(output["err"])
                    log.error("Failed ...")
                    return {}

                if "is_fail" in output and output["is_fail"]:
                    log_fail("Failed ...")
                    return {}

                if "info" not in output or not output["info"]:
                    log_fail(
                        "Failed to get return info from CPUID process. Skipping ..."
                    )
                    return {}

                return output["info"]
        else:
            # FIXME: This should write the values like in the above call to actual
            orig_stdout = sys.stdout
            orig_stderr = sys.stderr

            output = _get_cpu_info_from_cpuid_actual()

            sys.stdout = orig_stdout
            sys.stderr = orig_stderr

            log.info(output)

            ## TODO: BC - what to do about this?
            # g_trace.success()
            return output["info"]
    except Exception as err:
        log_fail(err)

    # Return {} if everything failed
    return {}


def _get_cpu_info_from_proc_cpuinfo() -> dict[str, Any]:
    """
    Returns the CPU info gathered from /proc/cpuinfo.
    Returns {} if /proc/cpuinfo is not found.
    """

    log.info("Tying to get info from /proc/cpuinfo ...")

    try:
        # Just return {} if there is no cpuinfo
        if not DataSource.has_proc_cpuinfo():
            log_fail("Failed to find /proc/cpuinfo. Skipping ...")
            return {}

        returncode, output = DataSource.cat_proc_cpuinfo()
        if returncode != 0:
            log_fail("Failed to run cat /proc/cpuinfo. Skipping ...")
            return {}

        # Various fields
        vendor_id = _get_field(
            False, output, None, "", "vendor_id", "vendor id", "vendor"
        )
        processor_brand = _get_field(
            True, output, None, None, "model name", "cpu", "processor", "uarch"
        )
        cache_size = _get_field(False, output, None, "", "cache size")
        stepping = _get_field(False, output, int, -1, "stepping")
        model = _get_field(False, output, int, -1, "model")
        family = _get_field(False, output, int, -1, "cpu family")
        hardware = _get_field(False, output, None, "", "Hardware")

        # Flags
        flags = _get_field(
            False, output, None, None, "flags", "Features", "ASEs implemented"
        )
        if flags:
            flags = flags.split()
            flags.sort()

        # Check for other cache format
        if not cache_size:
            try:
                for i in range(0, 10):
                    name = f"cache{i}"
                    value = _get_field(False, output, None, None, name)
                    if value:
                        value = [entry.split("=") for entry in value.split(" ")]
                        value = dict(value)
                        if (
                            "level" in value
                            and value["level"] == "3"
                            and "size" in value
                        ):
                            cache_size = value["size"]
                            break
            except Exception:
                pass

        # Convert from MHz string to Hz
        hz_actual = _get_field(
            False,
            output,
            None,
            "",
            "cpu MHz",
            "cpu speed",
            "clock",
            "cpu MHz dynamic",
            "cpu MHz static",
        )
        hz_actual = hz_actual.lower().rstrip("mhz").strip()
        hz_actual = _to_decimal_string(hz_actual)

        # Convert from GHz/MHz string to Hz
        hz_advertised, scale = (None, 0)
        try:
            hz_advertised, scale = _parse_cpu_brand_string(processor_brand)
        except Exception:
            pass

        info = {
            "hardware_raw": hardware,
            "brand_raw": processor_brand,
            "l3_cache_size": _friendly_bytes_to_int(cache_size),
            "flags": flags,
            "vendor_id_raw": vendor_id,
            "stepping": stepping,
            "model": model,
            "family": family,
        }

        # Make the Hz the same for actual and advertised if missing any
        if not hz_advertised or hz_advertised == "0.0":
            hz_advertised = hz_actual
            scale = 6
        elif not hz_actual or hz_actual == "0.0":
            hz_actual = hz_advertised

        # Add the Hz if there is one
        if _hz_short_to_full(hz_advertised, scale) > (0, 0):
            info["hz_advertised_friendly"] = _hz_short_to_friendly(hz_advertised, scale)
            info["hz_advertised"] = _hz_short_to_full(hz_advertised, scale)
        if _hz_short_to_full(hz_actual, scale) > (0, 0):
            info["hz_actual_friendly"] = _hz_short_to_friendly(hz_actual, 6)
            info["hz_actual"] = _hz_short_to_full(hz_actual, 6)

        info = _filter_dict_keys_with_empty_values(
            info, {"stepping": 0, "model": 0, "family": 0}
        )
        # g_trace.success()
        log.info(info)
        return info
    except Exception as err:
        log_fail(err)
        # raise # NOTE: To have this throw on error, uncomment this line
        return {}


def _get_cpu_info_from_cpufreq_info() -> dict[str, Any]:
    """
    Returns the CPU info gathered from cpufreq-info.
    Returns {} if cpufreq-info is not found.
    """

    log.info("Tying to get info from cpufreq-info ...")

    try:
        hz_brand, scale = "0.0", 0

        if not DataSource.has_cpufreq_info():
            log_fail("Failed to find cpufreq-info. Skipping ...")
            return {}

        returncode, output = DataSource.cpufreq_info()
        if returncode != 0:
            log_fail("Failed to run cpufreq-info. Skipping ...")
            return {}

        hz_brand = output.split("current CPU frequency is")[1].split("\n")[0]
        i = hz_brand.find("Hz")
        assert i != -1
        hz_brand = hz_brand[0 : i + 2].strip().lower()

        if hz_brand.endswith("mhz"):
            scale = 6
        elif hz_brand.endswith("ghz"):
            scale = 9
        hz_brand = hz_brand.rstrip("mhz").rstrip("ghz").strip()
        hz_brand = _to_decimal_string(hz_brand)

        info = {
            "hz_advertised_friendly": _hz_short_to_friendly(hz_brand, scale),
            "hz_actual_friendly": _hz_short_to_friendly(hz_brand, scale),
            "hz_advertised": _hz_short_to_full(hz_brand, scale),
            "hz_actual": _hz_short_to_full(hz_brand, scale),
        }

        info = _filter_dict_keys_with_empty_values(info)
        log.info(info)
        # g_trace.success()
        return info
    except Exception as err:
        log_fail(err)
        # raise # NOTE: To have this throw on error, uncomment this line
        return {}


def _get_cpu_info_from_lscpu() -> dict[str, Any]:
    """
    Returns the CPU info gathered from lscpu.
    Returns {} if lscpu is not found.
    """

    log.info("Tying to get info from lscpu ...")

    try:
        if not DataSource.has_lscpu():
            log_fail("Failed to find lscpu. Skipping ...")
            return {}

        returncode, output = DataSource.lscpu()
        if returncode != 0:
            log_fail("Failed to run lscpu. Skipping ...")
            return {}

        info = {}

        new_hz = _get_field(False, output, None, None, "CPU max MHz", "CPU MHz")
        if new_hz:
            new_hz = _to_decimal_string(new_hz)
            scale = 6
            info["hz_advertised_friendly"] = _hz_short_to_friendly(new_hz, scale)
            info["hz_actual_friendly"] = _hz_short_to_friendly(new_hz, scale)
            info["hz_advertised"] = _hz_short_to_full(new_hz, scale)
            info["hz_actual"] = _hz_short_to_full(new_hz, scale)

        new_hz = _get_field(
            False, output, None, None, "CPU dynamic MHz", "CPU static MHz"
        )
        if new_hz:
            new_hz = _to_decimal_string(new_hz)
            scale = 6
            info["hz_advertised_friendly"] = _hz_short_to_friendly(new_hz, scale)
            info["hz_actual_friendly"] = _hz_short_to_friendly(new_hz, scale)
            info["hz_advertised"] = _hz_short_to_full(new_hz, scale)
            info["hz_actual"] = _hz_short_to_full(new_hz, scale)

        vendor_id = _get_field(False, output, None, None, "Vendor ID")
        if vendor_id:
            info["vendor_id_raw"] = vendor_id

        brand = _get_field(False, output, None, None, "Model name")
        if brand:
            info["brand_raw"] = brand
        else:
            brand = _get_field(False, output, None, None, "Model")
            if brand and not brand.isdigit():
                info["brand_raw"] = brand

        family = _get_field(False, output, None, None, "CPU family")
        if family and family.isdigit():
            info["family"] = int(family)

        stepping = _get_field(False, output, None, None, "Stepping")
        if stepping and stepping.isdigit():
            info["stepping"] = int(stepping)

        model = _get_field(False, output, None, None, "Model")
        if model and model.isdigit():
            info["model"] = int(model)

        l1_data_cache_size = _get_field(False, output, None, None, "L1d cache")
        if l1_data_cache_size:
            l1_data_cache_size = l1_data_cache_size.split("(")[0].strip()
            info["l1_data_cache_size"] = _friendly_bytes_to_int(l1_data_cache_size)

        l1_instruction_cache_size = _get_field(False, output, None, None, "L1i cache")
        if l1_instruction_cache_size:
            l1_instruction_cache_size = l1_instruction_cache_size.split("(")[0].strip()
            info["l1_instruction_cache_size"] = _friendly_bytes_to_int(
                l1_instruction_cache_size
            )

        l2_cache_size = _get_field(False, output, None, None, "L2 cache", "L2d cache")
        if l2_cache_size:
            l2_cache_size = l2_cache_size.split("(")[0].strip()
            info["l2_cache_size"] = _friendly_bytes_to_int(l2_cache_size)

        l3_cache_size = _get_field(False, output, None, None, "L3 cache")
        if l3_cache_size:
            l3_cache_size = l3_cache_size.split("(")[0].strip()
            info["l3_cache_size"] = _friendly_bytes_to_int(l3_cache_size)

        # Flags
        flags = _get_field(
            False, output, None, None, "flags", "Features", "ASEs implemented"
        )
        if flags:
            flags = flags.split()
            flags.sort()
            info["flags"] = flags

        info = _filter_dict_keys_with_empty_values(
            info, {"stepping": 0, "model": 0, "family": 0}
        )
        log.info(info)
        # g_trace.success()
        return info
    except Exception as err:
        log_fail(err)
        # raise # NOTE: To have this throw on error, uncomment this line
        return {}


def _get_cpu_info_from_dmesg() -> dict[str, Any]:
    """
    Returns the CPU info gathered from dmesg.
    Returns {} if dmesg is not found or does not have the desired info.
    """

    log.info("Tying to get info from the dmesg ...")

    # Just return {} if this arch has an unreliable dmesg log
    arch, _ = _parse_arch(DataSource.arch_string_raw)
    if arch in {"S390X"}:
        log_fail("Running on S390X. Skipping ...")
        return {}

    # Just return {} if there is no dmesg
    if not DataSource.has_dmesg():
        log_fail("Failed to find dmesg. Skipping ...")
        return {}

    # If dmesg fails return {}
    returncode, output = DataSource.dmesg_a()
    if output is None or returncode != 0:
        log_fail('Failed to run "dmesg -a". Skipping ...')
        return {}

    info = _parse_dmesg_output(output)
    log.info(info)
    # g_trace.success()
    return info


# https://openpowerfoundation.org/wp-content/uploads/2016/05/LoPAPR_DRAFT_v11_24March2016_cmt1.pdf
# page 767
def _get_cpu_info_from_ibm_pa_features() -> dict[str, Any]:
    """
    Returns the CPU info gathered from lsprop /proc/device-tree/cpus/*/ibm,pa-features
    Returns {} if lsprop is not found or ibm,pa-features does not have the desired info.
    """

    log.info("Tying to get info from lsprop ...")

    try:
        # Just return {} if there is no lsprop
        if not DataSource.has_ibm_pa_features():
            log_fail("Failed to find lsprop. Skipping ...")
            return {}

        # If ibm,pa-features fails return {}
        returncode, output = DataSource.ibm_pa_features()
        if output is None or returncode != 0:
            log_fail(
                "Failed to glob /proc/device-tree/cpus/*/ibm,pa-features. Skipping ..."
            )
            return {}

        # Filter out invalid characters from output
        value = output.split("ibm,pa-features")[1].lower()
        value = [s for s in value if s in list("0123456789abcfed")]
        value = "".join(value)

        # Get data converted to Uint32 chunks
        left = int(value[0:8], 16)
        right = int(value[8:16], 16)

        # Get the CPU flags
        flags = {
            # Byte 0
            "mmu": _is_bit_set(left, 0),
            "fpu": _is_bit_set(left, 1),
            "slb": _is_bit_set(left, 2),
            "run": _is_bit_set(left, 3),
            #'reserved' : _is_bit_set(left, 4),
            "dabr": _is_bit_set(left, 5),
            "ne": _is_bit_set(left, 6),
            "wtr": _is_bit_set(left, 7),
            # Byte 1
            "mcr": _is_bit_set(left, 8),
            "dsisr": _is_bit_set(left, 9),
            "lp": _is_bit_set(left, 10),
            "ri": _is_bit_set(left, 11),
            "dabrx": _is_bit_set(left, 12),
            "sprg3": _is_bit_set(left, 13),
            "rislb": _is_bit_set(left, 14),
            "pp": _is_bit_set(left, 15),
            # Byte 2
            "vpm": _is_bit_set(left, 16),
            "dss_2.05": _is_bit_set(left, 17),
            #'reserved' : _is_bit_set(left, 18),
            "dar": _is_bit_set(left, 19),
            #'reserved' : _is_bit_set(left, 20),
            "ppr": _is_bit_set(left, 21),
            "dss_2.02": _is_bit_set(left, 22),
            "dss_2.06": _is_bit_set(left, 23),
            # Byte 3
            "lsd_in_dscr": _is_bit_set(left, 24),
            "ugr_in_dscr": _is_bit_set(left, 25),
            #'reserved' : _is_bit_set(left, 26),
            #'reserved' : _is_bit_set(left, 27),
            #'reserved' : _is_bit_set(left, 28),
            #'reserved' : _is_bit_set(left, 29),
            #'reserved' : _is_bit_set(left, 30),
            #'reserved' : _is_bit_set(left, 31),
            # Byte 4
            "sso_2.06": _is_bit_set(right, 0),
            #'reserved' : _is_bit_set(right, 1),
            #'reserved' : _is_bit_set(right, 2),
            #'reserved' : _is_bit_set(right, 3),
            #'reserved' : _is_bit_set(right, 4),
            #'reserved' : _is_bit_set(right, 5),
            #'reserved' : _is_bit_set(right, 6),
            #'reserved' : _is_bit_set(right, 7),
            # Byte 5
            "le": _is_bit_set(right, 8),
            "cfar": _is_bit_set(right, 9),
            "eb": _is_bit_set(right, 10),
            "lsq_2.07": _is_bit_set(right, 11),
            #'reserved' : _is_bit_set(right, 12),
            #'reserved' : _is_bit_set(right, 13),
            #'reserved' : _is_bit_set(right, 14),
            #'reserved' : _is_bit_set(right, 15),
            # Byte 6
            "dss_2.07": _is_bit_set(right, 16),
            #'reserved' : _is_bit_set(right, 17),
            #'reserved' : _is_bit_set(right, 18),
            #'reserved' : _is_bit_set(right, 19),
            #'reserved' : _is_bit_set(right, 20),
            #'reserved' : _is_bit_set(right, 21),
            #'reserved' : _is_bit_set(right, 22),
            #'reserved' : _is_bit_set(right, 23),
            # Byte 7
            #'reserved' : _is_bit_set(right, 24),
            #'reserved' : _is_bit_set(right, 25),
            #'reserved' : _is_bit_set(right, 26),
            #'reserved' : _is_bit_set(right, 27),
            #'reserved' : _is_bit_set(right, 28),
            #'reserved' : _is_bit_set(right, 29),
            #'reserved' : _is_bit_set(right, 30),
            #'reserved' : _is_bit_set(right, 31),
        }

        # Get a list of only the flags that are true
        flags = [k for k, v in flags.items() if v]
        flags.sort()

        info = {"flags": flags}
        info = _filter_dict_keys_with_empty_values(info)
        log.info(info)
        # g_trace.success()
        return info
    except Exception as err:
        log_fail(err)
        return {}


def _get_cpu_info_from_cat_var_run_dmesg_boot() -> dict[str, Any]:
    """
    Returns the CPU info gathered from /var/run/dmesg.boot.
    Returns {} if dmesg is not found or does not have the desired info.
    """

    log.info("Tying to get info from the /var/run/dmesg.boot log ...")

    # Just return {} if there is no /var/run/dmesg.boot
    if not DataSource.has_var_run_dmesg_boot():
        log_fail("Failed to find /var/run/dmesg.boot file. Skipping ...")
        return {}

    # If dmesg.boot fails return {}
    returncode, output = DataSource.cat_var_run_dmesg_boot()
    if output is None or returncode != 0:
        log_fail('Failed to run "cat /var/run/dmesg.boot". Skipping ...')
        return {}

    info = _parse_dmesg_output(output)
    log.info(info)
    # g_trace.success()
    return info


def _get_cpu_info_from_sysctl() -> dict[str, Any]:
    """
    Returns the CPU info gathered from sysctl.
    Returns {} if sysctl is not found.
    """

    log.info("Tying to get info from sysctl ...")

    try:
        # Just return {} if there is no sysctl
        if not DataSource.has_sysctl():
            log_fail("Failed to find sysctl. Skipping ...")
            return {}

        # If sysctl fails return {}
        returncode, output = DataSource.sysctl_machdep_cpu_hw_cpufrequency()
        if output is None or returncode != 0:
            log_fail('Failed to run "sysctl machdep.cpu hw.cpufrequency". Skipping ...')
            return {}

        # Various fields
        vendor_id = _get_field(False, output, None, None, "machdep.cpu.vendor")
        processor_brand = _get_field(
            True, output, None, None, "machdep.cpu.brand_string"
        )
        cache_size = _get_field(False, output, int, 0, "machdep.cpu.cache.size")
        stepping = _get_field(False, output, int, 0, "machdep.cpu.stepping")
        model = _get_field(False, output, int, 0, "machdep.cpu.model")
        family = _get_field(False, output, int, 0, "machdep.cpu.family")

        # Flags
        flags = (
            _get_field(False, output, None, "", "machdep.cpu.features").lower().split()
        )
        flags.extend(
            _get_field(False, output, None, "", "machdep.cpu.leaf7_features")
            .lower()
            .split()
        )
        flags.extend(
            _get_field(False, output, None, "", "machdep.cpu.extfeatures")
            .lower()
            .split()
        )
        flags.sort()

        # Convert from GHz/MHz string to Hz
        hz_advertised, scale = _parse_cpu_brand_string(processor_brand)
        hz_actual = _get_field(False, output, None, None, "hw.cpufrequency")
        hz_actual = _to_decimal_string(hz_actual)

        info = {
            "vendor_id_raw": vendor_id,
            "brand_raw": processor_brand,
            "hz_advertised_friendly": _hz_short_to_friendly(hz_advertised, scale),
            "hz_actual_friendly": _hz_short_to_friendly(hz_actual, 0),
            "hz_advertised": _hz_short_to_full(hz_advertised, scale),
            "hz_actual": _hz_short_to_full(hz_actual, 0),
            "l2_cache_size": int(cache_size) * 1024,
            "stepping": stepping,
            "model": model,
            "family": family,
            "flags": flags,
        }

        info = _filter_dict_keys_with_empty_values(info)
        log.info(info)
        # g_trace.success()
        return info
    except Exception as err:
        log_fail(err)
        return {}


def _get_cpu_info_from_sysinfo() -> dict[str, Any]:
    """
    Returns the CPU info gathered from sysinfo.
    Returns {} if sysinfo is not found.
    """

    info = _get_cpu_info_from_sysinfo_v1()
    info.update(_get_cpu_info_from_sysinfo_v2())
    return info


def _get_cpu_info_from_sysinfo_v1() -> dict[str, Any]:
    """
    Returns the CPU info gathered from sysinfo.
    Returns {} if sysinfo is not found.
    """

    log.info("Tying to get info from sysinfo version 1 ...")

    try:
        # Just return {} if there is no sysinfo
        if not DataSource.has_sysinfo():
            log_fail("Failed to find sysinfo. Skipping ...")
            return {}

        # If sysinfo fails return {}
        returncode, output = DataSource.sysinfo_cpu()
        if output is None or returncode != 0:
            log_fail('Failed to run "sysinfo -cpu". Skipping ...')
            return {}

        # Various fields
        vendor_id = ""  # _get_field(False, output, None, None, 'CPU #0: ')
        processor_brand = output.split('CPU #0: "')[1].split('"\n')[0].strip()
        cache_size = (
            ""  # _get_field(False, output, None, None, 'machdep.cpu.cache.size')
        )
        stepping = int(output.split(", stepping ")[1].split(",")[0].strip())
        model = int(output.split(", model ")[1].split(",")[0].strip())
        family = int(output.split(", family ")[1].split(",")[0].strip())

        # Flags
        flags = []
        for line in output.split("\n"):
            if line.startswith("\t\t"):
                for flag in line.strip().lower().split():
                    flags.append(flag)
        flags.sort()

        # Convert from GHz/MHz string to Hz
        hz_advertised, scale = _parse_cpu_brand_string(processor_brand)
        hz_actual = hz_advertised

        info = {
            "vendor_id_raw": vendor_id,
            "brand_raw": processor_brand,
            "hz_advertised_friendly": _hz_short_to_friendly(hz_advertised, scale),
            "hz_actual_friendly": _hz_short_to_friendly(hz_actual, scale),
            "hz_advertised": _hz_short_to_full(hz_advertised, scale),
            "hz_actual": _hz_short_to_full(hz_actual, scale),
            "l2_cache_size": _to_friendly_bytes(cache_size),
            "stepping": stepping,
            "model": model,
            "family": family,
            "flags": flags,
        }

        info = _filter_dict_keys_with_empty_values(info)
        log.info(info)
        # g_trace.success()
        return info
    except Exception as err:
        log_fail(err)
        # raise # NOTE: To have this throw on error, uncomment this line
        return {}


def _get_cpu_info_from_sysinfo_v2() -> dict[str, Any]:
    """
    Returns the CPU info gathered from sysinfo.
    Returns {} if sysinfo is not found.
    """

    log.info("Tying to get info from sysinfo version 2 ...")

    try:
        # Just return {} if there is no sysinfo
        if not DataSource.has_sysinfo():
            log_fail("Failed to find sysinfo. Skipping ...")
            return {}

        # If sysinfo fails return {}
        returncode, output = DataSource.sysinfo_cpu()
        if output is None or returncode != 0:
            log_fail('Failed to run "sysinfo -cpu". Skipping ...')
            return {}

        # Various fields
        vendor_id = ""  # _get_field(False, output, None, None, 'CPU #0: ')
        processor_brand = output.split('CPU #0: "')[1].split('"\n')[0].strip()
        cache_size = (
            ""  # _get_field(False, output, None, None, 'machdep.cpu.cache.size')
        )
        signature = output.split("Signature:")[1].split("\n")[0].strip()
        #
        stepping = int(signature.split("stepping ")[1].split(",")[0].strip())
        model = int(signature.split("model ")[1].split(",")[0].strip())
        family = int(signature.split("family ")[1].split(",")[0].strip())

        # Flags
        def get_subsection_flags(output: str) -> list[str]:
            retval = []
            for line in output.split("\n")[1:]:
                if not line.startswith("                ") and not line.startswith(
                    "		"
                ):
                    break
                for entry in line.strip().lower().split(" "):
                    retval.append(entry)
            return retval

        flags = (
            get_subsection_flags(output.split("Features: ")[1])
            + get_subsection_flags(output.split("Extended Features (0x00000001): ")[1])
            + get_subsection_flags(output.split("Extended Features (0x80000001): ")[1])
        )
        flags.sort()

        # Convert from GHz/MHz string to Hz
        lines = [n for n in output.split("\n") if n]
        raw_hz = lines[0].split("running at ")[1].strip().lower()
        hz_advertised = raw_hz.rstrip("mhz").rstrip("ghz").strip()
        hz_advertised = _to_decimal_string(hz_advertised)
        hz_actual = hz_advertised

        scale = 0
        if raw_hz.endswith("mhz"):
            scale = 6
        elif raw_hz.endswith("ghz"):
            scale = 9

        info = {
            "vendor_id_raw": vendor_id,
            "brand_raw": processor_brand,
            "hz_advertised_friendly": _hz_short_to_friendly(hz_advertised, scale),
            "hz_actual_friendly": _hz_short_to_friendly(hz_actual, scale),
            "hz_advertised": _hz_short_to_full(hz_advertised, scale),
            "hz_actual": _hz_short_to_full(hz_actual, scale),
            "l2_cache_size": _to_friendly_bytes(cache_size),
            "stepping": stepping,
            "model": model,
            "family": family,
            "flags": flags,
        }

        info = _filter_dict_keys_with_empty_values(info)
        # g_trace.success()
        log.info(info)
        return info
    except Exception as err:
        log_fail(err)
        # raise # NOTE: To have this throw on error, uncomment this line
        return {}


def _get_cpu_info_from_wmic() -> dict[str, Any]:
    """
    Returns the CPU info gathered from WMI.
    Returns {} if not on Windows, or wmic is not installed.
    """
    log.info("Tying to get info from wmic ...")

    try:
        # Just return {} if not Windows or there is no wmic
        if not DataSource.is_windows or not DataSource.has_wmic():
            log_fail("Failed to find WMIC, or not on Windows. Skipping ...")
            return {}

        returncode, output = DataSource.wmic_cpu()
        if output is None or returncode != 0:
            log_fail("Failed to run wmic. Skipping ...")
            return {}

        # Break the list into key values pairs
        value = output.split("\n")
        value = [s.rstrip().split("=") for s in value if "=" in s]
        value = {k: v for k, v in value if v}

        # Get the advertised MHz
        processor_brand = value.get("Name")
        hz_advertised, scale_advertised = _parse_cpu_brand_string(processor_brand)

        # Get the actual MHz
        hz_actual = value.get("CurrentClockSpeed")
        scale_actual = 6
        if hz_actual:
            hz_actual = _to_decimal_string(hz_actual)

        # Get cache sizes
        l2_cache_size = value.get("L2CacheSize")  # NOTE: L2CacheSize is in kilobytes
        if l2_cache_size:
            l2_cache_size = int(l2_cache_size) * 1024

        l3_cache_size = value.get("L3CacheSize")  # NOTE: L3CacheSize is in kilobytes
        if l3_cache_size:
            l3_cache_size = int(l3_cache_size) * 1024

        # Get family, model, and stepping
        family, model, stepping = "", "", ""
        description = value.get("Description") or value.get("Caption")
        entries = description.split(" ")

        if "Family" in entries and entries.index("Family") < len(entries) - 1:
            i = entries.index("Family")
            family = int(entries[i + 1])

        if "Model" in entries and entries.index("Model") < len(entries) - 1:
            i = entries.index("Model")
            model = int(entries[i + 1])

        if "Stepping" in entries and entries.index("Stepping") < len(entries) - 1:
            i = entries.index("Stepping")
            stepping = int(entries[i + 1])

        info = {
            "vendor_id_raw": value.get("Manufacturer"),
            "brand_raw": processor_brand,
            "hz_advertised_friendly": _hz_short_to_friendly(
                hz_advertised, scale_advertised
            ),
            "hz_actual_friendly": _hz_short_to_friendly(hz_actual, scale_actual),
            "hz_advertised": _hz_short_to_full(hz_advertised, scale_advertised),
            "hz_actual": _hz_short_to_full(hz_actual, scale_actual),
            "l2_cache_size": l2_cache_size,
            "l3_cache_size": l3_cache_size,
            "stepping": stepping,
            "model": model,
            "family": family,
        }

        info = _filter_dict_keys_with_empty_values(info)
        # g_trace.success()
        log.info(info)
        return info
    except Exception as err:
        log_fail(err)
        # raise # NOTE: To have this throw on error, uncomment this line
        return {}


def _get_cpu_info_from_registry() -> dict[str, Any]:
    """
    Returns the CPU info gathered from the Windows Registry.
    Returns {} if not on Windows.
    """

    log.info("Tying to get info from Windows registry ...")

    try:
        # Just return {} if not on Windows
        if not DataSource.is_windows:
            log_fail("Not running on Windows. Skipping ...")
            return {}

        # Get the CPU name
        processor_brand = DataSource.winreg_processor_brand().strip()

        # Get the CPU vendor id
        vendor_id = DataSource.winreg_vendor_id_raw()

        # Get the CPU arch and bits
        arch_string_raw = DataSource.winreg_arch_string_raw()
        arch, bits = _parse_arch(arch_string_raw)

        # Get the actual CPU Hz
        hz_actual = DataSource.winreg_hz_actual()
        hz_actual = _to_decimal_string(hz_actual)

        # Get the advertised CPU Hz
        hz_advertised, scale = _parse_cpu_brand_string(processor_brand)

        # If advertised hz not found, use the actual hz
        if hz_advertised == "0.0":
            scale = 6
            hz_advertised = _to_decimal_string(hz_actual)

        # Get the CPU features
        feature_bits = DataSource.winreg_feature_bits()

        def is_set(bit: int) -> int:
            mask = 0x80000000 >> bit
            retval = mask & feature_bits > 0
            return retval

        # http://en.wikipedia.org/wiki/CPUID
        # http://unix.stackexchange.com/questions/43539/what-do-the-flags-in-proc-cpuinfo-mean
        # http://www.lohninger.com/helpcsuite/public_constants_cpuid.htm
        flags = {
            "fpu": is_set(0),  # Floating Point Unit
            "vme": is_set(1),  # V86 Mode Extensions
            "de": is_set(2),  # Debug Extensions - I/O breakpoints supported
            "pse": is_set(3),  # Page Size Extensions (4 MB pages supported)
            "tsc": is_set(4),  # Time Stamp Counter and RDTSC instruction are available
            "msr": is_set(5),  # Model Specific Registers
            "pae": is_set(6),  # Physical Address Extensions (36 bit address, 2MB pages)
            "mce": is_set(7),  # Machine Check Exception supported
            "cx8": is_set(8),  # Compare Exchange Eight Byte instruction available
            "apic": is_set(9),  # Local APIC present (multiprocessor operation support)
            "sepamd": is_set(10),  # Fast system calls (AMD only)
            "sep": is_set(11),  # Fast system calls
            "mtrr": is_set(12),  # Memory Type Range Registers
            "pge": is_set(13),  # Page Global Enable
            "mca": is_set(14),  # Machine Check Architecture
            "cmov": is_set(15),  # Conditional MOVe instructions
            "pat": is_set(16),  # Page Attribute Table
            "pse36": is_set(17),  # 36 bit Page Size Extensions
            "serial": is_set(18),  # Processor Serial Number
            "clflush": is_set(19),  # Cache Flush
            #'reserved1' : is_set(20), # reserved
            "dts": is_set(21),  # Debug Trace Store
            "acpi": is_set(22),  # ACPI support
            "mmx": is_set(23),  # MultiMedia Extensions
            "fxsr": is_set(24),  # FXSAVE and FXRSTOR instructions
            "sse": is_set(25),  # SSE instructions
            "sse2": is_set(26),  # SSE2 (WNI) instructions
            "ss": is_set(27),  # self snoop
            #'reserved2' : is_set(28), # reserved
            "tm": is_set(29),  # Automatic clock control
            "ia64": is_set(30),  # IA64 instructions
            "3dnow": is_set(31),  # 3DNow! instructions available
        }

        # Get a list of only the flags that are true
        flags = [k for k, v in flags.items() if v]
        flags.sort()

        info = {
            "vendor_id_raw": vendor_id,
            "brand_raw": processor_brand,
            "hz_advertised_friendly": _hz_short_to_friendly(hz_advertised, scale),
            "hz_actual_friendly": _hz_short_to_friendly(hz_actual, 6),
            "hz_advertised": _hz_short_to_full(hz_advertised, scale),
            "hz_actual": _hz_short_to_full(hz_actual, 6),
            "flags": flags,
        }

        info = _filter_dict_keys_with_empty_values(info)
        # g_trace.success()
        log.info(info)
        return info
    except Exception as err:
        log_fail(err)
        return {}


def _get_cpu_info_from_kstat() -> dict[str, Any]:
    """
    Returns the CPU info gathered from isainfo and kstat.
    Returns {} if isainfo or kstat are not found.
    """

    log.info("Tying to get info from kstat ...")

    try:
        # Just return {} if there is no isainfo or kstat
        if not DataSource.has_isainfo() or not DataSource.has_kstat():
            log_fail("Failed to find isinfo or kstat. Skipping ...")
            return {}

        # If isainfo fails return {}
        returncode, flag_output = DataSource.isainfo_vb()
        if flag_output is None or returncode != 0:
            log_fail('Failed to run "isainfo -vb". Skipping ...')
            return {}

        # If kstat fails return {}
        returncode, kstat = DataSource.kstat_m_cpu_info()
        if kstat is None or returncode != 0:
            log_fail('Failed to run "kstat -m cpu_info". Skipping ...')
            return {}

        # Various fields
        vendor_id = kstat.split("\tvendor_id ")[1].split("\n")[0].strip()
        processor_brand = kstat.split("\tbrand ")[1].split("\n")[0].strip()
        stepping = int(kstat.split("\tstepping ")[1].split("\n")[0].strip())
        model = int(kstat.split("\tmodel ")[1].split("\n")[0].strip())
        family = int(kstat.split("\tfamily ")[1].split("\n")[0].strip())

        # Flags
        flags = flag_output.strip().split("\n")[-1].strip().lower().split()
        flags.sort()

        # Convert from GHz/MHz string to Hz
        scale = 6
        hz_advertised = kstat.split("\tclock_MHz ")[1].split("\n")[0].strip()
        hz_advertised = _to_decimal_string(hz_advertised)

        # Convert from GHz/MHz string to Hz
        hz_actual = kstat.split("\tcurrent_clock_Hz ")[1].split("\n")[0].strip()
        hz_actual = _to_decimal_string(hz_actual)

        info = {
            "vendor_id_raw": vendor_id,
            "brand_raw": processor_brand,
            "hz_advertised_friendly": _hz_short_to_friendly(hz_advertised, scale),
            "hz_actual_friendly": _hz_short_to_friendly(hz_actual, 0),
            "hz_advertised": _hz_short_to_full(hz_advertised, scale),
            "hz_actual": _hz_short_to_full(hz_actual, 0),
            "stepping": stepping,
            "model": model,
            "family": family,
            "flags": flags,
        }

        info = _filter_dict_keys_with_empty_values(info)
        # g_trace.success()
        log.info(info)
        return info
    except Exception as err:
        log_fail(err)
        return {}


def _get_cpu_info_from_platform_uname() -> dict[str, Any]:
    log.info("Tying to get info from platform.uname ...")

    try:
        uname = DataSource.uname_string_raw.split(",")[0]

        family, model, stepping = (None, None, None)
        entries = uname.split(" ")

        if "Family" in entries and entries.index("Family") < len(entries) - 1:
            i = entries.index("Family")
            family = int(entries[i + 1])

        if "Model" in entries and entries.index("Model") < len(entries) - 1:
            i = entries.index("Model")
            model = int(entries[i + 1])

        if "Stepping" in entries and entries.index("Stepping") < len(entries) - 1:
            i = entries.index("Stepping")
            stepping = int(entries[i + 1])

        info = {"family": family, "model": model, "stepping": stepping}
        info = _filter_dict_keys_with_empty_values(info)
        # g_trace.success()
        log.info(info)
        return info
    except Exception as err:
        log_fail(err)
        return {}


def _get_cpu_info_internal() -> dict[str, Any]:
    """
    Returns the CPU info by using the best sources of information for your OS.
    Returns {} if nothing is found.
    """

    log.info("!" * 80)

    # Get the CPU arch and bits
    arch, bits = _parse_arch(DataSource.arch_string_raw)

    friendly_maxsize = {2**31 - 1: "32 bit", 2**63 - 1: "64 bit"}.get(
        sys.maxsize
    ) or "unknown bits"
    friendly_version = "{}.{}.{}.{}.{}".format(*sys.version_info)
    PYTHON_VERSION = f"{friendly_version} ({friendly_maxsize})"

    info = {
        "python_version": PYTHON_VERSION,
        "cpuinfo_version": __version__,
        "arch": arch,
        "bits": bits,
        "count": DataSource.cpu_count,
        "arch_string_raw": DataSource.arch_string_raw,
    }

    log.info("python_version: {}".format(info["python_version"]))
    log.info("cpuinfo_version: {}".format(info["cpuinfo_version"]))
    log.info("arch: {}".format(info["arch"]))
    log.info("bits: {}".format(info["bits"]))
    log.info("count: {}".format(info["count"]))
    log.info("arch_string_raw: {}".format(info["arch_string_raw"]))

    # Try the Windows wmic
    _copy_new_fields(info, _get_cpu_info_from_wmic())

    # Try the Windows registry
    _copy_new_fields(info, _get_cpu_info_from_registry())

    # Try /proc/cpuinfo
    _copy_new_fields(info, _get_cpu_info_from_proc_cpuinfo())

    # Try cpufreq-info
    _copy_new_fields(info, _get_cpu_info_from_cpufreq_info())

    # Try LSCPU
    _copy_new_fields(info, _get_cpu_info_from_lscpu())

    # Try sysctl
    _copy_new_fields(info, _get_cpu_info_from_sysctl())

    # Try kstat
    _copy_new_fields(info, _get_cpu_info_from_kstat())

    # Try dmesg
    _copy_new_fields(info, _get_cpu_info_from_dmesg())

    # Try /var/run/dmesg.boot
    _copy_new_fields(info, _get_cpu_info_from_cat_var_run_dmesg_boot())

    # Try lsprop ibm,pa-features
    _copy_new_fields(info, _get_cpu_info_from_ibm_pa_features())

    # Try sysinfo
    _copy_new_fields(info, _get_cpu_info_from_sysinfo())

    # Try querying the CPU cpuid register
    # FIXME: This should print stdout and stderr to trace log
    _copy_new_fields(info, _get_cpu_info_from_cpuid())

    # Try platform.uname
    _copy_new_fields(info, _get_cpu_info_from_platform_uname())

    log.info("!" * 80)

    return info


def get_cpu_info_json() -> str:
    """
    Returns the CPU info by using the best sources of information for your OS.
    Returns the result in a json string
    """

    import json

    output = None

    # If running under pyinstaller, run normally
    if getattr(sys, "frozen", False):
        info = _get_cpu_info_internal()
        output = json.dumps(info)
        output = f"{output}"
    # if not running under pyinstaller, run in another process.
    # This is done because multiprocesing has a design flaw that
    # causes non main programs to run multiple times on Windows.
    else:
        from subprocess import Popen, PIPE

        command = [sys.executable, __file__, "--json"]
        p1 = Popen(command, stdout=PIPE, stderr=PIPE, stdin=PIPE)
        output = p1.communicate()[0]

        if p1.returncode != 0:
            return "{}"

        output = output.decode(encoding="UTF-8")

    return output


def get_cpu_info() -> dict[str, Any]:
    """
    Returns the CPU info by using the best sources of information for your OS.
    Returns the result in a dict
    """

    import json

    output = get_cpu_info_json()

    # Convert JSON to Python with non unicode strings
    output = json.loads(output, object_hook=_utf_to_str)

    return output
