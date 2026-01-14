import logging
from io import StringIO


log = logging.getLogger(__name__)


class Trace:
    def __init__(self, is_active: bool, is_stored_in_string: bool) -> None:
        self._is_active = is_active
        if not self._is_active:
            return

        from datetime import datetime
        from io import StringIO

        if is_stored_in_string:
            self._output = StringIO()
        else:
            date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
            self._output = open(f"cpuinfo_trace_{date}.trace", "w")

        self._stdout = StringIO()
        self._stderr = StringIO()
        self._err = None

    def header(self, msg):
        if not self._is_active:
            return

        from inspect import stack

        frame = stack()[1]
        file = frame[1]
        line = frame[2]
        self._output.write(f"{msg} ({file} {line})\n")
        self._output.flush()

    def success(self):
        if not self._is_active:
            return

        from inspect import stack

        frame = stack()[1]
        file = frame[1]
        line = frame[2]

        self._output.write(f"Success ... ({file} {line})\n\n")
        self._output.flush()

    def fail(self, msg):
        if not self._is_active:
            return

        from inspect import stack

        frame = stack()[1]
        file = frame[1]
        line = frame[2]

        if isinstance(msg, str):
            msg = "".join(["\t" + line for line in msg.split("\n")]) + "\n"

            self._output.write(msg)
            self._output.write(f"Failed ... ({file} {line})\n\n")
            self._output.flush()
        elif isinstance(msg, Exception):
            from traceback import format_exc

            err_string = format_exc()
            self._output.write(f"\tFailed ... ({file} {line})\n")
            self._output.write(
                "".join([f"\t\t{n}\n" for n in err_string.split("\n")]) + "\n"
            )
            self._output.flush()

    def command_header(self, msg):
        if not self._is_active:
            return

        from inspect import stack

        frame = stack()[3]
        file = frame[1]
        line = frame[2]
        self._output.write(f"\t{msg} ({file} {line})\n")
        self._output.flush()

    def command_output(self, msg, output):
        if not self._is_active:
            return

        self._output.write(f"\t\t{msg}\n")
        self._output.write("".join([f"\t\t\t{n}\n" for n in output.split("\n")]) + "\n")
        self._output.flush()

    def keys(self, keys, info, new_info):
        if not self._is_active:
            return

        from inspect import stack

        frame = stack()[2]
        file = frame[1]
        line = frame[2]

        # List updated keys
        self._output.write(f"\tChanged keys ({file} {line})\n")
        changed_keys = [
            key
            for key in keys
            if key in info and key in new_info and info[key] != new_info[key]
        ]
        if changed_keys:
            for key in changed_keys:
                self._output.write(f"\t\t{key}: {info[key]} to {new_info[key]}\n")
        else:
            self._output.write("\t\tNone\n")

        # List new keys
        self._output.write(f"\tNew keys ({file} {line})\n")
        new_keys = [key for key in keys if key in new_info and key not in info]
        if new_keys:
            for key in new_keys:
                self._output.write(f"\t\t{key}: {new_info[key]}\n")
        else:
            self._output.write("\t\tNone\n")

        self._output.write("\n")
        self._output.flush()

    def write(self, msg):
        if not self._is_active:
            return

        self._output.write(msg + "\n")
        self._output.flush()

    def to_dict(self, info, is_fail):
        return {
            "output": self._output.getvalue(),
            "stdout": self._stdout.getvalue(),
            "stderr": self._stderr.getvalue(),
            "info": info,
            "err": self._err,
            "is_fail": is_fail,
        }


def log_fail(msg: str | Exception) -> None:
    from inspect import stack

    frame = stack()[1]
    file = frame[1]
    line = frame[2]
    _output = StringIO()

    if isinstance(msg, str):
        msg = "".join(["\t" + line for line in msg.split("\n")]) + "\n"

        _output.write(msg)
        _output.write(f"Failed ... ({file} {line})\n\n")
        _output.flush()
    elif isinstance(msg, Exception):
        from traceback import format_exc

        err_string = format_exc()
        _output.write(f"\tFailed ... ({file} {line})\n")
        _output.write("".join([f"\t\t{n}\n" for n in err_string.split("\n")]) + "\n")
    log.error(_output.getvalue())
