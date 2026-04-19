from bot import LOGGER  # এই লাইন ঠিক করেছি
from ...ext_utils.status_utils import (
    get_readable_file_size,
    EngineStatus,
    MirrorStatus,
    get_readable_time,
)

class FfmpegStatus:
    def __init__(self, listener, obj, gid, status=""):
        self.listener = listener
        self._obj = obj
        self._gid = gid
        self._cstatus = status
        self.engine = EngineStatus().STATUS_FFMPEG

    def speed(self):
        return f"{get_readable_file_size(self._obj.speed_raw)}/s"

    def processed_bytes(self):
        return get_readable_file_size(self._obj.processed_bytes)

    def progress(self):
        return f"{round(self._obj.progress_raw, 2)}%"

    def gid(self):
        return self._gid

    def name(self):
        return self.listener.name

    def size(self):
        return get_readable_file_size(self.listener.size)

    def eta(self):
        return get_readable_time(self._obj.eta_raw) if self._obj.eta_raw else "-"

    def status(self):
        return self._cstatus

    def task(self):
        return self

    async def cancel_task(self):
        LOGGER.info(f"Cancelling {self._cstatus}: {self.listener.name}")
        self.listener.is_cancelled = True
        if self.listener._subprocess and self.listener._subprocess.returncode is None:
            try:
                self.listener._subprocess.kill()
            except Exception:
                pass
        await self.listener.on_upload_error(f"{self._cstatus} stopped by user!")

__all__ = ['FfmpegStatus']
