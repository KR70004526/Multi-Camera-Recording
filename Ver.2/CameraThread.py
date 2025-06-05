# CameraThread.py
# 멀티카메라 동기화를 위한 Grab/Barrier 기반 CameraThread 구현

import cv2
import threading
import platform
import numpy as np

class CameraThread(threading.Thread):
    """
    각 카메라별로 grab()만 수행한 뒤, Barrier를 통해 메인 스레드가 retrieve()를 수행하도록 동기화합니다.
    """
    def __init__(self, cam_id, barrier, stop_event, width=640, height=480, fps=30):
        super().__init__(daemon=True)
        self.cam_id = cam_id
        self.barrier = barrier
        self.stop_event = stop_event

        # 플랫폼에 맞는 백엔드 사용 (Windows: CAP_DSHOW, Linux/macOS: CAP_V4L2 or CAP_AVFOUNDATION)
        backend = cv2.CAP_DSHOW if platform.system() == 'Windows' else cv2.CAP_V4L2
        self.cap = cv2.VideoCapture(self.cam_id, backend)
        if not self.cap.isOpened():
            raise RuntimeError(f"[CameraThread] Cannot open camera {self.cam_id}")

        # 해상도, FPS 설정
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)

    def run(self):
        # 루프: grab → barrier.wait() → barrier.wait() → 다음 grab
        while not self.stop_event.is_set():
            # 1) grab(): USB 전송 요청만 발생, 픽셀 데이터는 아직 가져오지 않음
            self.cap.grab()

            # 2) 모든 카메라 grab() 완료 대기
            try:
                self.barrier.wait()
            except threading.BrokenBarrierError:
                break

            # 3) 메인 스레드가 retrieve()를 마칠 때까지 대기
            try:
                self.barrier.wait()
            except threading.BrokenBarrierError:
                break

        # stop_event가 set되면 루프 종료 후 VideoCapture를 해제
        self.cap.release()

    def retrieve_frame(self):
        """
        메인 스레드에서 호출: grab() 이후에 실제 픽셀 데이터를 가져옵니다.
        실패 시 검정 프레임을 반환합니다.
        """
        ret, frame = self.cap.retrieve()
        if not ret:
            h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            return np.zeros((h, w, 3), dtype=np.uint8)
        return frame
