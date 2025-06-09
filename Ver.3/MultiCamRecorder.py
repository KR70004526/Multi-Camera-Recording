# MultiCamRecorder.py
"""
MultiCamRecorder
────────────────
1. 각 카메라별 Queue(작은 버퍼)에 들어온 (ts_ns, frame)을 수집
2. 중앙 동기화 스레드가 '동기창(sync_window_ns)' 이내에 들어온
   모든 카메라 프레임을 한 묶음(batch)으로 간주
3. 동기화된 프레임을 카메라별 VideoWriter에 기록
4. 지연(cam lag) 발생 시 drop 정책으로 복구
"""
from __future__ import annotations
import cv2, threading, queue, time, os, datetime
from pathlib import Path
from typing import Dict, Tuple, List, Optional

class MultiCamRecorder(threading.Thread):
    def __init__(
        self,
        cam_ids: List[int],
        output_dir: str = "./videos",
        base_name: str = "recording",
        fps: int = 30,
        sync_window_ms: int = 2,
        queue_size: int = 8,
    ):
        super().__init__(daemon=True)                # 데몬 스레드
        self.cam_ids = cam_ids
        self.fps = fps
        self.sync_window_ns = sync_window_ms * 1_000_000
        self.queues: Dict[int, queue.Queue] = {
            cid: queue.Queue(maxsize=queue_size) for cid in cam_ids
        }
        self.writers: Dict[int, cv2.VideoWriter] = {}
        self.running = threading.Event()
        self.running.set()

        # 출력 경로 준비
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.base_path = Path(output_dir) / f"{base_name}_{ts}"
        self.base_path.parent.mkdir(parents=True, exist_ok=True)

        # 내부 버퍼(큐 peek 결과 임시 보관)
        self._peek_buf: Dict[int, Optional[Tuple[int, any]]] = {
            cid: None for cid in cam_ids
        }

    # ───────────────────────── Public API ─────────────────────────
    def enqueue(self, cam_id: int, frame, ts_ns: Optional[int] = None):
        """캡처 스레드에서 호출: 프레임을 버퍼에 넣는다."""
        if not self.running.is_set() or cam_id not in self.queues:
            return
        if ts_ns is None:
            ts_ns = time.monotonic_ns()
        q = self.queues[cam_id]
        try:
            q.put_nowait((ts_ns, frame.copy()))
        except queue.Full:
            # 가장 오래된 항목 drop 후 새 frame 삽입
            _ = q.get_nowait()
            q.put_nowait((ts_ns, frame.copy()))

    def stop(self):
        """GUI 종료 시 등에서 호출: 녹화 스레드를 깨끗이 종료."""
        self.running.clear()
        self.join()

    # ─────────────────────── Thread main loop ─────────────────────

    def run(self):
        while True:
            # running이 해제됐으면, 즉시 종료
            if not self.running.is_set():
                break
            # 기존: 모든 카메라 큐에 패킷이 준비되었는지 검사
            for cid, q in self.queues.items():
                if self._peek_buf[cid] is None and not q.empty():
                    self._peek_buf[cid] = q.queue[0]
            if None in self._peek_buf.values():
                time.sleep(0.0005)
                continue
            # 동기화된 프레임 처리 (기존 로직 유지)
            ts_list = [ts for ts, _ in self._peek_buf.values()]
            t_min, t_max = min(ts_list), max(ts_list)
            if t_max - t_min <= self.sync_window_ns:
                batch = {}
                for cid, q in self.queues.items():
                    ts, frame = q.get()
                    self._peek_buf[cid] = None
                    batch[cid] = frame
                self._write_batch(batch)
            else:
                fastest_cid = ts_list.index(t_min)
                cid_to_drop = self.cam_ids[fastest_cid]
                _ = self.queues[cid_to_drop].get()
                self._peek_buf[cid_to_drop] = None

        # 루프 종료 → 남은 writer들도 release
        for w in self.writers.values():
            w.release()

    # ───────────────────────── 내부 함수 ─────────────────────────
    def _writer_for(self, cid: int, frame_shape) -> cv2.VideoWriter:
        if cid in self.writers:
            return self.writers[cid]
        h, w, _ = frame_shape
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        fname = f"{self.base_path}_cam{cid}.mp4"
        vw = cv2.VideoWriter(str(fname), fourcc, self.fps, (w, h))
        self.writers[cid] = vw
        return vw

    def _write_batch(self, batch: Dict[int, any]):
        """동기화된 프레임 dict{cid: frame} → 파일로 저장"""
        for cid, frame in batch.items():
            vw = self._writer_for(cid, frame.shape)
            vw.write(frame)
