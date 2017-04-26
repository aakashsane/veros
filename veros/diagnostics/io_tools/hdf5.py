import threading
import contextlib
import logging
import h5py


@contextlib.contextmanager
def threaded_io(veros, filepath, mode):
    """
    If using IO threads, start a new thread to write the netCDF data to disk.
    """
    if veros.use_io_threads:
        _wait_for_disk(veros, filepath)
        _io_locks[filepath].clear()
    h5file = h5py.File(filepath, mode)
    try:
        yield h5file
    finally:
        if veros.use_io_threads:
            io_thread = threading.Thread(target=_write_to_disk, args=(veros, h5file, filepath))
            io_thread.start()
        else:
            _write_to_disk(veros, h5file, filepath)


_io_locks = {}


def _add_to_locks(file_id):
    """
    If there is no lock for file_id, create one
    """
    if file_id not in _io_locks:
        _io_locks[file_id] = threading.Event()
        _io_locks[file_id].set()


def _wait_for_disk(veros, file_id):
    """
    Wait for the lock of file_id to be released
    """
    logging.debug("Waiting for lock {} to be released".format(file_id))
    _add_to_locks(file_id)
    lock_released = _io_locks[file_id].wait(veros.io_timeout)
    if not lock_released:
        raise RuntimeError("Timeout while waiting for disk IO to finish")


def _write_to_disk(veros, h5file, file_id):
    """
    Sync HDF5 data to disk, close file handle, and release lock.
    May run in a separate thread.
    """
    h5file.flush()
    h5file.close()
    if veros.use_io_threads and file_id is not None:
        _io_locks[file_id].set()
