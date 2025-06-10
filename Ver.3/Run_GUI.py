# Run_GUI.py
# "grab → barrier → retrieve" 기반 동기화로 수정된 메인 GUI 실행 파일

import sys
import cv2
import threading
import time
import datetime
from pathlib import Path
from PyQt5.QtWidgets import QApplication, QMainWindow, QFileDialog, QSizePolicy
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt, QThread, pyqtSignal

from Multi_Webcam_GUI import Ui_MainWindow       # 수정된 GUI 파일명
from CameraThread import CameraThread            # Grab/Barrier 기반 CameraThread
from MultiCamRecorder import MultiCamRecorder    # 녹화 동기화·저장 담당


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
        while not self.stop_event.is_set():
            # 1) 모든 GrabThread가 grab() 완료 대기
            try:
                self.barrier.wait()
            except threading.BrokenBarrierError:
                break

            # 2) retrieve 단계: 각 카메라별 frame 수집
            frames = [(t.cam_id, t.retrieve_frame()) for t in self.cam_threads]

            # 3) 메인 윈도우로 시그널 전송
            self.framesReady.emit(frames)

            # 4) 다음 grab()으로 넘어가도록 해제
            try:
                self.barrier.wait()
            except threading.BrokenBarrierError:
                break

        return


class MultiCamWindow(QMainWindow):
    def __init__(self, cam_ids=(0, 1, 2)):
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        default_dir = Path("C:/Users/USER/Desktop/Video").resolve()
        self.ui.Directory.setText(str(default_dir))

        # 1) DirectoryButton → 폴더 선택 다이얼로그
        self.ui.DirectoryButton.clicked.connect(self.select_directory)

        # 2) RecordingButton 토글 신호 연결
        self.ui.RecordingButton.toggled.connect(self.toggle_recording)

        # 3) cam_id ↔ QLabel 동적 매핑 (CAM_0, CAM_1, …)
        self.cam_ids = cam_ids
        self.label_map = {}
        for cid in cam_ids:
            lbl = getattr(self.ui, f"CAM_{cid}", None)
            if lbl is not None:
                self.label_map[cid] = lbl
        
        for lbl in self.label_map.values():
            lbl.setScaledContents(True)
            lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        # 4) Barrier 및 stop_event 생성
        num_cams = len(self.cam_ids)
        self.barrier = threading.Barrier(num_cams + 1)
        self.stop_event = threading.Event()

        # 5) CameraThread 인스턴스 생성 및 시작
        self.cam_threads = []
        for cid in self.cam_ids:
            t = CameraThread(
                cam_id=cid,
                barrier=self.barrier,
                stop_event=self.stop_event,
                width=1920,
                height=1080,
                fps=30
            )
            t.start()
            self.cam_threads.append(t)

        # 6) SyncThread 생성 및 시그널 연결
        self.recorder = None
        self.sync_thread = SyncThread(self.cam_threads, self.barrier, self.stop_event)
        self.sync_thread.framesReady.connect(self.update_frames)
        self.sync_thread.start()

        self.log_label = getattr(self.ui, "LogLabel", None)
        if self.log_label:
            self.log_label.setWordWrap(True)
            self.log_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            self.log_label.setText("")
            self.append_log("Ready")

        self.rec_start_time = None

    def append_log(self, msg: str):
        """LogLabel에 [날짜-시간] 메시지 형태로 누적 출력합니다."""
        if not self.log_label:
            return
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{now_str}] {msg}"
        prev = self.log_label.text()
        new_text = f"{prev}\n{entry}" if prev else entry
        self.log_label.setText(new_text)

    def select_directory(self):
        """Load Directory 버튼 클릭 시 호출"""
        init_dir = self.ui.Directory.text() or str(Path.home())
        dir_path = QFileDialog.getExistingDirectory(self, "Select Recording Directory", init_dir)
        if dir_path:
            self.ui.Directory.setText(dir_path)
            self.append_log(f"Directory selected: {dir_path}")


    def toggle_recording(self, checked: bool):
        """
        RecordingButton 토글:
          - checked == True: 녹화 시작
          - checked == False: 녹화 중지
        """
        if checked:
            # 이미 녹화 중이면 무시
            if self.recorder:
                return

            self.rec_start_time = datetime.datetime.now()
            out_dir = self.ui.Directory.text() or "./videos"
            base_str = self.ui.Name.text() or "session"

            # 녹화 스레드 실행
            self.recorder = MultiCamRecorder(
                cam_ids=list(self.label_map.keys()),
                output_dir=out_dir,
                base_name=base_str,
                fps=30,
                sync_window_ms=25,
                queue_size=64,
            )
            self.recorder.start()
            self.ui.RecordingButton.setText("Stop Recording")
            self.append_log("Recording started")
        else:
            if not self.recorder:
                return

            # 녹화 종료 및 파일 저장
            self.recorder.stop()
            self.recorder = None
            self.ui.RecordingButton.setText("Start Recording")
            self.append_log("Recording stopped")
            # 녹화 종료 시점과 시작 시점 차이 계산해서 로그로 출력
            if self.rec_start_time:
                delta = datetime.datetime.now() - self.rec_start_time
                secs = int(delta.total_seconds())
                h = secs // 3600
                m = (secs % 3600) // 60
                s = secs % 60
                dur_str = f"{h:02}:{m:02}:{s:02}"
                self.append_log(f"Recording duration: {dur_str}")

    def update_frames(self, frames):
        """
        SyncThread에서 전달된 (cam_id, frame) 리스트를 QLabel에 렌더링하고,
        녹화 중이면 MultiCamRecorder에 enqueue 합니다.
        """
        timestamp = time.monotonic_ns()
        for cam_id, frame in frames:
            if cam_id not in self.label_map:
                continue
            # (1) QLabel에 표시
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            bytes_per_line = ch * w
            img = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
            lbl = self.label_map[cam_id]
            pix = QPixmap.fromImage(img)
            lbl.setPixmap(pix)

            # (2) 녹화 중이면 enqueue
            if self.recorder:
                self.recorder.enqueue(cam_id, frame, ts_ns=timestamp)


    def closeEvent(self, event):
        """
        창 종료 시: 녹화 중지 → 스레드 종료 → 리소스 해제
        """
        if self.recorder and self.ui.RecordingButton.isChecked():
            self.ui.RecordingButton.setChecked(False)

        # SyncThread 중지
        self.stop_event.set()
        try:
            self.barrier.abort()
        except:
            pass
        self.sync_thread.wait()

        # CameraThread 종료
        for t in self.cam_threads:
            t.join(timeout=0.5)

        cv2.destroyAllWindows()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    # 카메라 ID 리스트를 실제 환경에 맞춰 수정하세요
    win = MultiCamWindow(cam_ids=(0, 1, 2))
    win.show()
    sys.exit(app.exec_())
