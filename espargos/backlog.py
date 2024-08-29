import numpy as np
import threading
import logging
import re

from . import csi

class CSIBacklog(object):
    def __init__(self, pool, enable_ht40 = True, calibrate = True, size = 200):
        self.logger = logging.getLogger("pyespargos.backlog")

        self.pool = pool
        self.size = size
        self.enable_ht40 = enable_ht40
        self.calibrate = calibrate

        self.storage_ht40 = np.zeros((size,) + self.pool.get_shape() + ((csi.csi_buf_t.htltf_lower.size + csi.HT40_GAP_SUBCARRIERS * 2 + csi.csi_buf_t.htltf_higher.size) // 2,), dtype = np.complex64)
        self.storage_timestamps = np.zeros(size)
        self.storage_rssi = np.zeros((size,) + self.pool.get_shape(), dtype = np.float32)
        self.head = 0
        self.latest = None

        self.running = True
        self.pool.add_csi_callback(self.onNewCSI.__get__(self))
        self.callbacks = []
        self.filllevel = 0

        self.mac_filter = None

    def onNewCSI(self, clustered_csi):
        # Check MAC address if filter is installed
        if self.mac_filter is not None:
            if not self.mac_filter.match(clustered_csi.get_source_mac()):
                return

        # Store timestamp
        sensor_timestamps = clustered_csi.get_sensor_timestamps()
        if self.calibrate:
            assert(self.pool.get_calibration() is not None)
            sensor_timestamps = self.pool.get_calibration().apply_timestamps(sensor_timestamps)
        self.storage_timestamps[self.head] = np.mean(sensor_timestamps)

        # Store HT40 CSI if applicable
        if self.enable_ht40 and clustered_csi.is_ht40():
            csi_ht40 = clustered_csi.deserialize_csi_ht40()
            if self.calibrate:
                assert(self.pool.get_calibration() is not None)
                csi_ht40 = self.pool.get_calibration().apply_ht40(csi_ht40)

            self.storage_ht40[self.head] = csi_ht40

        # Store RSSI
        self.storage_rssi[self.head] = clustered_csi.get_rssi()

        # Advance ringbuffer head
        self.latest = self.head
        self.head = (self.head + 1) % self.size
        self.filllevel = min(self.filllevel + 1, self.size)

        for cb in self.callbacks:
            cb()

    def add_update_callback(self, cb):
        self.callbacks.append(cb)

    def get_ht40(self):
        assert(self.enable_ht40)
        return np.roll(self.storage_ht40, -self.head, axis = 0)[-self.filllevel:]

    def get_rssi(self):
        return np.roll(self.storage_rssi, -self.head, axis = 0)[-self.filllevel:]

    def get_timestamps(self):
        return np.roll(self.storage_timestamps, -self.head, axis = 0)[-self.filllevel:]

    def get_all(self):
        assert(self.enable_ht40)
        return self.get_ht40(), self.get_rssi(), self.get_timestamps()

    def get_latest_timestamp(self):
        if self.latest is None:
            return None

        return self.storage_timestamps[self.latest]

    def nonempty(self):
        return self.latest is not None

    def start(self):
        self.thread = threading.Thread(target=self.run)
        self.thread.start()
        self.logger.info(f"Started CSI backlog thread")

    def stop(self):
        self.running = False
        self.thread.join()

    def set_mac_filter(self, filter_regex):
        self.mac_filter = re.compile(filter_regex)

    def run(self):
        while self.running:
            self.pool.run()
