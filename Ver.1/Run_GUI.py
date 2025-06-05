
# Run_GUI.py
import sys
import cv2
from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt
from typing import Optional  # ← Python 3.9용 타입 힌트

from Multi_Camera_GUI import Ui_MainWindow       # ui 클래스
from CameraThread import CameraThread            # 스레드 클래스
from MultiCamRecorder import MultiCamRecorder    # 녹화 동기화·저장 담당

class MultiCamWindow(QMainWindow):
    def __init__(self, cam_ids=(0, 1, 2)):
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        # cam_id ↔ QLabel 매핑 (Multi_Camera_GUI.py 기준)
        self.label_map = {
            cam_ids[0]: self.ui.CAM_1,
            cam_ids[1]: self.ui.CAM_2,
            cam_ids[2]: self.ui.CAM_3,
        }

        # CameraThread 인스턴스 생성 (화면 표시 전용)
        self.threads = []
        for cid in cam_ids:
            th = CameraThread(cam_id=cid, width=1920, height=1080, fps=30)
            th.frameReady.connect(self.update_frame, Qt.QueuedConnection)
            th.start()
            self.threads.append(th)

        # ─── 녹화 제어용 변수 선언 + 토글 버튼 연결 ─────────────────
        # Python 3.9에서는 'MultiCamRecorder | None'이 불가능하므로 Optional 사용
        self.recorder: Optional[MultiCamRecorder] = None
        self.ui.StartStopbutton.toggled.connect(self.toggle_recording)

    def toggle_recording(self, checked: bool):
        """
        StartStopbutton.toggled 신호 슬롯:
         - checked == True  → 녹화 시작
         - checked == False → 녹화 중지 및 파일 저장
        """
        if checked:
            # ─── 녹화 시작 ───────────────────────────────────────────
            if self.recorder is not None:
                return  # 이미 녹화 중이면 무시

            # 1) UI에서 저장 경로·파일명 prefix 가져오기
            out_dir  = self.ui.Directory.text() or "./videos"
            base_str = self.ui.Name.text()      or "session"

            # 2) MultiCamRecorder 생성 및 백그라운드 스레드 실행
            self.recorder = MultiCamRecorder(
                cam_ids=list(self.label_map.keys()),
                output_dir=out_dir,
                base_name=base_str,
                fps=30,
                sync_window_ms=25,
                queue_size=64
            )
            self.recorder.start()

            # 3) 각 CameraThread에 recorder 전달 → run()에서 enqueue() 호출
            for th in self.threads:
                th.set_recorder(self.recorder)

            # 4) 상태바에 녹화 중 표시
            self.statusBar().showMessage("● REC")
        else:
            # ─── 녹화 중지 및 저장 ───────────────────────────────────
            if self.recorder is None:
                return  # 녹화 중이 아닐 때는 무시

            # 1) CameraThread에서 recorder 제거 → enqueue 중단
            for th in self.threads:
                th.set_recorder(None)

            # 2) 녹화 스레드 종료 → 파일 flush & VideoWriter.release()
            self.recorder.stop()
            self.recorder = None

            # 3) 상태바에 저장 완료 표시
            self.statusBar().showMessage("Recording saved ✓")

    def update_frame(self, cam_id, frame):
        """시그널 슬롯: 수신된 프레임을 해당 QLabel에 렌더링"""
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        img = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        lbl = self.label_map[cam_id]
        pix = QPixmap.fromImage(
            img.scaled(lbl.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        lbl.setPixmap(pix)

    def closeEvent(self, event):
        """창 종료 시, 녹화 중이면 중지 후 모든 스레드 정리"""
        if self.recorder:
            # 버튼을 언체크하면 toggle_recording(False)가 호출되어 녹화가 정상 종료됨
            self.ui.StartStopbutton.setChecked(False)
        for th in self.threads:
            th.stop()
        super().closeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MultiCamWindow(cam_ids=(0, 1, 2))  # 시스템에 맞게 카메라 ID 수정
    win.show()
    sys.exit(app.exec_())
