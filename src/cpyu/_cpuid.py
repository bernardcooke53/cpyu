import ctypes

from cpyu._asm import ASM
from cpyu._datasource import DataSource
from cpyu._io import _is_bit_set
from cpyu._system import _is_selinux_enforcing


class CPUID:
    def __init__(self):
        # Figure out if SE Linux is on and in enforcing mode
        self.is_selinux_enforcing = _is_selinux_enforcing()

    def _asm_func(self, restype=None, argtypes=(), machine_code=[]):
        asm = ASM(restype, argtypes, machine_code)
        asm.compile()
        return asm

    def _run_asm(self, *machine_code):
        asm = ASM(ctypes.c_uint32, (), machine_code)
        asm.compile()
        retval = asm.run()
        asm.free()
        return retval

    # http://en.wikipedia.org/wiki/CPUID#EAX.3D0:_Get_vendor_ID
    def get_vendor_id(self):
        # EBX
        ebx = self._run_asm(
            b"\x31\xc0",  # xor eax,eax
            b"\x0f\xa2"  # cpuid
            b"\x89\xd8"  # mov ax,bx
            b"\xc3",  # ret
        )

        # ECX
        ecx = self._run_asm(
            b"\x31\xc0",  # xor eax,eax
            b"\x0f\xa2"  # cpuid
            b"\x89\xc8"  # mov ax,cx
            b"\xc3",  # ret
        )

        # EDX
        edx = self._run_asm(
            b"\x31\xc0",  # xor eax,eax
            b"\x0f\xa2"  # cpuid
            b"\x89\xd0"  # mov ax,dx
            b"\xc3",  # ret
        )

        # Each 4bits is a ascii letter in the name
        vendor_id = []
        for reg in [ebx, edx, ecx]:
            for n in [0, 8, 16, 24]:
                vendor_id.append(chr((reg >> n) & 0xFF))
        vendor_id = "".join(vendor_id)

        return vendor_id

    # http://en.wikipedia.org/wiki/CPUID#EAX.3D1:_Processor_Info_and_Feature_Bits
    def get_info(self):
        # EAX
        eax = self._run_asm(
            b"\xb8\x01\x00\x00\x00",  # mov eax,0x1"
            b"\x0f\xa2"  # cpuid
            b"\xc3",  # ret
        )

        # Get the CPU info
        stepping_id = (eax >> 0) & 0xF  # 4 bits
        model = (eax >> 4) & 0xF  # 4 bits
        family_id = (eax >> 8) & 0xF  # 4 bits
        processor_type = (eax >> 12) & 0x3  # 2 bits
        extended_model_id = (eax >> 16) & 0xF  # 4 bits
        extended_family_id = (eax >> 20) & 0xFF  # 8 bits
        family = 0

        if family_id in [15]:
            family = extended_family_id + family_id
        else:
            family = family_id

        if family_id in [6, 15]:
            model = (extended_model_id << 4) + model

        return {
            "stepping": stepping_id,
            "model": model,
            "family": family,
            "processor_type": processor_type,
        }

    # http://en.wikipedia.org/wiki/CPUID#EAX.3D80000000h:_Get_Highest_Extended_Function_Supported
    def get_max_extension_support(self):
        # Check for extension support
        max_extension_support = self._run_asm(
            b"\xb8\x00\x00\x00\x80"  # mov ax,0x80000000
            b"\x0f\xa2"  # cpuid
            b"\xc3"  # ret
        )

        return max_extension_support

    # http://en.wikipedia.org/wiki/CPUID#EAX.3D1:_Processor_Info_and_Feature_Bits
    def get_flags(self, max_extension_support):
        # EDX
        edx = self._run_asm(
            b"\xb8\x01\x00\x00\x00",  # mov eax,0x1"
            b"\x0f\xa2"  # cpuid
            b"\x89\xd0"  # mov ax,dx
            b"\xc3",  # ret
        )

        # ECX
        ecx = self._run_asm(
            b"\xb8\x01\x00\x00\x00",  # mov eax,0x1"
            b"\x0f\xa2"  # cpuid
            b"\x89\xc8"  # mov ax,cx
            b"\xc3",  # ret
        )

        # Get the CPU flags
        flags = {
            "fpu": _is_bit_set(edx, 0),
            "vme": _is_bit_set(edx, 1),
            "de": _is_bit_set(edx, 2),
            "pse": _is_bit_set(edx, 3),
            "tsc": _is_bit_set(edx, 4),
            "msr": _is_bit_set(edx, 5),
            "pae": _is_bit_set(edx, 6),
            "mce": _is_bit_set(edx, 7),
            "cx8": _is_bit_set(edx, 8),
            "apic": _is_bit_set(edx, 9),
            #'reserved1' : _is_bit_set(edx, 10),
            "sep": _is_bit_set(edx, 11),
            "mtrr": _is_bit_set(edx, 12),
            "pge": _is_bit_set(edx, 13),
            "mca": _is_bit_set(edx, 14),
            "cmov": _is_bit_set(edx, 15),
            "pat": _is_bit_set(edx, 16),
            "pse36": _is_bit_set(edx, 17),
            "pn": _is_bit_set(edx, 18),
            "clflush": _is_bit_set(edx, 19),
            #'reserved2' : _is_bit_set(edx, 20),
            "dts": _is_bit_set(edx, 21),
            "acpi": _is_bit_set(edx, 22),
            "mmx": _is_bit_set(edx, 23),
            "fxsr": _is_bit_set(edx, 24),
            "sse": _is_bit_set(edx, 25),
            "sse2": _is_bit_set(edx, 26),
            "ss": _is_bit_set(edx, 27),
            "ht": _is_bit_set(edx, 28),
            "tm": _is_bit_set(edx, 29),
            "ia64": _is_bit_set(edx, 30),
            "pbe": _is_bit_set(edx, 31),
            "pni": _is_bit_set(ecx, 0),
            "pclmulqdq": _is_bit_set(ecx, 1),
            "dtes64": _is_bit_set(ecx, 2),
            "monitor": _is_bit_set(ecx, 3),
            "ds_cpl": _is_bit_set(ecx, 4),
            "vmx": _is_bit_set(ecx, 5),
            "smx": _is_bit_set(ecx, 6),
            "est": _is_bit_set(ecx, 7),
            "tm2": _is_bit_set(ecx, 8),
            "ssse3": _is_bit_set(ecx, 9),
            "cid": _is_bit_set(ecx, 10),
            #'reserved3' : _is_bit_set(ecx, 11),
            "fma": _is_bit_set(ecx, 12),
            "cx16": _is_bit_set(ecx, 13),
            "xtpr": _is_bit_set(ecx, 14),
            "pdcm": _is_bit_set(ecx, 15),
            #'reserved4' : _is_bit_set(ecx, 16),
            "pcid": _is_bit_set(ecx, 17),
            "dca": _is_bit_set(ecx, 18),
            "sse4_1": _is_bit_set(ecx, 19),
            "sse4_2": _is_bit_set(ecx, 20),
            "x2apic": _is_bit_set(ecx, 21),
            "movbe": _is_bit_set(ecx, 22),
            "popcnt": _is_bit_set(ecx, 23),
            "tscdeadline": _is_bit_set(ecx, 24),
            "aes": _is_bit_set(ecx, 25),
            "xsave": _is_bit_set(ecx, 26),
            "osxsave": _is_bit_set(ecx, 27),
            "avx": _is_bit_set(ecx, 28),
            "f16c": _is_bit_set(ecx, 29),
            "rdrnd": _is_bit_set(ecx, 30),
            "hypervisor": _is_bit_set(ecx, 31),
        }

        # Get a list of only the flags that are true
        flags = [k for k, v in flags.items() if v]

        # http://en.wikipedia.org/wiki/CPUID#EAX.3D7.2C_ECX.3D0:_Extended_Features
        if max_extension_support >= 7:
            # EBX
            ebx = self._run_asm(
                b"\x31\xc9",  # xor ecx,ecx
                b"\xb8\x07\x00\x00\x00"  # mov eax,7
                b"\x0f\xa2"  # cpuid
                b"\x89\xd8"  # mov ax,bx
                b"\xc3",  # ret
            )

            # ECX
            ecx = self._run_asm(
                b"\x31\xc9",  # xor ecx,ecx
                b"\xb8\x07\x00\x00\x00"  # mov eax,7
                b"\x0f\xa2"  # cpuid
                b"\x89\xc8"  # mov ax,cx
                b"\xc3",  # ret
            )

            # Get the extended CPU flags
            extended_flags = {
                #'fsgsbase' : _is_bit_set(ebx, 0),
                #'IA32_TSC_ADJUST' : _is_bit_set(ebx, 1),
                "sgx": _is_bit_set(ebx, 2),
                "bmi1": _is_bit_set(ebx, 3),
                "hle": _is_bit_set(ebx, 4),
                "avx2": _is_bit_set(ebx, 5),
                #'reserved' : _is_bit_set(ebx, 6),
                "smep": _is_bit_set(ebx, 7),
                "bmi2": _is_bit_set(ebx, 8),
                "erms": _is_bit_set(ebx, 9),
                "invpcid": _is_bit_set(ebx, 10),
                "rtm": _is_bit_set(ebx, 11),
                "pqm": _is_bit_set(ebx, 12),
                #'FPU CS and FPU DS deprecated' : _is_bit_set(ebx, 13),
                "mpx": _is_bit_set(ebx, 14),
                "pqe": _is_bit_set(ebx, 15),
                "avx512f": _is_bit_set(ebx, 16),
                "avx512dq": _is_bit_set(ebx, 17),
                "rdseed": _is_bit_set(ebx, 18),
                "adx": _is_bit_set(ebx, 19),
                "smap": _is_bit_set(ebx, 20),
                "avx512ifma": _is_bit_set(ebx, 21),
                "pcommit": _is_bit_set(ebx, 22),
                "clflushopt": _is_bit_set(ebx, 23),
                "clwb": _is_bit_set(ebx, 24),
                "intel_pt": _is_bit_set(ebx, 25),
                "avx512pf": _is_bit_set(ebx, 26),
                "avx512er": _is_bit_set(ebx, 27),
                "avx512cd": _is_bit_set(ebx, 28),
                "sha": _is_bit_set(ebx, 29),
                "avx512bw": _is_bit_set(ebx, 30),
                "avx512vl": _is_bit_set(ebx, 31),
                "prefetchwt1": _is_bit_set(ecx, 0),
                "avx512vbmi": _is_bit_set(ecx, 1),
                "umip": _is_bit_set(ecx, 2),
                "pku": _is_bit_set(ecx, 3),
                "ospke": _is_bit_set(ecx, 4),
                #'reserved' : _is_bit_set(ecx, 5),
                "avx512vbmi2": _is_bit_set(ecx, 6),
                #'reserved' : _is_bit_set(ecx, 7),
                "gfni": _is_bit_set(ecx, 8),
                "vaes": _is_bit_set(ecx, 9),
                "vpclmulqdq": _is_bit_set(ecx, 10),
                "avx512vnni": _is_bit_set(ecx, 11),
                "avx512bitalg": _is_bit_set(ecx, 12),
                #'reserved' : _is_bit_set(ecx, 13),
                "avx512vpopcntdq": _is_bit_set(ecx, 14),
                #'reserved' : _is_bit_set(ecx, 15),
                #'reserved' : _is_bit_set(ecx, 16),
                #'mpx0' : _is_bit_set(ecx, 17),
                #'mpx1' : _is_bit_set(ecx, 18),
                #'mpx2' : _is_bit_set(ecx, 19),
                #'mpx3' : _is_bit_set(ecx, 20),
                #'mpx4' : _is_bit_set(ecx, 21),
                "rdpid": _is_bit_set(ecx, 22),
                #'reserved' : _is_bit_set(ecx, 23),
                #'reserved' : _is_bit_set(ecx, 24),
                #'reserved' : _is_bit_set(ecx, 25),
                #'reserved' : _is_bit_set(ecx, 26),
                #'reserved' : _is_bit_set(ecx, 27),
                #'reserved' : _is_bit_set(ecx, 28),
                #'reserved' : _is_bit_set(ecx, 29),
                "sgx_lc": _is_bit_set(ecx, 30),
                #'reserved' : _is_bit_set(ecx, 31)
            }

            # Get a list of only the flags that are true
            extended_flags = [k for k, v in extended_flags.items() if v]
            flags += extended_flags

        # http://en.wikipedia.org/wiki/CPUID#EAX.3D80000001h:_Extended_Processor_Info_and_Feature_Bits
        if max_extension_support >= 0x80000001:
            # EBX
            ebx = self._run_asm(
                b"\xb8\x01\x00\x00\x80"  # mov ax,0x80000001
                b"\x0f\xa2"  # cpuid
                b"\x89\xd8"  # mov ax,bx
                b"\xc3"  # ret
            )

            # ECX
            ecx = self._run_asm(
                b"\xb8\x01\x00\x00\x80"  # mov ax,0x80000001
                b"\x0f\xa2"  # cpuid
                b"\x89\xc8"  # mov ax,cx
                b"\xc3"  # ret
            )

            # Get the extended CPU flags
            extended_flags = {
                "fpu": _is_bit_set(ebx, 0),
                "vme": _is_bit_set(ebx, 1),
                "de": _is_bit_set(ebx, 2),
                "pse": _is_bit_set(ebx, 3),
                "tsc": _is_bit_set(ebx, 4),
                "msr": _is_bit_set(ebx, 5),
                "pae": _is_bit_set(ebx, 6),
                "mce": _is_bit_set(ebx, 7),
                "cx8": _is_bit_set(ebx, 8),
                "apic": _is_bit_set(ebx, 9),
                #'reserved' : _is_bit_set(ebx, 10),
                "syscall": _is_bit_set(ebx, 11),
                "mtrr": _is_bit_set(ebx, 12),
                "pge": _is_bit_set(ebx, 13),
                "mca": _is_bit_set(ebx, 14),
                "cmov": _is_bit_set(ebx, 15),
                "pat": _is_bit_set(ebx, 16),
                "pse36": _is_bit_set(ebx, 17),
                #'reserved' : _is_bit_set(ebx, 18),
                "mp": _is_bit_set(ebx, 19),
                "nx": _is_bit_set(ebx, 20),
                #'reserved' : _is_bit_set(ebx, 21),
                "mmxext": _is_bit_set(ebx, 22),
                "mmx": _is_bit_set(ebx, 23),
                "fxsr": _is_bit_set(ebx, 24),
                "fxsr_opt": _is_bit_set(ebx, 25),
                "pdpe1gp": _is_bit_set(ebx, 26),
                "rdtscp": _is_bit_set(ebx, 27),
                #'reserved' : _is_bit_set(ebx, 28),
                "lm": _is_bit_set(ebx, 29),
                "3dnowext": _is_bit_set(ebx, 30),
                "3dnow": _is_bit_set(ebx, 31),
                "lahf_lm": _is_bit_set(ecx, 0),
                "cmp_legacy": _is_bit_set(ecx, 1),
                "svm": _is_bit_set(ecx, 2),
                "extapic": _is_bit_set(ecx, 3),
                "cr8_legacy": _is_bit_set(ecx, 4),
                "abm": _is_bit_set(ecx, 5),
                "sse4a": _is_bit_set(ecx, 6),
                "misalignsse": _is_bit_set(ecx, 7),
                "3dnowprefetch": _is_bit_set(ecx, 8),
                "osvw": _is_bit_set(ecx, 9),
                "ibs": _is_bit_set(ecx, 10),
                "xop": _is_bit_set(ecx, 11),
                "skinit": _is_bit_set(ecx, 12),
                "wdt": _is_bit_set(ecx, 13),
                #'reserved' : _is_bit_set(ecx, 14),
                "lwp": _is_bit_set(ecx, 15),
                "fma4": _is_bit_set(ecx, 16),
                "tce": _is_bit_set(ecx, 17),
                #'reserved' : _is_bit_set(ecx, 18),
                "nodeid_msr": _is_bit_set(ecx, 19),
                #'reserved' : _is_bit_set(ecx, 20),
                "tbm": _is_bit_set(ecx, 21),
                "topoext": _is_bit_set(ecx, 22),
                "perfctr_core": _is_bit_set(ecx, 23),
                "perfctr_nb": _is_bit_set(ecx, 24),
                #'reserved' : _is_bit_set(ecx, 25),
                "dbx": _is_bit_set(ecx, 26),
                "perftsc": _is_bit_set(ecx, 27),
                "pci_l2i": _is_bit_set(ecx, 28),
                #'reserved' : _is_bit_set(ecx, 29),
                #'reserved' : _is_bit_set(ecx, 30),
                #'reserved' : _is_bit_set(ecx, 31)
            }

            # Get a list of only the flags that are true
            extended_flags = [k for k, v in extended_flags.items() if v]
            flags += extended_flags

        flags.sort()
        return flags

    # http://en.wikipedia.org/wiki/CPUID#EAX.3D80000002h.2C80000003h.2C80000004h:_Processor_Brand_String
    def get_processor_brand(self, max_extension_support):
        processor_brand = ""

        # Processor brand string
        if max_extension_support >= 0x80000004:
            instructions = [
                b"\xb8\x02\x00\x00\x80",  # mov ax,0x80000002
                b"\xb8\x03\x00\x00\x80",  # mov ax,0x80000003
                b"\xb8\x04\x00\x00\x80",  # mov ax,0x80000004
            ]
            for instruction in instructions:
                # EAX
                eax = self._run_asm(
                    instruction,  # mov ax,0x8000000?
                    b"\x0f\xa2"  # cpuid
                    b"\x89\xc0"  # mov ax,ax
                    b"\xc3",  # ret
                )

                # EBX
                ebx = self._run_asm(
                    instruction,  # mov ax,0x8000000?
                    b"\x0f\xa2"  # cpuid
                    b"\x89\xd8"  # mov ax,bx
                    b"\xc3",  # ret
                )

                # ECX
                ecx = self._run_asm(
                    instruction,  # mov ax,0x8000000?
                    b"\x0f\xa2"  # cpuid
                    b"\x89\xc8"  # mov ax,cx
                    b"\xc3",  # ret
                )

                # EDX
                edx = self._run_asm(
                    instruction,  # mov ax,0x8000000?
                    b"\x0f\xa2"  # cpuid
                    b"\x89\xd0"  # mov ax,dx
                    b"\xc3",  # ret
                )

                # Combine each of the 4 bytes in each register into the string
                for reg in [eax, ebx, ecx, edx]:
                    for n in [0, 8, 16, 24]:
                        processor_brand += chr((reg >> n) & 0xFF)

        # Strip off any trailing NULL terminators and white space
        processor_brand = processor_brand.strip("\0").strip()

        return processor_brand

    # http://en.wikipedia.org/wiki/CPUID#EAX.3D80000006h:_Extended_L2_Cache_Features
    def get_cache(self, max_extension_support):
        cache_info = {}

        # Just return if the cache feature is not supported
        if max_extension_support < 0x80000006:
            return cache_info

        # ECX
        ecx = self._run_asm(
            b"\xb8\x06\x00\x00\x80"  # mov ax,0x80000006
            b"\x0f\xa2"  # cpuid
            b"\x89\xc8"  # mov ax,cx
            b"\xc3"  # ret
        )

        cache_info = {
            "size_b": (ecx & 0xFF) * 1024,
            "associativity": (ecx >> 12) & 0xF,
            "line_size_b": (ecx >> 16) & 0xFFFF,
        }

        return cache_info

    def get_ticks_func(self):
        retval = None

        if DataSource.bits == "32bit":
            # Works on x86_32
            restype = None
            argtypes = (ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint))
            get_ticks_x86_32 = self._asm_func(
                restype,
                argtypes,
                [
                    b"\x55",  # push bp
                    b"\x89\xe5",  # mov bp,sp
                    b"\x31\xc0",  # xor ax,ax
                    b"\x0f\xa2",  # cpuid
                    b"\x0f\x31",  # rdtsc
                    b"\x8b\x5d\x08",  # mov bx,[di+0x8]
                    b"\x8b\x4d\x0c",  # mov cx,[di+0xc]
                    b"\x89\x13",  # mov [bp+di],dx
                    b"\x89\x01",  # mov [bx+di],ax
                    b"\x5d",  # pop bp
                    b"\xc3",  # ret
                ],
            )

            # Monkey patch func to combine high and low args into one return
            old_func = get_ticks_x86_32.func

            def new_func():
                # Pass two uint32s into function
                high = ctypes.c_uint32(0)
                low = ctypes.c_uint32(0)
                old_func(ctypes.byref(high), ctypes.byref(low))

                # Shift the two uint32s into one uint64
                retval = ((high.value << 32) & 0xFFFFFFFF00000000) | low.value
                return retval

            get_ticks_x86_32.func = new_func

            retval = get_ticks_x86_32
        elif DataSource.bits == "64bit":
            # Works on x86_64
            restype = ctypes.c_uint64
            argtypes = ()
            get_ticks_x86_64 = self._asm_func(
                restype,
                argtypes,
                [
                    b"\x48",  # dec ax
                    b"\x31\xc0",  # xor ax,ax
                    b"\x0f\xa2",  # cpuid
                    b"\x0f\x31",  # rdtsc
                    b"\x48",  # dec ax
                    b"\xc1\xe2\x20",  # shl dx,byte 0x20
                    b"\x48",  # dec ax
                    b"\x09\xd0",  # or ax,dx
                    b"\xc3",  # ret
                ],
            )

            retval = get_ticks_x86_64
        return retval

    def get_raw_hz(self):
        from time import sleep

        ticks_fn = self.get_ticks_func()

        start = ticks_fn.func()
        sleep(1)
        end = ticks_fn.func()

        ticks = end - start
        ticks_fn.free()

        return ticks
