import ctypes

from cpyu._datasource import DataSource


class ASM:
    def __init__(self, restype=None, argtypes=(), machine_code=[]):
        self.restype = restype
        self.argtypes = argtypes
        self.machine_code = machine_code
        self.prochandle = None
        self.mm = None
        self.func = None
        self.address = None
        self.size = 0

    def compile(self):
        machine_code = bytes.join(b"", self.machine_code)
        self.size = ctypes.c_size_t(len(machine_code))

        if DataSource.is_windows:
            # Allocate a memory segment the size of the machine code, and make it executable
            size = len(machine_code)
            # Alloc at least 1 page to ensure we own all pages that we want to change protection on
            if size < 0x1000:
                size = 0x1000
            MEM_COMMIT = ctypes.c_ulong(0x1000)
            PAGE_READWRITE = ctypes.c_ulong(0x4)
            pfnVirtualAlloc = ctypes.windll.kernel32.VirtualAlloc
            pfnVirtualAlloc.restype = ctypes.c_void_p
            self.address = pfnVirtualAlloc(
                None, ctypes.c_size_t(size), MEM_COMMIT, PAGE_READWRITE
            )
            if not self.address:
                raise Exception("Failed to VirtualAlloc")

            # Copy the machine code into the memory segment
            memmove = ctypes.CFUNCTYPE(
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t
            )(ctypes._memmove_addr)
            if memmove(self.address, machine_code, size) < 0:
                raise Exception("Failed to memmove")

            # Enable execute permissions
            PAGE_EXECUTE = ctypes.c_ulong(0x10)
            old_protect = ctypes.c_ulong(0)
            pfnVirtualProtect = ctypes.windll.kernel32.VirtualProtect
            res = pfnVirtualProtect(
                ctypes.c_void_p(self.address),
                ctypes.c_size_t(size),
                PAGE_EXECUTE,
                ctypes.byref(old_protect),
            )
            if not res:
                raise Exception("Failed VirtualProtect")

            # Flush Instruction Cache
            # First, get process Handle
            if not self.prochandle:
                pfnGetCurrentProcess = ctypes.windll.kernel32.GetCurrentProcess
                pfnGetCurrentProcess.restype = ctypes.c_void_p
                self.prochandle = ctypes.c_void_p(pfnGetCurrentProcess())
            # Actually flush cache
            res = ctypes.windll.kernel32.FlushInstructionCache(
                self.prochandle, ctypes.c_void_p(self.address), ctypes.c_size_t(size)
            )
            if not res:
                raise Exception("Failed FlushInstructionCache")
        else:
            from mmap import (
                mmap,
                MAP_PRIVATE,
                MAP_ANONYMOUS,
                PROT_WRITE,
                PROT_READ,
                PROT_EXEC,
            )

            # Allocate a private and executable memory segment the size of the machine code
            machine_code = bytes.join(b"", self.machine_code)
            self.size = len(machine_code)
            self.mm = mmap(
                -1,
                self.size,
                flags=MAP_PRIVATE | MAP_ANONYMOUS,
                prot=PROT_WRITE | PROT_READ | PROT_EXEC,
            )

            # Copy the machine code into the memory segment
            self.mm.write(machine_code)
            self.address = ctypes.addressof(ctypes.c_int.from_buffer(self.mm))

        # Cast the memory segment into a function
        functype = ctypes.CFUNCTYPE(self.restype, *self.argtypes)
        self.func = functype(self.address)

    def run(self):
        # Call the machine code like a function
        retval = self.func()

        return retval

    def free(self):
        # Free the function memory segment
        if DataSource.is_windows:
            MEM_RELEASE = ctypes.c_ulong(0x8000)
            ctypes.windll.kernel32.VirtualFree(
                ctypes.c_void_p(self.address), ctypes.c_size_t(0), MEM_RELEASE
            )
        else:
            self.mm.close()

        self.prochandle = None
        self.mm = None
        self.func = None
        self.address = None
        self.size = 0
