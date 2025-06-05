""" 
File:       CameraThread.py
Purpose:    A thread for capturing frames from a camera using OpenCV.
Created by: JYJ
Created on: 2025-05-30
"""

from PyQt5.QtCore import QThread, pyqtSignal
import cv2
import time
import platform
from typing import Optional

class CameraThread(QThread):
    frameReady = pyqtSignal(int, object)
    def __init__(self, cam_id, width = None, height = None, fps = None):
        super().__init__()
        self.cam_id = cam_id
        self._running = True

        backend = cv2.CAP_DSHOW if platform.system() == 'Windows' else cv2.CAP_V4L2
        self.cap = cv2.VideoCapture(cam_id, backend)
        if not self.cap.isOpened():
            raise RuntimeError(f"[!] Cannot open camera {cam_id}")
        if width:  self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
        if height: self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if fps:    self.cap.set(cv2.CAP_PROP_FPS,          fps)
        # actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
        # print(f"[i] Camera {cam_id} opened with {actual_fps} FPS")
        self.recorder: Optional["MultiCamRecorder"] = None
        
    def set_recorder(self, rec = Optional["MultiCamRecorder"]):
        """레코더 인스턴스를 설정합니다."""
        self.recorder = rec

    def run(self):
        while self._running:
            ok, frame = self.cap.read()
            if not ok:
                print(f"[!] Failed to read frame from camera {self.cam_id}")
                break

            if self.recorder is not None:
                self.recorder.enqueue(self.cam_id, frame)
            self.frameReady.emit(self.cam_id, frame)
            time.sleep(0.001)

        self.cap.release()

    def stop(self):
        self._running = False
        self.wait()
