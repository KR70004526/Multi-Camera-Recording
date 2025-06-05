# Run_GUI.py
# "grab → barrier → retrieve" 기반 동기화로 수정된 메인 GUI 실행 파일

import sys
import cv2
import threading
import time
import numpy as np
from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from typing import Optional

from Multi_Camera_GUI import Ui_MainWindow  # GUI Designer로 생성된 파일
from CameraThread import CameraThread      # 동기화된 Grab/Barrier 기반 CameraThread
from MultiCamRecorder import MultiCamRecorder  # 녹화 동기화·저장 담당


class SyncThread(QThread):
    """
    GrabThread들과 Barrier를 이용해 각 루프마다 grab()을 동기화하고,
    retrieve()로부터 가져온 모든 카메라 프레임을 메인 윈도우로 전달합니다.
    """
    framesReady = pyqtSignal(list)

    def __init__(self, cam_threads, barrier, stop_event, parent=None):
        super().__init__(parent)
        self.cam_threads = cam_threads
        self.barrier = barrier
        self.stop_event = stop_event

    def run(self):
        # 메인 루프: grab() 동기화 → retrieve() → 시그널 emit → barrier 해제
        while not self.stop_event.is_set():
            try:
                # 1) 모든 GrabThread가 grab() 완료할 때까지 대기
                self.barrier.wait()
            except threading.BrokenBarrierError:
                break

            # 2) retrieve 단계: 각 카메라별 Frame 수집
            frames = []  # (cam_id, frame) 튜플 리스트
            for t in self.cam_threads:
                frame = t.retrieve_frame()
                frames.append((t.cam_id, frame))

            # 3) 메인 윈도우로 시그널 전송
            self.framesReady.emit(frames)

            try:
                # 4) retrieve 완료 후 GrabThread가 다음 grab()으로 넘어갈 수 있도록 신호
                self.barrier.wait()
            except threading.BrokenBarrierError:
                break

            # Optional: 너무 빠르게 도는 것을 방지하려면 약간 슬립 가능
            # time.sleep(1/60)

        # 종료 시 종료 시그널
        return


class MultiCamWindow(QMainWindow):
    def __init__(self, cam_ids=(0, 1, 2)):
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        # 1) 카메라 ID와 QLabel 매핑
        #    Multi_Camera_GUI.py에서 objectName으로 지정된 QLabel 이름과 일치해야 합니다.
        self.label_map = {
            cam_ids[0]: self.ui.CAM_1,
            cam_ids[1]: self.ui.CAM_2,
            cam_ids[2]: self.ui.CAM_3,
        }

        # 2) Barrier 및 stop_event 생성
        self.cam_ids = cam_ids
        num_cams = len(self.cam_ids)
        self.barrier = threading.Barrier(num_cams + 1)
        self.stop_event = threading.Event()

        # 3) CameraThread(GrabThread) 인스턴스 생성 및 시작
        self.cam_threads = []
        for cid in self.cam_ids:
            t = CameraThread(cam_id=cid,
                             barrier=self.barrier,
                             stop_event=self.stop_event,
                             width=640, height=480, fps=30)
            t.start()
            self.cam_threads.append(t)

        # 4) 녹화 제어용 변수 및 토글 버튼 연결
        self.recorder: Optional[MultiCamRecorder] = None
        self.ui.StartStopbutton.toggled.connect(self.toggle_recording)

        # 5) SyncThread 생성 및 시그널 연결
        self.sync_thread = SyncThread(self.cam_threads, self.barrier, self.stop_event)
        self.sync_thread.framesReady.connect(self.update_frames)
        self.sync_thread.start()

        # 6) 상태 표시줄 초기화
        self.statusBar().showMessage("Ready")

    def toggle_recording(self, checked: bool):
        """
        StartStopbutton 토글 슬롯:
          - checked == True: 녹화 시작
          - checked == False: 녹화 중지 및 저장
        """
        if checked:
            # ─── 녹화 시작 ───────────────────────────────────────────
            if self.recorder is not None:
                return  # 이미 녹화 중인 경우 무시

            # 1) UI에서 저장 경로·파일명 prefix 가져오기
            out_dir = self.ui.Directory.text() or "./videos"
            base_str = self.ui.Name.text() or "session"

            # 2) MultiCamRecorder 생성 및 백그라운드 스레드 실행
            self.recorder = MultiCamRecorder(
                cam_ids=list(self.label_map.keys()),
                output_dir=out_dir,
                base_name=base_str,
                fps=30,
                sync_window_ms=25,
                queue_size=64,
            )
            self.recorder.start()

            # 상태바에 녹화 중 표시
            self.statusBar().showMessage("● REC")
        else:
            # ─── 녹화 중지 및 저장 ───────────────────────────────────
            if self.recorder is None:
                return

            # 1) 녹화 스레드 종료 → 파일 저장 완료
            self.recorder.stop()
            self.recorder = None

            # 2) 상태바에 저장 완료 표시
            self.statusBar().showMessage("Recording saved ✓")

    def update_frames(self, frames):
        """
        SyncThread에서 emit된 (cam_id, frame) 리스트를 받아서 QLabel에 렌더링하고,
        녹화 중이면 MultiCamRecorder에 enqueue 합니다.
        """
        timestamp = time.monotonic_ns()
        for cam_id, frame in frames:
            # (1) QLabel에 표시
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            bytes_per_line = ch * w
            img = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
            lbl = self.label_map[cam_id]
            pix = QPixmap.fromImage(
                img.scaled(lbl.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            )
            lbl.setPixmap(pix)

            # (2) 녹화 중이면 enqueue
            if self.recorder:
                self.recorder.enqueue(cam_id, frame, ts_ns=timestamp)

        # OpenCV 창으로도 확인이 필요하면 아래 주석을 해제하세요:
        # for cam_id, frame in frames:
        #     cv2.imshow(f"Cam {cam_id}", frame)
        # cv2.waitKey(1)

    def closeEvent(self, event):
        """
        창 종료 시: 녹화 중이면 중지 후, GrabThread와 SyncThread를 정리합니다.
        """
        # 1) 녹화 중이면 강제로 중지
        if self.recorder:
            self.ui.StartStopbutton.setChecked(False)
            # toggle_recording(False)가 호출되어 recorder.stop()이 실행됩니다.

        # 2) SyncThread 중지
        self.stop_event.set()
        try:
            self.barrier.abort()
        except:
            pass
        self.sync_thread.wait()

        # 3) CameraThread 종료
        for t in self.cam_threads:
            t.join(timeout=0.5)

        # 4) OpenCV 창 모두 닫기
        cv2.destroyAllWindows()

        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MultiCamWindow(cam_ids=(0, 1, 2))  # 시스템 카메라 ID에 맞게 수정하세요
    win.show()
    sys.exit(app.exec_())
