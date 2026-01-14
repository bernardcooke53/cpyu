import os
import platform
import multiprocessing

from cpyu._system import _program_paths, _run_and_get_stdout, _read_windows_registry_key
from cpyu._io import _to_decimal_string


class DataSource:
    bits = platform.architecture()[0]
    cpu_count = multiprocessing.cpu_count()
    is_windows = platform.system().lower() == "windows"
    arch_string_raw = platform.machine()
    uname_string_raw = platform.uname()[5]
    can_cpuid = True

    @staticmethod
    def has_proc_cpuinfo() -> bool:
        return os.path.exists("/proc/cpuinfo")

    @staticmethod
    def has_dmesg() -> bool:
        return len(_program_paths("dmesg")) > 0

    @staticmethod
    def has_var_run_dmesg_boot() -> bool:
        uname = platform.system().strip().strip('"').strip("'").strip().lower()
        return "linux" in uname and os.path.exists("/var/run/dmesg.boot")

    @staticmethod
    def has_cpufreq_info() -> bool:
        return len(_program_paths("cpufreq-info")) > 0

    @staticmethod
    def has_sestatus() -> bool:
        return len(_program_paths("sestatus")) > 0

    @staticmethod
    def has_sysctl() -> bool:
        return len(_program_paths("sysctl")) > 0

    @staticmethod
    def has_isainfo() -> bool:
        return len(_program_paths("isainfo")) > 0

    @staticmethod
    def has_kstat() -> bool:
        return len(_program_paths("kstat")) > 0

    @staticmethod
    def has_sysinfo() -> bool:
        uname = platform.system().strip().strip('"').strip("'").strip().lower()
        is_beos = "beos" in uname or "haiku" in uname
        return is_beos and len(_program_paths("sysinfo")) > 0

    @staticmethod
    def has_lscpu() -> bool:
        return len(_program_paths("lscpu")) > 0

    @staticmethod
    def has_ibm_pa_features() -> bool:
        return len(_program_paths("lsprop")) > 0

    @staticmethod
    def has_wmic() -> bool:
        returncode, output = _run_and_get_stdout(["wmic", "os", "get", "Version"])
        return returncode == 0 and len(output) > 0

    @staticmethod
    def cat_proc_cpuinfo() -> tuple[int, str]:
        return _run_and_get_stdout(["cat", "/proc/cpuinfo"])

    @staticmethod
    def cpufreq_info() -> tuple[int, str]:
        return _run_and_get_stdout(["cpufreq-info"])

    @staticmethod
    def sestatus_b() -> tuple[int, str]:
        return _run_and_get_stdout(["sestatus", "-b"])

    @staticmethod
    def dmesg_a() -> tuple[int, str]:
        return _run_and_get_stdout(["dmesg", "-a"])

    @staticmethod
    def cat_var_run_dmesg_boot() -> tuple[int, str]:
        return _run_and_get_stdout(["cat", "/var/run/dmesg.boot"])

    @staticmethod
    def sysctl_machdep_cpu_hw_cpufrequency() -> tuple[int, str]:
        return _run_and_get_stdout(["sysctl", "machdep.cpu", "hw.cpufrequency"])

    @staticmethod
    def isainfo_vb() -> tuple[int, str]:
        return _run_and_get_stdout(["isainfo", "-vb"])

    @staticmethod
    def kstat_m_cpu_info() -> tuple[int, str]:
        return _run_and_get_stdout(["kstat", "-m", "cpu_info"])

    @staticmethod
    def sysinfo_cpu() -> tuple[int, str]:
        return _run_and_get_stdout(["sysinfo", "-cpu"])

    @staticmethod
    def lscpu() -> tuple[int, str]:
        return _run_and_get_stdout(["lscpu"])

    @staticmethod
    def ibm_pa_features() -> tuple[int, str] | None:
        import glob

        ibm_features = glob.glob("/proc/device-tree/cpus/*/ibm,pa-features")
        if ibm_features:
            return _run_and_get_stdout(["lsprop", ibm_features[0]])

    @staticmethod
    def wmic_cpu() -> tuple[int, str]:
        return _run_and_get_stdout(
            [
                "wmic",
                "cpu",
                "get",
                "Name,CurrentClockSpeed,L2CacheSize,L3CacheSize,Description,Caption,Manufacturer",
                "/format:list",
            ]
        )

    @staticmethod
    def winreg_processor_brand() -> str:
        processor_brand = _read_windows_registry_key(
            r"Hardware\Description\System\CentralProcessor\0", "ProcessorNameString"
        )
        return processor_brand.strip()

    @staticmethod
    def winreg_vendor_id_raw() -> str:
        vendor_id_raw = _read_windows_registry_key(
            r"Hardware\Description\System\CentralProcessor\0", "VendorIdentifier"
        )
        return vendor_id_raw

    @staticmethod
    def winreg_arch_string_raw() -> str:
        arch_string_raw = _read_windows_registry_key(
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            "PROCESSOR_ARCHITECTURE",
        )
        return arch_string_raw

    @staticmethod
    def winreg_hz_actual() -> str:
        hz_actual = _read_windows_registry_key(
            r"Hardware\Description\System\CentralProcessor\0", "~Mhz"
        )
        hz_actual = _to_decimal_string(hz_actual)
        return hz_actual

    @staticmethod
    def winreg_feature_bits() -> str:
        feature_bits = _read_windows_registry_key(
            r"Hardware\Description\System\CentralProcessor\0", "FeatureSet"
        )
        return feature_bits
